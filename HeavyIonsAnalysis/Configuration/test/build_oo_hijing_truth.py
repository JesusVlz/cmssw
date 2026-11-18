#!/usr/bin/env python3
"""Collect the exact HIJING generator particles overlaid on the first N events of an OO MiniAODSIM."""

import argparse
from collections import defaultdict
from contextlib import contextmanager
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

HIJING_DATASET = "/MinBias_OO_5p36TeV_hijing/HINOOSpring25GS-150X_mcRun3_2025_forOO_realistic_v8-v2/GEN-SIM"


def event_key(event_id):
    return (int(event_id.run()), int(event_id.luminosityBlock()), int(event_id.event()))


@contextmanager
def open_events(name):
    import ROOT
    from DataFormats.FWLite import Events

    url = "root://cms-xrd-global.cern.ch/" + name if name.startswith("/store/") else name
    source = ROOT.TFile.Open(url)
    if not source or source.IsZombie():
        raise OSError(f"Could not open {name}")
    try:
        yield Events(source)
    finally:
        source.Close()


def entry_id(events, entry, name):
    if not events.to(entry):
        raise OSError(f"Could not read entry {entry} of {name}")
    return event_key(events.eventAuxiliary().id())


def bx0_keys(mini, max_events):
    from DataFormats.FWLite import Handle

    wanted = set()
    with open_events(mini) as events:
        count = min(events.size(), max_events)
        if count == 0:
            raise ValueError(f"Empty MiniAOD {mini}")
        pileup = Handle("std::vector<PileupSummaryInfo>")
        for entry in range(count):
            signal = entry_id(events, entry, mini)
            if not events.getByLabel("slimmedAddPileupInfo", pileup):
                raise ValueError(f"Missing slimmedAddPileupInfo in signal event {signal}")
            in_time = [bx for bx in pileup.product() if bx.getBunchCrossing() == 0]
            if len(in_time) != 1 or in_time[0].getPU_NumInteractions() != 1 or len(in_time[0].getPU_EventID()) != 1:
                raise ValueError(f"Expected one in-time HIJING interaction and one EventID for signal {signal}")
            wanted.add(event_key(in_time[0].getPU_EventID()[0]))
    return count, wanted


def candidate_files(wanted):
    by_lumi = defaultdict(set)
    for key in wanted:
        by_lumi[key[:2]].add(key)
    by_file = defaultdict(set)
    for (run, lumi), keys in sorted(by_lumi.items()):
        query = f"file dataset={HIJING_DATASET} run={run} lumi={lumi}"
        result = subprocess.run(["dasgoclient", "-query", query, "-limit", "0"], capture_output=True,
                                text=True, timeout=180)
        if result.returncode:
            raise RuntimeError(f"DAS failed for '{query}': {result.stderr.strip()}")
        paths = {line.strip() for line in result.stdout.splitlines() if line.strip()}
        if not paths or any(not re.fullmatch(r"/store/[^\s]+\.root", path) for path in paths):
            raise ValueError(f"Missing or invalid DAS file list for '{query}'")
        for lfn in paths:
            by_file[lfn].update(keys)
    return by_file


def packed_input_rows(events, handle, key):
    # Use the original GEN-SIM collection and the two cuts in PATPackedGenParticleProducer.
    if not events.getByLabel(("genParticles", "", "SIM"), handle) or handle.product().empty():
        raise ValueError(f"Missing or empty genParticles::SIM for HIJING event {key}")
    rows = []
    for particle in handle.product():
        if particle.status() != 1 or not abs(particle.y()) < 6:
            continue
        kinematics = (particle.pt(), particle.eta(), particle.phi(), particle.mass())
        if not all(math.isfinite(x) for x in kinematics):
            raise ValueError(f"Nonfinite particle kinematics for HIJING event {key}")
        rows.append((*kinematics, int(particle.charge()), int(particle.pdgId())))
    return rows


def write_events(output, wanted, files, handle):
    found = {}
    output.write("OOHIJING_V1\n")
    for lfn, keys in sorted(files.items()):
        with open_events(lfn) as events:
            # Inspect the entire candidate file so duplicate EventIDs within a file are detected too.
            for entry in range(events.size()):
                key = entry_id(events, entry, lfn)
                if key not in keys:
                    continue
                if key in found:
                    raise ValueError(f"Ambiguous HIJING event {key}: {found[key]} and {(lfn, entry)}")
                rows = packed_input_rows(events, handle, key)
                found[key] = (lfn, entry)
                print("E", *key, len(rows), lfn, file=output)
                for row in rows:
                    print("P", *row, file=output)
    missing = wanted.difference(found)
    if missing:
        raise ValueError(f"HIJING EventIDs missing from DBS candidate files: {sorted(missing)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mini", required=True, help="single MiniAODSIM LFN, XRootD URL or local path")
    parser.add_argument("--events", required=True, type=int, help="maximum number of consecutive entries from entry 0")
    parser.add_argument("--output", required=True, type=Path, help="new local HIJING truth table")
    args = parser.parse_args()
    if args.events < 1:
        parser.error("--events must be positive")
    if args.output.exists():
        parser.error(f"Output already exists: {args.output}")

    from DataFormats.FWLite import Handle

    count, wanted = bx0_keys(args.mini, args.events)
    files = candidate_files(wanted)
    print(f"Reading {len(wanted)} unique HIJING events for {count} MiniAOD entries from "
          f"{len(files)} candidate GEN-SIM files", flush=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=args.output.parent, encoding="ascii", delete=False) as tmp:
        try:
            write_events(tmp, wanted, files, Handle("std::vector<reco::GenParticle>"))
            tmp.flush()
            # Publish the complete file without replacing another invocation's output.
            os.link(tmp.name, args.output)
        finally:
            os.unlink(tmp.name)
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        sys.exit(str(exc))
