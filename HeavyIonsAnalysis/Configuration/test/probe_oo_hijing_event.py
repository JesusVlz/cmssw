#!/usr/bin/env python3
"""Inspect generator products at one indexed HIJING event used by the OO forest."""

import argparse
import json
from pathlib import Path
import sys


def event_id(value):
    try:
        fields = tuple(int(n) for n in value.split(":"))
    except ValueError:
        fields = ()
    if len(fields) != 3 or min(fields) < 1:
        raise argparse.ArgumentTypeError("Expected positive run:lumi:event")
    return fields


GEN = ("genParticles", "", "SIM")
SMEARED = ("generatorSmeared", "", "SIM")
RAW = ("generator", "unsmeared", "SIM")


def inspect(events, handle_type):
    tree = events.object().getTFile().Get("Events")
    if not tree:
        raise ValueError("Missing Events tree")
    branches = {}
    for branch in tree.GetListOfBranches():
        parts = branch.GetName().rstrip(".").split("_")
        if len(parts) != 4:
            continue
        kind, module, instance, process = parts
        if (kind == "recoGenParticles" and module == "genParticles") or (
                kind == "edmHepMCProduct" and module in ("generator", "generatorSmeared")):
            branches[(module, instance, process)] = branch.GetName()

    products = {}
    for label in sorted(set(branches) | {GEN, SMEARED, RAW}):
        gen = label[0] == "genParticles"
        handle = handle_type("std::vector<reco::GenParticle>" if gen else "edm::HepMCProduct")
        record = {"branch": branches.get(label)}
        products[":".join(label)] = record
        if not events.getByLabel(label, handle):
            record["status"] = "unavailable"
            # FWLite reports both missing branches and wrappers absent at this event.
            try:
                handle.product()
            except RuntimeError as error:
                record["fwlite_error"] = str(error)
            continue
        if gen:
            record.update(status="present", particles=int(handle.product().size()))
        else:
            event = handle.product().GetEvent()
            if event:
                record.update(status="present", particles=int(event.particles_size()))
            else:
                record["status"] = "null_gen_event"
    return products


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--event", required=True, type=event_id, metavar="RUN:LUMI:EVENT")
    args = parser.parse_args()

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "crab" / "ooWeightedForest"))
    from hijing_catalog import locations
    from build_oo_hijing_truth import entry_id, open_events, packed_input_rows
    from DataFormats.FWLite import Handle

    by_file = locations(args.catalog, {args.event})
    (lfn, [(entry, _)]), = by_file.items()
    with open_events(lfn) as events:
        actual = entry_id(events, entry, lfn)
        if actual != args.event:
            raise ValueError(f"{lfn} entry {entry} contains {actual}, requested {args.event}")
        products = inspect(events, Handle)
        result = dict(event=actual, lfn=lfn, entry=entry, products=products)
        try:
            rows = packed_input_rows(events, Handle("std::vector<reco::GenParticle>"), actual, lfn, entry)
        except Exception as error:
            result.update(worker_accepts=False, worker_error=f"{type(error).__name__}: {error}")
        else:
            result.update(worker_accepts=True, worker_rows=len(rows))
    smeared = products[":".join(SMEARED)].get("particles")
    raw = products[":".join(RAW)].get("particles")
    if smeared is not None and raw is not None and raw != smeared:
        result["observation"] = "generator:unsmeared:SIM and generatorSmeared::SIM have different particle counts"
    print(json.dumps(result, sort_keys=True))
    return 0 if result["worker_accepts"] else 1


if __name__ == "__main__":
    sys.exit(main())
