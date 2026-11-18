#!/usr/bin/env python3
"""Reconcile a full OO forest campaign with its frozen MiniAOD dataset and staged forests."""

import argparse
from collections import Counter
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory

from campaign import DATASET, digest_file


def require_same(description, actual, expected):
    actual, expected = set(actual), set(expected)
    if actual != expected:
        missing, extra = sorted(expected - actual), sorted(actual - expected)
        raise ValueError(f"{description}: missing {len(missing)} {missing[:3]}, unexpected {len(extra)} {extra[:3]}")


def production_shards(plan_path, area, tag):
    plan = json.loads(plan_path.read_text())
    if plan.get("version") != 1 or plan.get("dataset") != DATASET:
        raise ValueError("Unrecognized plan version or dataset")
    expected = set(plan["file_events"])
    if len(expected) != plan["nfiles"] or not expected or plan["nevents"] < 1:
        raise ValueError("Incomplete dataset plan")
    prefix = f"OOsube_{tag}_full"
    plan_hash = digest_file(plan_path)
    seen = set()
    shards = []
    for receipt in sorted((area / ".oo_submissions").glob(prefix + "*.json")):
        if not re.fullmatch(re.escape(prefix) + r"\d{3,}", receipt.stem):
            continue
        record = json.loads(receipt.read_text())
        spec = record["inputs"]["spec"]
        if not record.get("submitted") or record["inputs"]["plan_sha256"] != plan_hash or spec["name"] != receipt.stem:
            raise ValueError(f"{receipt}: missing submission confirmation, task-name mismatch or wrong plan")
        files = spec.get("files") or [f for block in spec["blocks"] for f in plan["blocks"][block]]
        inputs = set(files)
        if seen & inputs or len(inputs) != len(files) or record["eligible_files"] != len(inputs):
            raise ValueError(f"{receipt}: overlapping inputs or incorrect file count")
        if not inputs <= expected or spec["splitting"] not in ("FileBased", "LumiBased"):
            raise ValueError(f"{receipt}: unexpected input LFN or splitting")
        seen.update(inputs)
        counts = [plan["file_events"][f] for f in inputs]
        events = sum(counts) if all(x is not None for x in counts) else None
        if record["eligible_events"] != events:
            raise ValueError(f"{receipt}: expected event count differs from the plan")
        shards.append((area / ("crab_" + spec["name"]), spec["splitting"], inputs, events))
    require_same("Full-task receipts against plan (check for missing shards)", seen, expected)
    return plan, shards


def staged_outputs(command, project, jobs):
    expected = {"HiForest_" + job + ".root" for job in jobs}
    forests = {}
    # CRABClient caps the request at 500 IDs. Some CRABServer versions return all
    # of the task's output metadata even when asked for a subset of jobs.
    for start in range(0, len(jobs), 200):
        batch = [j for j in jobs[start:start + 200] if "HiForest_" + j + ".root" not in forests]
        if not batch:
            continue
        result = command("getoutput", dir=str(project), dump=True, jobids=",".join(batch))
        lfns = result.get("lfn", [])
        names = [Path(lfn).name for lfn in lfns]
        if len(set(names)) != len(names) or any(not lfn.startswith("/store/") for lfn in lfns):
            raise ValueError(f"{project.name}: invalid or duplicate staged forest metadata")
        requested = {"HiForest_" + job + ".root" for job in batch}
        if not requested.issubset(names) or not set(names).issubset(expected):
            raise ValueError(f"{project.name}: staged forests do not match finished job IDs")
        for name, lfn in zip(names, lfns):
            if name in forests and forests[name] != lfn:
                raise ValueError(f"{project.name}: multiple staged LFNs for {name}")
            forests[name] = lfn
    return set(forests.values())


def tree_entries(lfn):
    import ROOT

    with ROOT.TFile.Open("root://cms-xrd-global.cern.ch/" + lfn) as root_file:
        if root_file.IsZombie() or root_file.TestBit(ROOT.TFile.kRecovered):
            raise ValueError("Incomplete staged ROOT file: " + lfn)
        tree = root_file.Get("hiEvtAnalyzer/HiTree")
        if not tree or tree.GetEntries() < 1:
            raise ValueError("Missing or empty event tree: " + lfn)
        return int(tree.GetEntries())


def check_campaign(plan, shards, command, count_outputs):
    staged = set()
    read_events = output_events = missing_counts = 0
    for project, splitting, expected, expected_events in shards:
        status = command("status", dir=str(project))
        states = Counter(s for s, _ in status["jobList"])
        if set(states) != {"finished"}:
            raise ValueError(f"{project.name}: waiting for all jobs to finish with stage-out; {dict(states)}")
        jobs = [str(j) for _, j in status["jobList"]]
        if len(set(jobs)) != len(jobs):
            raise ValueError(f"{project.name}: duplicate CRAB job IDs")

        # A fresh report directory avoids reusing files left by an earlier status.
        with TemporaryDirectory() as output:
            report = command("report", dir=str(project), recovery="notFinished", outputdir=output)
            if report["notProcessedLumis"]:
                raise ValueError(f"{project.name}: unprocessed lumis: {report['notProcessedLumis']}")
            inputs = json.loads((Path(output) / "processedFiles.json").read_text())
        require_same(f"{project.name}: completed input assignments", inputs, jobs)
        files = [f for job_files in inputs.values() for f in job_files]
        require_same(f"{project.name}: processed MiniAOD LFNs", files, expected)
        if splitting == "FileBased" and len(files) != len(expected):
            raise ValueError(f"{project.name}: repeated MiniAOD files in FileBased jobs")
        forests = staged_outputs(command, project, jobs)
        if staged & forests:
            raise ValueError(f"{project.name}: duplicate staged forest LFN across full tasks")
        staged.update(forests)
        raw = str(report["numEventsRead"])
        if raw.isdecimal():
            read_events += int(raw)
            if expected_events is not None and int(raw) != expected_events:
                raise ValueError(f"{project.name}: CRAB reports {raw} input events, expected {expected_events}")
        else:
            missing_counts += 1
        message = f"{project.name}: {len(files)} file assignments, {len(expected)} unique inputs, {len(forests)} staged forests, CRAB input events {raw}"
        if count_outputs:
            entries = sum(tree_entries(lfn) for lfn in sorted(forests))
            if expected_events is not None and entries != expected_events:
                raise ValueError(f"{project.name}: staged forests contain {entries} events, expected {expected_events}")
            output_events += entries
            message += f", {entries:,} staged tree entries"
        print(message, flush=True)
    total = plan["nevents"]
    print(f"Full campaign: {len(shards)} tasks, {plan['nfiles']:,} distinct MiniAOD files, {len(staged):,} staged forests, "
          f"{total:,} planned events", flush=True)
    if not missing_counts and read_events != total:
        raise ValueError(f"CRAB input events sum to {read_events:,}; plan has {total:,}")
    if missing_counts:
        print(f"CRAB event counts unavailable for {missing_counts} task(s); counted {read_events:,} input events in the others.")
        if not count_outputs:
            raise ValueError("File coverage and stage-out verified. Use --count-output-events to verify the full event total.")
    else:
        print(f"CRAB input events: {read_events:,}")
    if count_outputs:
        if output_events != total:
            raise ValueError(f"Staged forests contain {output_events:,} events; plan has {total:,}")
        print(f"Verified staged forest entries: {output_events:,}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True, help="directory with campaign plan.json")
    parser.add_argument("--work-area", type=Path, default=Path("crab_oo_sube"))
    parser.add_argument("--tag", required=True)
    parser.add_argument("--count-output-events", action="store_true", help="open each staged forest and count HiTree entries")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_]{1,55}", args.tag):
        parser.error("Invalid campaign tag")
    from CRABAPI.RawCommand import crabCommand

    plan, shards = production_shards(args.plan / "plan.json", args.work_area, args.tag)
    check_campaign(plan, shards, crabCommand, args.count_output_events)


if __name__ == "__main__":
    try:
        main()
    except (KeyError, OSError, ValueError) as error:
        raise SystemExit(f"Coverage check: {error}") from error
