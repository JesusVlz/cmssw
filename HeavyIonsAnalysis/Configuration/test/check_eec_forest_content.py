#!/usr/bin/env python3
"""Inspect all ROOT trees/branches and check the OO/ppRef EEC input contract.

Requires uproot and numpy. This inspects existing files and never rewrites them.
"""
import argparse
import csv
import json
import sys


def expected_content(sample, l1=False):
    oo = sample.startswith('oo-')
    mc = sample.endswith('-mc')
    event = {'run', 'lumi', 'evt', 'vz', 'hiHFPlus_pf', 'hiHFMinus_pf'}
    if oo:
        event |= {'hiBin', 'hiHF_pf'}
    if mc:
        event |= {'pthat', 'weight'}
    track = {'nTrk', 'trkPt', 'trkEta', 'trkPhi', 'trkPtError', 'highPurity',
             'trkDzFirstVtx', 'trkDzErrFirstVtx', 'trkDxyFirstVtx',
             'trkDxyErrFirstVtx', 'trkCharge'}
    jets = {'nref', 'rawpt', 'jtpt', 'jteta', 'jtphi', 'WTAeta', 'WTAphi',
            'trackMax', 'jtarea', 'jtPfCHF', 'jtPfNHF', 'jtPfCEF', 'jtPfNEF',
            'jtPfMUF', 'jtPfCHM', 'jtPfNHM', 'jtPfCEM', 'jtPfNEM', 'jtPfMUM'}
    if mc:
        jets |= {'refpt', 'refeta', 'refphi', 'refparton_flavorForB', 'ngen',
                 'genpt', 'geneta', 'genphi', 'WTAgeneta', 'WTAgenphi'}
    filters = {'pprimaryVertexFilter'}
    if oo:
        filters |= {'OOphfCoincFilterPF2Th4' if mc else 'OOpfCoincFilterPF2Th4',
                    'pileupVertexFilter'}
    expected = {'hiEvtAnalyzer/HiTree': event, 'skimanalysis/HltTree': filters,
                ('PbPbTracks' if oo else 'ppTracks') + '/trackTree': track}
    for r in ('4', '8'):
        expected[('akCs' if oo else 'ak') + r + 'PFJetAnalyzer/t'] = jets
    if mc:
        expected['HiGenParticleAna/hi'] = {'pt', 'eta', 'phi', 'chg', 'sube'}
    else:
        expected['hltanalysis/HltTree'] = {
            'HLT_MinimumBiasHF_OR_BptxAND_v1' if oo else 'HLT_PPRefZeroBias_v6'}
    if not oo:
        expected['ggHiNtuplizer/EventTree'] = {'run', 'event', 'lumis', 'rho'}
    return expected


def inspect(path, sample, l1=False, strict=False, inventory=None):
    import numpy as np
    import uproot

    expected = expected_content(sample, l1)
    errors = []
    rows = []
    summary = []
    with uproot.open(path) as f:
        trees = {p: f[p] for p, cls in f.classnames(cycle=False, recursive=True).items()
                 if cls == 'TTree'}
        events = trees.get('hiEvtAnalyzer/HiTree')
        count = None if events is None else events.num_entries
        for p, names in expected.items():
            if p not in trees:
                errors.append('Missing tree: ' + p)
                continue
            t = trees[p]
            missing = names - set(t.keys())
            if missing:
                errors.append(p + ': missing ' + ', '.join(sorted(missing)))
            if t.num_entries != count:
                errors.append(p + ': event entry count differs from HiTree')
        if 'HiForestInfo/HiForest' not in trees:
            errors.append('Missing HiForestInfo/HiForest (generator reader format marker)')
        for p, t in trees.items():
            names = expected.get(p, set())
            for name, branch in t.items(recursive=False):
                keep = name in names or p == 'HiForestInfo/HiForest'
                rows.append(dict(tree=p, branch=name, typename=branch.typename,
                                 entries=t.num_entries, compressed_bytes=branch.compressed_bytes,
                                 uncompressed_bytes=branch.uncompressed_bytes,
                                 action='keep' if keep else 'omit'))
            extra = set(t.keys()) - names
            if strict and extra and p != 'HiForestInfo/HiForest':
                errors.append(p + ': extra branches ' + ', '.join(sorted(extra)))
            summary.append(dict(tree=p, branches=len(t.keys()), entries=t.num_entries,
                                compressed_bytes=t.compressed_bytes))
        if events is not None and 'evt' in events:
            if events['evt'].typename not in ('uint64_t', 'ULong64_t', 'unsigned long long'):
                errors.append('hiEvtAnalyzer/HiTree/evt must preserve unsigned 64-bit identity')
        # Validate the actual L1 payload representation and values, without a rho=0
        # or geometric-area fallback. Stored R8 area is inspected too; current EEC
        # L1FastJet calibration itself supports ppRef R4 only.
        if l1:
            rho_tree = trees.get('ggHiNtuplizer/EventTree')
            if rho_tree is not None and 'rho' in rho_tree:
                rho = rho_tree['rho']
                if rho.typename not in ('float', 'double', 'Float_t', 'Double_t'):
                    errors.append('rho must be a Float_t/Double_t scalar')
                for values in rho_tree.iterate(['rho'], step_size='25 MB', library='np'):
                    if not np.all(np.isfinite(values['rho']) & (values['rho'] >= 0)):
                        errors.append('rho has nonfinite or negative values')
                        break
            for p, t in trees.items():
                if p not in expected or not p.endswith('PFJetAnalyzer/t'):
                    continue
                if not {'nref', 'jtarea'} <= set(t.keys()):
                    continue
                area = t['jtarea']
                if area.count_branch is None or area.count_branch.name != 'nref':
                    errors.append(p + '/jtarea must be an array indexed by nref')
                if area.typename not in ('float[]', 'double[]'):
                    errors.append(p + '/jtarea must be a Float_t/Double_t array')
                if t['nref'].typename not in ('int32_t', 'Int_t', 'int'):
                    errors.append(p + '/nref must be Int_t')
                import awkward as ak
                for values in t.iterate(['nref', 'jtarea'], step_size='25 MB', library='ak'):
                    n = values['nref']
                    a = values['jtarea']
                    if not bool(ak.all((n >= 0) & (n <= 250) & (ak.num(a) == n))):
                        errors.append(p + ': invalid jet count/area length')
                        break
                    if not bool(ak.all(np.isfinite(a) & (a > 0))):
                        errors.append(p + ': nonpositive/nonfinite measured jet area')
                        break
    if inventory:
        with open(inventory, 'w', newline='') as output:
            writer = csv.DictWriter(output, fieldnames=list(rows[0]) if rows else
                                    ['tree', 'branch', 'typename', 'entries', 'compressed_bytes', 'uncompressed_bytes', 'action'])
            writer.writeheader()
            writer.writerows(rows)
    result = dict(file=path, sample=sample, events=count, trees=summary, errors=errors,
                  stored_branch_bytes=sum(r['compressed_bytes'] for r in rows),
                  existing_kept_branch_bytes=sum(r['compressed_bytes'] for r in rows if r['action'] == 'keep'),
                  note='Basket-byte sums are not a prediction of a reforested file size.')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file')
    parser.add_argument('--sample', choices=['oo-data', 'oo-mc', 'pp-data', 'pp-mc'], required=True)
    parser.add_argument('--l1', action='store_true', help='Check scalar rho and measured jet-area payloads')
    parser.add_argument('--strict', action='store_true', help='Reject unused trees or branches')
    parser.add_argument('--inventory', help='Write every tree/branch and its keep/omit classification to CSV')
    args = parser.parse_args()
    if args.l1 and args.sample.startswith('oo-'):
        parser.error('The current EEC L1FastJet option supports ppRef R4 only')
    result = inspect(args.file, args.sample, args.l1, args.strict, args.inventory)
    print(json.dumps(result, indent=2))
    return 1 if result['errors'] else 0


if __name__ == '__main__':
    sys.exit(main())
