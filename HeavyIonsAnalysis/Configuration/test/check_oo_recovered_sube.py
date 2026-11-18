#!/usr/bin/env python3
"""Validate a recovered OO EEC forest against the original MiniAOD and HIJING GEN-SIM."""
import argparse
from collections import Counter
from ctypes import c_float
from functools import lru_cache
from itertools import combinations
import json
import math


def source_locations(index):
    # Read only file identities from the index. Recompute the particle payload from GEN-SIM.
    locations = {}
    with open(index) as src:
        if src.readline().strip() != "OOHIJING_V1":
            raise ValueError("Unexpected HIJING index format")
        for line in src:
            if line.startswith("E "):
                _, run, lumi, event, _, name = line.split()
                key = int(run), int(lumi), int(event)
                if key in locations:
                    raise ValueError(f"Duplicate background {key} in the index")
                locations[key] = name
    return locations


def event_key(identifier):
    return int(identifier.run()), int(identifier.luminosityBlock()), int(identifier.event())


def open_edm(name):
    from DataFormats.FWLite import Events

    url = "root://cms-xrd-global.cern.ch/" + name if name.startswith("/store/") else name
    events = Events(url, forceEvent=True)
    events.size()  # Construct the single-file FWLite reader before a lookup by ID.
    return events


def get_product(event, tag, type_name):
    from DataFormats.FWLite import Handle

    handle = Handle(type_name)
    if not event.getByLabel(tag, handle):
        raise RuntimeError(f"Missing {tag} ({type_name})")
    return handle.product()


def selected_row(particle, sube):
    if particle.charge() and particle.pt() >= 0.5 and abs(particle.eta()) <= 2.5:
        return (c_float(particle.pt()).value, c_float(particle.eta()).value,
                c_float(particle.phi()).value, int(particle.charge()), sube)
    return None


def signal_rows(mini):
    refs = get_product(mini, "packedGenParticlesSignal", "pat::PackedGenParticleRefVector")
    rows = []
    for i in range(refs.size()):
        ref = refs.at(i)
        if ref.isNull() or not ref.isAvailable():
            raise ValueError("Unresolved MiniAOD signal particle")
        row = selected_row(ref.get(), 0)
        if row is not None:
            rows.append(row)
    return rows


def background_rows(mini, locations, read_background, root):
    summary = get_product(mini, "slimmedAddPileupInfo", "std::vector<PileupSummaryInfo>")
    ids = [event_key(i) for bx in summary if bx.getBunchCrossing() == 0 for i in bx.getPU_EventID()]
    if len(ids) != 1 or ids[0] not in locations:
        raise ValueError(f"Expected one indexed in-time HIJING event, found {ids}")
    key = ids[0]
    background = read_background(locations[key])
    if not background.object().to(*key) or event_key(background.eventAuxiliary().id()) != key:
        raise ValueError(f"Missing source event {key} in {locations[key]}")
    particles = get_product(background, ("genParticles", "", "SIM"), "std::vector<reco::GenParticle>")
    null_mother = root.reco.GenParticleRef()
    rows = []
    for particle in particles:
        if particle.status() == 1 and particle.charge() and abs(particle.y()) < 6:
            packed = root.pat.PackedGenParticle(particle, null_mother)
            row = selected_row(packed, 1)
            if row is not None:
                rows.append(row)
    return rows


def forest_rows(tree):
    branches = [tree.pt, tree.eta, tree.phi, tree.chg, tree.sube]
    if len({len(col) for col in branches}) != 1:
        raise ValueError("Unequal particle vector lengths in HiGenParticleAna/hi")
    rows = list(zip(*branches))
    if any(row[-1] not in (0, 1) for row in rows):
        raise ValueError("Found a missing or nonbinary sube label")
    return rows


def dr(a_eta, a_phi, b_eta, b_phi):
    return math.hypot(a_eta - b_eta, math.remainder(a_phi - b_phi, 2 * math.pi))


def eec(rows, jets):
    # Test all 16 pair-separation bins on the same forest AK4 axes for both particle lists.
    counts = [[0] * 16 for _ in range(3)]
    sums = [[0.] * 16 for _ in range(3)]
    ordered = sorted(rows)
    for jet_pt, eta, phi in jets:
        inside = [p for p in ordered if dr(p[1], p[2], eta, phi) < 0.4]
        for a, b in combinations(inside, 2):
            category = a[-1] + b[-1]
            bin_id = min(15, int(dr(a[1], a[2], b[1], b[2]) / 0.05))
            counts[category][bin_id] += 1
            sums[category][bin_id] += a[0] * b[0] / (jet_pt * jet_pt)
    return counts, sums


def validate(args):
    import ROOT
    locations = source_locations(args.index)
    mini = open_edm(args.mini)
    if not ROOT.gInterpreter.Declare('#include "DataFormats/PatCandidates/interface/PackedGenParticle.h"'):
        raise RuntimeError("Could not load the packed generator-particle header")
    backgrounds = lru_cache(maxsize=4)(open_edm)
    n_events = min(mini.size(), args.events)
    with ROOT.TFile.Open(args.forest) as output:
        events = output.Get("hiEvtAnalyzer/HiTree")
        particles = output.Get("HiGenParticleAna/hi")
        axes = output.Get("akCs4PFJetAnalyzer/t")
        if not n_events or any(not t or t.GetEntries() != n_events for t in (events, particles, axes)):
            raise ValueError(f"Expected {n_events} aligned, nonempty entries in each output tree")

        totals = Counter()
        pairs = [0, 0, 0]
        weighted = [[0.] * 16 for _ in range(3)]
        for i in range(n_events):
            if not mini.to(i) or any(tree.GetEntry(i) <= 0 for tree in (events, particles, axes)):
                raise ValueError(f"Unreadable entry {i}")
            key = event_key(mini.eventAuxiliary().id())
            if key != (int(events.run), int(events.lumi), int(events.evt)):
                raise ValueError(f"Forest and MiniAOD disagree on event identity at entry {i}")
            info = get_product(mini, "generator", "GenEventInfoProduct")
            if (not math.isfinite(events.pthat) or not math.isfinite(events.weight) or not info.binningValues().size()
                    or c_float(info.binningValues()[0]).value != events.pthat
                    or c_float(info.weight()).value != events.weight):
                raise ValueError(f"Generator scale or event weight differs for {key}")
            signal = signal_rows(mini)
            background = background_rows(mini, locations, backgrounds, ROOT)
            expected = signal + background
            observed = forest_rows(particles)
            missing = Counter(expected) - Counter(observed)
            extra = Counter(observed) - Counter(expected)
            if missing or extra:
                raise ValueError(f"{key} differs in stored pt/eta/phi/charge/source: "
                                 f"{sum(missing.values())} missing and {sum(extra.values())} unexpected rows")

            jets = [(axes.genpt[j], axes.geneta[j], axes.genphi[j]) for j in range(axes.ngen)
                    if axes.genpt[j] >= 80 and abs(axes.geneta[j]) <= 2.1]
            counts, sums = eec(observed, jets)
            if (counts, sums) != eec(expected, jets):
                raise ValueError(f"Source-separated EEC differs for {key}")
            totals.update(signal=len(signal), background=len(background), jets=len(jets))
            for k in range(3):
                pairs[k] += sum(counts[k])
                for b, value in enumerate(sums[k]):
                    weighted[k][b] += events.weight * value
        if totals["background"] == 0:
            raise ValueError("No selected HIJING particles in the pilot; use more events to test recovery")
    print(json.dumps(dict(events=n_events, **totals, pair_categories=["SS", "SB", "BB"],
                          pair_counts=pairs, weighted_eec_bins=weighted)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mini", required=True)
    parser.add_argument("--forest", required=True)
    parser.add_argument("--index", required=True)
    parser.add_argument("--events", type=int, required=True, help="same positive limit used by cmsRun")
    args = parser.parse_args()
    if args.events < 1:
        parser.error("--events must be positive")
    validate(args)
