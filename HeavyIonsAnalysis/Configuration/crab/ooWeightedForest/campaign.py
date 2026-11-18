#!/usr/bin/env python3
"""Freeze the central weighted OO inputs and submit separate CRAB pilots or full forest tasks."""

import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile

DATASET = ("/QCD-dijet_pThat15-event-weighted_TuneCP5_5p36TeV_pythia8/"
           "HINOOSpring25MiniAOD-CustomTrack_150X_mcRun3_2025_forOO_realistic_v9-v2/MINIAODSIM")
MAX_FILES = 8000


def das(query, key):
    proc = subprocess.run(["dasgoclient", "-query", query, "-json", "-limit", "0"],
                          check=True, capture_output=True, text=True, timeout=600)
    return [v for row in json.loads(proc.stdout) for v in row[key]]


def block_files(block):
    return block, [row["name"] for row in das("file block=" + block, "file")]


def membership(blocks, files):
    by_block = defaultdict(list)
    if all(f.get("block_name") for f in files.values()):
        for name, f in files.items():
            by_block[f["block_name"]].append(name)
    else:
        with ThreadPoolExecutor(max_workers=4) as pool:
            by_block.update(pool.map(block_files, blocks))
    if set(by_block) != set(blocks):
        raise ValueError("DBS block lists disagree")
    listed = [name for batch in by_block.values() for name in batch]
    if len(listed) != len(files) or set(listed) != set(files):
        raise ValueError("Incomplete or overlapping LFNs in DBS block membership")
    for block, names in by_block.items():
        if not names:
            raise ValueError("Empty primary DBS block " + block)
        by_block[block] = sorted(names)
    return dict(sorted(by_block.items()))


def make_plan(n_pilot):
    summary, = das("summary dataset=" + DATASET, "summary")
    rows = das("file dataset=" + DATASET, "file")
    files = {f["name"]: f for f in rows}
    nfiles, nevents = int(summary["nfiles"]), int(summary["nevents"])
    if (nfiles < 1 or nevents < 1 or len(rows) != len(files) or len(files) != nfiles
            or any(not re.fullmatch(r"/store/[^\s]+\.root", name) for name in files)):
        raise ValueError("Invalid or truncated dataset file listing")
    blocks = sorted({b["name"] for b in das("block dataset=" + DATASET, "block")})
    if (not blocks or any(not b.startswith(DATASET + "#") for b in blocks)
            or ("nblocks" in summary and len(blocks) != int(summary["nblocks"]))):
        raise ValueError("Invalid or truncated dataset block listing")
    by_block = membership(blocks, files)
    events = {name: int(f["nevents"]) if f.get("nevents") is not None else None for name, f in files.items()}
    if all(n is not None for n in events.values()) and sum(events.values()) != nevents:
        raise ValueError("File-level event counts disagree with the DBS summary")
    # File-size/event-count quantiles include the largest file; ties use deterministic LFN order.
    ordered = sorted(files, key=lambda f: (events[f] or int(files[f].get("size_bytes") or files[f].get("size") or 0), f))
    n = min(n_pilot, len(ordered))
    chosen = [ordered[i * (len(ordered) - 1) // max(1, n - 1)] for i in range(n)]
    return dict(version=1, dataset=DATASET, nfiles=nfiles, nevents=nevents, blocks=by_block,
                file_events=events, pilot=chosen)


def file_groups(plan):
    """Bound tasks even when sites and block boundaries prevent grouping files together."""
    group, count = [], 0
    for block, files in plan["blocks"].items():
        if group and count + len(files) > MAX_FILES:
            yield {"blocks": group}
            group, count = [], 0
        if len(files) > MAX_FILES:
            for start in range(0, len(files), MAX_FILES):
                yield {"files": files[start:start + MAX_FILES]}
        else:
            group.append(block)
            count += len(files)
    if group:
        yield {"blocks": group}


def file_run_lumis(name):
    runs = {int(r["run_number"]) for r in das("run file=" + name, "run")}
    if not runs:
        raise ValueError("Missing run information for " + name)
    for run in sorted(runs):
        query = "lumi file=" + name
        if len(runs) > 1:
            query += f" run={run}"
        lumis = das(query, "lumi")
        if not lumis:
            raise ValueError("Missing lumisections for " + name)
        for row in lumis:
            values = row["number"]
            for lumi in values if isinstance(values, list) else [values]:
                yield run, int(lumi)


def pilot_lumi_mask(plan, plan_path, plan_hash, count):
    """Use the same explicit run/lumis across changes to the lumi splitting parameters."""
    path = plan_path.parent / f"pilot_lumis_{plan_hash[:12]}_{count}.json"
    if not path.exists():
        with ThreadPoolExecutor(max_workers=4) as pool:
            selections = pool.map(lambda name: list(file_run_lumis(name)), plan["pilot"])
            candidates = sorted({key for selection in selections for key in selection})
        if not candidates:
            raise ValueError("No pilot lumisections found in DBS")
        length = min(count, len(candidates))
        chosen = [candidates[i * (len(candidates) - 1) // max(1, length - 1)] for i in range(length)]
        mask = defaultdict(list)
        for run, lumi in chosen:
            if run < 1 or lumi < 1:
                raise ValueError("Invalid DBS run or lumi")
            mask[str(run)].append([lumi, lumi])
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as tmp:
            json.dump(mask, tmp, sort_keys=True)
        Path(tmp.name).replace(path)
    return path


def task_specs(plan, args):
    base = "OOsube_" + args.tag
    if args.mode in ("pilot1", "pilot2"):
        cores = int(args.mode[-1])
        yield dict(name=base + "_" + args.mode, files=plan["pilot"], splitting="FileBased",
                   units=1, cores=cores, memory=2500 if cores == 1 else 4000, logs=True)
    elif args.mode in ("pilotLumi", "pilotSized"):
        if args.mode == "pilotLumi" or args.splitting == "LumiBased":
            names = set(plan["pilot"])
            group = {"blocks": [b for b, files in plan["blocks"].items() if not names.isdisjoint(files)]}
            splitting = "LumiBased"
        else:
            group = {"files": plan["pilot"]}
            splitting = "FileBased"
        yield dict(name=base + "_" + args.mode, **group, splitting=splitting, units=args.units,
                   cores=args.cores, memory=args.memory, logs=True)
    else:
        if args.splitting == "FileBased":
            groups = file_groups(plan)
        else:
            # Refuse an oversized block only if the CRAB splitter later reports that it exceeds its cap.
            groups = ({"blocks": [block]} for block in plan["blocks"])
        for index, group in enumerate(groups, 1):
            yield dict(name=f"{base}_full{index:03d}", **group, splitting=args.splitting,
                       units=args.units, cores=args.cores, memory=args.memory, logs=False)


def digest_file(path):
    h = hashlib.sha256()
    with path.open("rb") as source:
        while data := source.read(1024 * 1024):
            h.update(data)
    return h.hexdigest()


def submit(plan, args, plan_path):
    from crabConfig import make_config

    catalog = args.catalog.resolve()
    catalog_hash = digest_file(catalog)
    plan_hash = digest_file(plan_path)
    specs = list(task_specs(plan, args))
    # A work area and tag identify one set of full shards across invocations and splitting modes.
    print(f"{len(specs)} {args.mode} task(s), primary dataset has {plan['nfiles']} files and {plan['nevents']:,} events")
    if not args.preview:
        from CRABAPI.RawCommand import crabCommand
        from fcntl import flock, LOCK_EX
        receipt_dir = Path(args.work_area).resolve() / ".oo_submissions"
        receipt_dir.mkdir(parents=True, exist_ok=True)
    for spec in specs:
        files = spec.get("files") or [f for block in spec["blocks"] for f in plan["blocks"][block]]
        counts = [plan["file_events"][f] for f in files]
        events = sum(counts) if all(n is not None for n in counts) else None
        print(f"{spec['name']}: {len(files)} eligible files, {events} eligible events, "
              f"{spec['splitting']} {spec['units']} unit(s)/job, {spec['cores']} core(s), {spec['memory']} MB")
        config = make_config(**spec, catalog=str(catalog), storage=args.storage, outlfn=args.outlfn,
                             work_area=args.work_area)
        mask_hash = None
        if args.mode in ("pilotLumi", "pilotSized") and spec["splitting"] == "LumiBased":
            mask = pilot_lumi_mask(plan, plan_path, plan_hash, args.pilot_lumis or 8)
            config.Data.lumiMask = str(mask.resolve())
            mask_hash = digest_file(mask)
            n_lumis = sum(len(ranges) for ranges in json.loads(mask.read_text()).values())
            print(f"Pilot reads {n_lumis} selected run/lumi pairs across all eligible files in the pilot blocks. Mask: {mask}")
        if args.preview:
            continue

        fingerprint = dict(spec=spec, plan_sha256=plan_hash, catalog_sha256=catalog_hash,
                           storage=args.storage, outlfn=args.outlfn)
        if mask_hash:
            fingerprint["lumi_mask_sha256"] = mask_hash
        path = receipt_dir / (spec["name"] + ".json")
        with (receipt_dir / (spec["name"] + ".lock")).open("a") as lock:
            flock(lock, LOCK_EX)
            if path.exists():
                record = json.loads(path.read_text())
                if record["inputs"] != fingerprint:
                    raise ValueError(f"Different settings already recorded for {spec['name']}; use a new tag")
                if record.get("submitted"):
                    print("Already submitted", spec["name"])
                    continue
                raise ValueError(f"Earlier submission outcome unknown. Inspect CRAB task {spec['name']} and {path} before retrying.")
            taskdir = Path(args.work_area) / ("crab_" + spec["name"])
            if taskdir.exists():
                raise FileExistsError(f"Existing CRAB project {taskdir} without a receipt. Check it before submitting.")
            record = {"inputs": fingerprint, "eligible_files": len(files), "eligible_events": events, "submitted": False}
            path.write_text(json.dumps(record))
            answer = crabCommand("submit", config=config)
            if not isinstance(answer, dict) or answer.get("commandStatus") != "SUCCESS":
                raise RuntimeError(f"Could not confirm submission: {answer}")
            record.update(submitted=True, remote_task=answer.get("uniquerequestname"))
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(record))
            tmp.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    plan_parser = actions.add_parser("plan")
    plan_parser.add_argument("--plan", type=Path, required=True, help="new plan directory")
    plan_parser.add_argument("--pilot-files", type=int, default=12)
    submit_parser = actions.add_parser("submit")
    submit_parser.add_argument("--plan", type=Path, required=True)
    submit_parser.add_argument("--mode", choices=["pilot1", "pilot2", "pilotLumi", "pilotSized", "full"], required=True)
    submit_parser.add_argument("--tag", required=True)
    submit_parser.add_argument("--catalog", type=Path, required=True)
    submit_parser.add_argument("--splitting", choices=["FileBased", "LumiBased"], default="FileBased")
    submit_parser.add_argument("--cores", type=int, choices=[1, 2], default=1)
    submit_parser.add_argument("--memory", type=int, default=2500)
    submit_parser.add_argument("--units", type=int, default=1, help="files or lumisections per sized pilot or full job")
    submit_parser.add_argument("--pilot-lumis", type=int,
                               help="maximum shared run/lumi pairs for a LumiBased pilot (default: 8)")
    submit_parser.add_argument("--work-area", default="crab_oo_sube")
    submit_parser.add_argument("--storage", default="T2_CH_CERN")
    submit_parser.add_argument("--outlfn", default="/store/group/phys_top/gkrintir/HiForest/OO2025/MC")
    submit_parser.add_argument("--preview", action="store_true", help="print task settings without submitting")
    args = parser.parse_args()
    path = args.plan / "plan.json"
    if args.action == "plan":
        if args.pilot_files < 1:
            parser.error("--pilot-files must be positive")
        if path.exists():
            parser.error(f"Plan {path} already exists")
        plan = make_plan(args.pilot_files)
        args.plan.mkdir(parents=True, exist_ok=True)
        with path.open("x") as out:
            json.dump(plan, out, sort_keys=True)
        print(f"Wrote {path}: {plan['nfiles']} files, {len(plan['blocks'])} blocks, "
              f"{plan['nevents']:,} events, {len(plan['pilot'])} matched pilot files.")
        return
    if not re.fullmatch(r"[A-Za-z0-9_]{1,55}", args.tag):
        parser.error("Use an alphanumeric/underscore tag")
    plan = json.loads(path.read_text())
    if plan.get("dataset") != DATASET or plan.get("version") != 1:
        parser.error("Unexpected input dataset or plan version")
    if args.units < 1 or args.memory < 1:
        parser.error("--units and --memory must be positive")
    if args.mode not in ("full", "pilotSized") and (args.cores != 1 or args.memory != 2500 or args.units != 1
                                                   or args.splitting != "FileBased"):
        parser.error("Resource and splitting overrides require --mode pilotSized or full")
    if args.pilot_lumis is not None and (args.pilot_lumis < 1 or not (args.mode == "pilotLumi" or
                                            (args.mode == "pilotSized" and args.splitting == "LumiBased"))):
        parser.error("--pilot-lumis takes a positive count for a LumiBased pilot")
    submit(plan, args, path)


if __name__ == "__main__":
    main()
