#!/usr/bin/env python3
"""Size FileBased and LumiBased forest jobs from completed CRAB pilots with staged worker logs."""

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import statistics
import tarfile

MARKER = b"OO_FOREST_PROFILE "


def read_profiles(project):
    profiles = {}
    for path in sorted((project / "results").rglob("cmsRun*.tar.gz")):
        with tarfile.open(path, "r:*") as archive:
            for member in archive:
                if not member.isfile() or not Path(member.name).name.startswith("cmsRun-stdout"):
                    continue
                with archive.extractfile(member) as stream:
                    for line in stream:
                        if not line.startswith(MARKER):
                            continue
                        profile = json.loads(line[len(MARKER):])
                        if profile["exit_code"] != 0:
                            continue
                        job = str(profile["job_id"])
                        if job in profiles and profile != profiles[job]:
                            raise ValueError(f"Conflicting successful measurements for {project} job {job}")
                        profiles[job] = profile
    return profiles


def positive(value):
    return isinstance(value, (int, float)) and math.isfinite(value) and value > 0


def report(project, jobs, dataset_files, target_hours, dataset_events=None, unit="file", dataset_lumis=None):
    states = Counter(ad["State"] for ad in jobs.values())
    print(f"\n{project}: {dict(states)}")
    if not jobs or set(states) != {"finished"}:
        raise ValueError("Sizing requires a completed pilot including successful CRAB stage-out.")
    profiles = read_profiles(project)
    if set(profiles) != set(jobs):
        missing = sorted(set(jobs) - set(profiles))
        raise ValueError(f"Mismatch between successful profile logs and CRAB jobs. Missing: {missing}. "
                         f"Fetch the pilot logs with crab getlog -d {project} --quantity=all.")
    cores = {p["requested_cpus"] for p in profiles.values()}
    if len(cores) != 1 or not positive(next(iter(cores))):
        raise ValueError(f"Invalid CPU requests within one task: {cores}")
    cores = cores.pop()
    signatures = Counter()
    wall_per_unit, rss = [], []
    unit_field = {"file": "input_files", "lumi": "input_lumis"}[unit]
    dataset_units = dataset_files if unit == "file" else dataset_lumis
    have_job_rss = True
    events = files = prep = cms = cpu = elapsed = retry_seconds = 0
    sites = Counter()
    complete_wall_history = True
    for job, p in profiles.items():
        for field in ("input_files", unit_field, "input_events", "wall_seconds", "peak_rss_mib"):
            if not positive(p.get(field)):
                raise ValueError(f"Job {job} has missing or invalid {field}")
        if p["input_events"] != p["output_events"]:
            raise ValueError(f"Job {job} has different input and forest event counts")
        signatures[(p["selection_sha256"], p["output_events"])] += 1
        ad = jobs[job]
        sites[(ad.get("SiteHistory") or ["unknown"])[-1]] += 1
        # CRAB's status cache gives one WallDurations value (seconds) per attempt.
        history = ad.get("WallDurations") or []
        if not history or not all(positive(t) for t in history):
            complete_wall_history = False
            last_wall = p["wall_seconds"]
            elapsed += last_wall
        else:
            last_wall = max(history[-1], p["wall_seconds"])
            retry_seconds += sum(history[:-1])
            elapsed += sum(history[:-1]) + last_wall
        wall_per_unit.append(last_wall / p[unit_field])
        rss_kib = (ad.get("ResidentSetSize") or [0])[-1]
        if not positive(rss_kib):
            have_job_rss = False
        rss.append(max(p["peak_rss_mib"], rss_kib / 1024 if positive(rss_kib) else 0))
        events += p["output_events"]
        files += p["input_files"]
        cpu += p["user_cpu_seconds"] + p["sys_cpu_seconds"]
        prep += p["prepare_wall_seconds"]
        cms += p["cmsrun_wall_seconds"]
    if not complete_wall_history:
        print("Some scheduler timings are missing; worker wall times exclude stage-out and retries for those jobs.")
    sorted_times = sorted(wall_per_unit)
    tail = sorted_times[math.ceil(.95 * len(sorted_times)) - 1]
    allocated = elapsed * cores / 3600
    print(f"{len(jobs)} jobs, {cores} requested core(s)/job, {files} input files, {events:,} forest events")
    print(f"Finishing sites: {dict(sites)}; prior attempt wall time recorded: {retry_seconds/3600:.2f} h")
    print(f"Wall minutes/{unit}, median {statistics.median(wall_per_unit)/60:.1f}, empirical p95 {tail/60:.1f}")
    if len(jobs) < 20:
        print("Fewer than 20 jobs: the empirical p95 is the observed maximum.")
    print(f"Successful payload time: HIJING prep {prep/3600:.2f} h; cmsRun {cms/3600:.2f} h")
    print(f"Highest observed RSS {max(rss):.0f} MiB")
    if cores == 1 or have_job_rss:
        print(f"Memory to retest with 25% headroom, rounded to 100 MiB: {math.ceil(max(rss)*1.25/100)*100} MiB")
    else:
        print("Some multicore jobs lack scheduler RSS. Check total job memory during parallel HIJING preparation.")
    print(f"Useful job CPU / requested core time: {100*cpu/(elapsed*cores):.1f}%")
    print(f"Delivered events/requested core-hour: {events/allocated:.1f}; "
          f"requested core-hours per million delivered events: {1e6*allocated/events:.1f}")
    if dataset_events:
        print(f"Estimated requested core-hours for {dataset_events:,} events: {dataset_events*allocated/events:,.0f}")
        if all(positive(p.get("output_bytes")) for p in profiles.values()):
            bytes_per_event = sum(p["output_bytes"] for p in profiles.values()) / events
            print(f"Forest size at this grouping: {bytes_per_event/1024:.1f} KiB/event, "
                  f"{dataset_events*bytes_per_event/1e12:.2f} TB projected")
    units = max(1, math.floor(target_hours * 3600 / tail))
    if tail > target_hours * 3600:
        print(f"Even one {unit} at the measured p95 exceeds the {target_hours:g} h target.")
    print(f"Candidate {unit}s/job for a {target_hours:g} h target: {units}. Retest at that grouping before production.")
    if dataset_units:
        print(f"Estimated production jobs at {units} {unit}s/job: {math.ceil(dataset_units/units):,} "
              f"for {dataset_units:,} {unit}s (before site/locality constraints).")
    digests = [p.get("event_multiset_sum256") for p in profiles.values()]
    if all(d is not None for d in digests):
        if any(len(d) != 64 for d in digests):
            raise ValueError("Malformed event multiset digest in worker profile")
        # Same run/lumi/event multiset across different CRAB job groupings.
        signatures = (events, sum(int(d, 16) for d in digests) % (1 << 256))
    return signatures, allocated / events


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("projects", nargs="+", type=Path, help="completed CRAB project(s), with full logs under results/")
    parser.add_argument("--dataset-files", type=int, help="total number of primary dataset files from DBS")
    parser.add_argument("--unit", choices=("file", "lumi"), default="file",
                        help="use file for FileBased pilots or lumi for LumiBased pilots")
    parser.add_argument("--dataset-lumis", type=int, help="total unique primary run/lumi pairs, for --unit lumi")
    parser.add_argument("--dataset-events", type=int, help="total number of primary events from DBS")
    parser.add_argument("--target-hours", type=float, default=6, help="planning target for a job, default 6")
    args = parser.parse_args()
    counts = (args.dataset_files, args.dataset_lumis, args.dataset_events)
    if not positive(args.target_hours) or any(x is not None and x < 1 for x in counts):
        parser.error("Job target and dataset counts must be positive.")
    from CRABAPI.RawCommand import crabCommand

    results = []
    for project in args.projects:
        status = crabCommand("status", dir=str(project))
        results.append(report(project, status.get("jobs", {}), args.dataset_files, args.target_hours,
                              args.dataset_events, args.unit, args.dataset_lumis))
    if len(results) > 1:
        if all(selected == results[0][0] for selected, _ in results):
            best = min(range(len(results)), key=lambda i: results[i][1])
            print(f"\nLowest measured requested core-hours/event on the same selections: {args.projects[best]}")
        else:
            print("\nEvent selections differ across pilots. Rerun on matched inputs for a resource comparison.")


if __name__ == "__main__":
    try:
        main()
    except (KeyError, ValueError, OSError, tarfile.TarError) as error:
        raise SystemExit(f"Pilot sizing: {error}") from error
