# EEC forest output for OO and ppRef

The four production configurations in this directory now apply
`configureEECSlimForest` from `python/eecSlimContent_cff.py`. This reduces
TFileService output during foresting, without requiring a subsequent ROOT skim.
`PoolOutputModule.outputCommands` cannot prune these TFileService trees.

Both data and MC retain R=0.4 and R=0.8 jets with `jetPtMin = 15` GeV and
`jetAbsEtaMax = 2.5`. WTA reclustering and the information needed for Run-3
PF Jet ID, event veto, matching, and ppRef FastJet inputs are retained.

## Retained particle and jet branches

| Tree | Branches |
| --- | --- |
| `PbPbTracks/trackTree` (OO) or `ppTracks/trackTree` (ppRef) | `nTrk`, `trkPt`, `trkEta`, `trkPhi`, `trkPtError`, `highPurity`, `trkDzFirstVtx`, `trkDzErrFirstVtx`, `trkDxyFirstVtx`, `trkDxyErrFirstVtx`, `trkCharge`, `trkNormChi2`, `trkNLayers`, `trkNHits`, `pfEcal`, `pfHcal` |
| `akCs{4,8}PFJetAnalyzer/t` (OO) or `ak{4,8}PFJetAnalyzer/t` (ppRef) | `run`, `evt`, `lumi`, `nref`, `rawpt`, `jtpt`, `jteta`, `jtphi`, `WTAeta`, `WTAphi`, `jtarea`, `trackMax`, `jtPfCHF`, `jtPfNHF`, `jtPfCEF`, `jtPfNEF`, `jtPfMUF`, `jtPfCHM`, `jtPfNHM`, `jtPfCEM`, `jtPfNEM`, `jtPfMUM` |
| Additional jet branches in MC | `pthat`, `refpt`, `refeta`, `refphi`, `refparton_flavorForB`, `ngen`, `genpt`, `geneta`, `genphi`, `WTAgeneta`, `WTAgenphi` |
| `HiGenParticleAna/hi` in MC | `mult`, `pt`, `eta`, `phi`, `chg`, `sube` |

Reconstructed tracks are retained with pT >= 0.5 GeV and |eta| <= 2.6 over
the full event. `applyTrackSelections=False` preserves tracks failing quality
cuts for downstream selections and response/fake studies. The five legacy
chi2/hit/calo inputs remain because the ordinary EEC reader still opens them;
`trkCharge` is needed by its response reader.

Generator particles retain stable charged particles with pT >= 0.5 GeV.
Their existing `etaMax=2.5` is preserved. `sube` filling and its missing-product
sentinel are unchanged. Generator-jet reconstruction uses its existing input
collection, independently of the filtered generator-particle output.

`trackMax` is the current reader input; it must not be replaced by
`chargedMax`. `doHiJetID=True` remains necessary to fill it. The ten native
`jtPf*` composition branches are independent inputs for the current PF
Jet ID/veto code. `jtarea` remains the measured PAT jet area.

## Other output

B tagging is disabled both when setting up the jet production tasks and in
the analyzers. Calo jets, constituents, subjets, generator substructure and
unused per-jet observables are omitted. Unused OO rho-profile and random-cone
analyzers are removed from the forest path. Generator ancestry and unused
track vertex/PF details are omitted from the saved trees.

The event, HLT, skim and HiForest metadata trees retain their existing
content. In particular, `hiEvtAnalyzer/HiTree` retains canonical event IDs,
vertex/activity inputs, and MC `pthat`/`weight`. Trigger/filter names and
decisions are preserved. pp centrality stays disabled.

Both ppRef configurations still save `ggHiNtuplizer/EventTree` with
`run`, `event`, `lumis`, and scalar `rho` from `fixedGridRhoFastjetAll`.
This is distinct from the removed OO rho profile. The EEC analysis can use
this rho together with `jtarea` for its optional ppRef R4 L1FastJet mode.
Producing R8 jets does not extend the analysis's current R4 calibration support.

## Analyzer option and validation

`TrackAnalyzer`, `HiInclusiveJetAnalyzer` and `HiGenAnalyzer` accept an
optional untracked `branchesToKeep` string vector. Nonempty lists filter
branch creation; every requested name must be booked or initialization
throws a configuration error. Native types and array count leaves are
preserved. Lists for array branches must include their count branches.
Omitting the parameter or supplying an empty list preserves the analyzer's
full original branch booking. It does not undo separately configured
particle retention cuts or producer flags.

These changes include C++, so rebuild in the CMSSW area before testing:

```bash
scram b -j 4
cmsRun HeavyIonsAnalysis/Configuration/forest_miniAOD_run3_MC.py
```

Before production, run a small sample for each of the four configurations.
Check retained branches, native ROOT types, synchronized event entries,
nonempty track/truth vectors, and finite pp rho/positive jet areas. Compare
file bytes per processed event against the previous configuration using the
same input events. No numerical storage reduction is claimed without this
measurement. Offline checks do not replace a CMSSW build or event processing.
