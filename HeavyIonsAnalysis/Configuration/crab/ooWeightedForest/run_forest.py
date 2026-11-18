#!/usr/bin/env python3
"""Prepare the original HIJING truth for the CRAB assignment, run HiForest and check its output."""

from bisect import bisect_right
from contextlib import ExitStack, contextmanager
import hashlib
import json
import os
from pathlib import Path
import resource
import struct
import subprocess
import sys
import tempfile
import time
from xml.etree import ElementTree as ET

SIDECAR = Path("oo_hijing_truth.txt")
OUTPUT = Path("HiForest.root")
REPORT = Path("FrameworkJobReport.xml")
CATALOG = Path("oo_hijing_catalog.sqlite.gz")


def lumi_intervals(ranges):
    import FWCore.ParameterSet.Config as cms

    intervals = []
    for value in ranges:
        if isinstance(value, str):
            value = cms.LuminosityBlockRange(value)
        first = (value.start(), value.startSub())
        last = (value.end(), value.endSub() or 0xffffffff)
        intervals.append((first, last))
    merged = []
    for first, last in sorted(intervals):
        if merged and first <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(last, merged[-1][1]))
        else:
            merged.append((first, last))
    return [first for first, _ in merged], [last for _, last in merged]


def in_lumi_mask(lumi, starts, ends):
    if not starts:
        return True
    index = bisect_right(starts, lumi) - 1
    return index >= 0 and lumi <= ends[index]


def event_digest(keys):
    # Sorted digest identifies a shard. The modular sum also compares campaigns regrouped across jobs.
    acc, ordered = 0, hashlib.sha256()
    for key in sorted(keys):
        payload = struct.pack(">IIQ", *key)
        ordered.update(payload)
        acc += int.from_bytes(hashlib.sha256(payload).digest(), "big")
    return ordered.hexdigest(), f"{(acc % (1 << 256)):064x}"


@contextmanager
def primary_events(lfn):
    from build_oo_hijing_truth import open_events

    with ExitStack() as stack:
        events = None
        if lfn.startswith("/store/"):
            resolved = subprocess.run(["edmFileUtil", "-d", lfn], capture_output=True, text=True)
            pfn = resolved.stdout.strip()
            if resolved.returncode == 0 and pfn and pfn != lfn and "\n" not in pfn:
                try:
                    events = stack.enter_context(open_events(pfn))
                except OSError as error:
                    print(f"Primary site read failed for {pfn}: {error}. Trying AAA.", flush=True)
        if events is None:
            events = stack.enter_context(open_events(lfn))
        yield events


def prepare(summary):
    from PSet import process
    from DataFormats.FWLite import Handle
    from build_oo_hijing_truth import entry_id, event_key
    from hijing_catalog import write_selected

    src = process.source
    if (process.maxEvents.input.value() != -1
            or (hasattr(src, "skipEvents") and src.skipEvents.value() != 0)
            or any(len(getattr(src, key, [])) for key in ("eventsToProcess", "eventsToSkip", "lumisToSkip"))):
        raise ValueError("Expected a CRAB FileBased or LumiBased full-event assignment")
    files = list(src.fileNames)
    logical = [s[s.index("/store/"):] if "/store/" in s else s for s in files]
    if not files or len(set(logical)) != len(files):
        raise ValueError(f"Expected nonempty distinct assigned primary files: {files}")
    starts, ends = lumi_intervals(getattr(src, "lumisToProcess", []))
    cores = process.options.numberOfThreads.value()
    if cores not in (1, 2):
        raise ValueError(f"Unsupported CPU reservation: {cores}")

    selected, wanted = [], set()
    pileup = Handle("std::vector<PileupSummaryInfo>")
    for name in files:
        with primary_events(name) as events:
            for entry in range(events.size()):
                key = entry_id(events, entry, name)
                if not in_lumi_mask(key[:2], starts, ends):
                    continue
                if not events.getByLabel("slimmedAddPileupInfo", pileup):
                    raise ValueError(f"Missing slimmedAddPileupInfo in {key}")
                intime = [bx for bx in pileup.product() if bx.getBunchCrossing() == 0]
                if len(intime) != 1 or intime[0].getPU_NumInteractions() != 1 or len(intime[0].getPU_EventID()) != 1:
                    raise ValueError(f"Expected exactly one in-time HIJING EventID in {key}")
                selected.append(key)
                wanted.add(event_key(intime[0].getPU_EventID()[0]))
    if not selected:
        raise ValueError("No events matched the assigned MiniAOD files and lumis")
    digest, total = event_digest(selected)
    profile = {
        "requested_cpus": cores, "cmsrun_threads": 1, "input_files": len(files), "input_events": len(selected),
        "input_lumis": len({key[:2] for key in selected}), "selection_sha256": digest, "event_multiset_sum256": total,
        "input_set_sha256": hashlib.sha256("\n".join(sorted(logical)).encode()).hexdigest(),
        "hijing_events": len(wanted),
    }
    summary.write_text(json.dumps(profile))
    profile["hijing_files"] = write_selected(wanted, CATALOG, SIDECAR, workers=cores)
    profile["table_bytes"] = SIDECAR.stat().st_size
    summary.write_text(json.dumps(profile))


def validate(summary):
    import ROOT

    profile = json.loads(summary.read_text())
    expected = profile["input_events"]
    if ET.parse(REPORT).getroot().tag != "FrameworkJobReport":
        raise ValueError("cmsRun did not produce a readable framework report")
    with ROOT.TFile.Open(str(OUTPUT)) as forest:
        if forest.IsZombie() or forest.TestBit(ROOT.TFile.kRecovered):
            raise ValueError("Incomplete forest ROOT file")
        required = {
            "hiEvtAnalyzer/HiTree": ("run", "lumi", "evt", "weight", "pthat"),
            "HiGenParticleAna/hi": ("pt", "eta", "phi", "chg", "sube"),
            "akCs4PFJetAnalyzer/t": ("genpt",), "akCs8PFJetAnalyzer/t": ("genpt",),
        }
        for path, branches in required.items():
            tree = forest.Get(path)
            if not tree or tree.GetEntries() != expected or any(not tree.GetBranch(b) for b in branches):
                raise ValueError(f"Missing branches or expected {expected} entries in {path}")
        events = forest.Get("hiEvtAnalyzer/HiTree")
        events.SetBranchStatus("*", 0)
        for name in ("run", "lumi", "evt"):
            events.SetBranchStatus(name, 1)
        keys = [(int(row.run), int(row.lumi), int(row.evt)) for row in events]
        if event_digest(keys)[0] != profile["selection_sha256"]:
            raise ValueError("Forest EventIDs differ from the CRAB input assignment")
    profile.update(output_events=len(keys), output_bytes=OUTPUT.stat().st_size)
    summary.write_text(json.dumps(profile))


def rss_tree_kib(pid):
    # Linux CRAB workers. Count shared pages per process, conservatively, during parallel preparation.
    base = Path("/proc") / str(pid)
    try:
        status = (base / "status").read_text()
        threads = list((base / "task").iterdir())
    except (FileNotFoundError, ProcessLookupError):
        return 0
    rss = next((int(row.split()[1]) for row in status.splitlines() if row.startswith("VmRSS:")), 0)
    children = set()
    for thread in threads:
        try:
            children.update((thread / "children").read_text().split())
        except (FileNotFoundError, ProcessLookupError):
            continue
    return rss + sum(rss_tree_kib(child) for child in children)


def run_stage(argv, phase, profile):
    start = time.monotonic()
    with subprocess.Popen(argv) as child:
        while child.poll() is None:
            profile["peak_rss_mib"] = max(profile["peak_rss_mib"], rss_tree_kib(os.getpid()) / 1024)
            try:
                child.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
    profile[phase + "_wall_seconds"] = round(time.monotonic() - start, 3)
    if child.returncode:
        raise subprocess.CalledProcessError(child.returncode, argv)


def error_report(message, code):
    # Keep cmsRun's native inputs, outputs and error code. Mark prep/validation failures for CRAB as well.
    try:
        tree = ET.parse(REPORT)
    except (FileNotFoundError, ET.ParseError):
        tree = ET.ElementTree(ET.Element("FrameworkJobReport"))
    root = tree.getroot()
    if not any(element.get("ExitStatus") not in (None, "0") for element in root.findall("FrameworkError")):
        ET.SubElement(root, "FrameworkError", ExitStatus=str(code), Type="OOForestPayload").text = message
        tree.write(REPORT, encoding="utf-8", xml_declaration=True)


def run(job):
    start = time.monotonic()
    profile = {"job_id": job, "peak_rss_mib": 0, "exit_code": 0}
    with tempfile.TemporaryDirectory(prefix="oo_forest_", dir=".") as work:
        work = Path(work)
        summary = work / "stats.json"
        owned = not (SIDECAR.exists() or OUTPUT.exists())
        try:
            if not owned:
                raise FileExistsError("A previous forest or truth table exists in the job directory")
            run_stage([sys.executable, __file__, "--prepare", str(summary)], "prepare", profile)
            # Start from the complete worker-tweaked process, including CRAB's files and lumi masks.
            # The second core, when requested, is used by independent FWLite prepass readers only.
            pset = work / "serial_forest_cfg.py"
            pset.write_text("import runpy\nimport FWCore.ParameterSet.Config as cms\n"
                            "process = runpy.run_path('PSet.py')['process']\n"
                            "process.options.numberOfThreads = cms.untracked.uint32(1)\n")
            run_stage(["cmsRun", "-j", str(REPORT), str(pset)], "cmsrun", profile)
            run_stage([sys.executable, __file__, "--validate", str(summary)], "validation", profile)
        except Exception as error:
            code = error.returncode if isinstance(error, subprocess.CalledProcessError) else 1
            profile["exit_code"] = code if 0 < code < 256 else 1
            print(f"Forest job failed: {error}", file=sys.stderr, flush=True)
            error_report(str(error), profile["exit_code"])
        finally:
            if owned:
                SIDECAR.unlink(missing_ok=True)
                if profile["exit_code"]:
                    OUTPUT.unlink(missing_ok=True)
            if summary.is_file():
                profile.update(json.loads(summary.read_text()))
            self_usage = resource.getrusage(resource.RUSAGE_SELF)
            children = resource.getrusage(resource.RUSAGE_CHILDREN)
            profile.update(wall_seconds=round(time.monotonic() - start, 3),
                           user_cpu_seconds=round(self_usage.ru_utime + children.ru_utime, 3),
                           sys_cpu_seconds=round(self_usage.ru_stime + children.ru_stime, 3),
                           peak_rss_mib=round(max(profile["peak_rss_mib"], children.ru_maxrss / 1024), 1))
            print("OO_FOREST_PROFILE " + json.dumps(profile, separators=(",", ":")), flush=True)
    return profile["exit_code"]


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--prepare":
        prepare(Path(sys.argv[2]))
    elif len(sys.argv) == 3 and sys.argv[1] == "--validate":
        validate(Path(sys.argv[2]))
    elif len(sys.argv) == 2:
        sys.exit(run(sys.argv[1]))
    else:
        sys.exit("CRAB invocation: run_forest.sh JOB_ID")
