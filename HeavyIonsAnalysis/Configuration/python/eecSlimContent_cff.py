"""EEC output content for the OO and ppRef production configurations.

These allowlists are evaluated when TFileService branches are booked.
An analyzer with no branchesToKeep parameter retains its original output.
"""

import FWCore.ParameterSet.Config as cms


TRACK_BRANCHES = (
    "nTrk", "trkPt", "trkEta", "trkPhi", "trkPtError", "highPurity",
    "trkDzFirstVtx", "trkDzErrFirstVtx", "trkDxyFirstVtx", "trkDxyErrFirstVtx",
    "trkCharge",
)

JET_BRANCHES = (
    "nref", "rawpt", "jtpt", "jteta", "jtphi",
    "WTAeta", "WTAphi", "jtarea", "trackMax",
    # Current Run-3 PF Jet ID and whole-event veto inputs.
    "jtPfCHF", "jtPfNHF", "jtPfCEF", "jtPfNEF", "jtPfMUF",
    "jtPfCHM", "jtPfNHM", "jtPfCEM", "jtPfNEM", "jtPfMUM",
)

PP_JET_MULTIPLICITY_BRANCHES = ("jtPfCM", "jtPfNM")

MC_JET_BRANCHES = (
    "refpt", "refeta", "refphi", "refparton_flavorForB",
    "ngen", "genpt", "geneta", "genphi", "WTAgeneta", "WTAgenphi",
)

GEN_PARTICLE_BRANCHES = ("pt", "eta", "phi", "chg", "sube")

EVENT_BRANCHES = ("run", "lumi", "evt", "vz", "hiHFPlus_pf", "hiHFMinus_pf")


def configureEECSlimForest(process, isMC, isOO):
    """Configure output content after all R4/R8 analyzers have been created."""
    event_branches = EVENT_BRANCHES + (("hiBin", "hiHF_pf") if isOO else ())
    if isMC:
        event_branches += ("pthat", "weight")
    process.hiEvtAnalyzer.branchesToKeep = cms.untracked.vstring(*event_branches)
    # PF-HF inputs are read even for ppRef; this does not enable pp centrality.
    process.hiEvtAnalyzer.doPFHF = cms.untracked.bool(True)
    filters = ("pprimaryVertexFilter",)
    if isOO:
        filters += (("OOphfCoincFilterPF2Th4" if isMC else "OOpfCoincFilterPF2Th4"), "pileupVertexFilter")
    process.skimanalysis.branchesToKeep = cms.untracked.vstring(*filters)
    if isMC:
        # Current MC cards have TriggerSelection 0.
        process.forest.remove(process.hltanalysis)
    else:
        trigger = "HLT_MinimumBiasHF_OR_BptxAND_v1" if isOO else "HLT_PPRefZeroBias_v6"
        process.hltanalysis.branchesToKeep = cms.untracked.vstring(trigger)

    tracks = getattr(process, "PbPbTracks" if isOO else "ppTracks")
    tracks.trackPtMin = cms.untracked.double(0.5)
    tracks.trackEtaMax = cms.untracked.double(2.6)
    # Keep low-quality tracks for downstream cut variations and fake studies.
    tracks.applyTrackSelections = cms.untracked.bool(False)
    tracks.branchesToKeep = cms.untracked.vstring(*TRACK_BRANCHES)

    for radius in ("4", "8"):
        label = ("akCs" if isOO else "ak") + radius + "PFJetAnalyzer"
        jets = getattr(process, label)
        jets.branchesToKeep = cms.untracked.vstring(
            *(JET_BRANCHES + (() if isOO else PP_JET_MULTIPLICITY_BRANCHES)
              + (MC_JET_BRANCHES if isMC else ()))
        )
        jets.doWTARecluster = cms.untracked.bool(True)
        jets.doHiJetID = cms.untracked.bool(True)  # Required to fill trackMax.
        jets.doBtagging = cms.untracked.bool(False)
        jets.doCaloJets = cms.untracked.bool(False)
        jets.doStandardJetID = cms.untracked.bool(False)
        jets.doSubJets = cms.untracked.bool(False)
        jets.doJetConstituents = cms.untracked.bool(False)
        jets.doGenSubJets = cms.untracked.bool(False)
        jets.doGenTaus = cms.untracked.bool(False)
        jets.doGenSym = cms.untracked.bool(False)
        if isMC:
            jets.fillGenJets = cms.untracked.bool(True)

    if isMC:
        particles = process.HiGenParticleAna
        particles.ptMin = cms.untracked.double(0.5)
        particles.chargedOnly = cms.untracked.bool(True)
        particles.stableOnly = cms.untracked.bool(True)
        particles.branchesToKeep = cms.untracked.vstring(*GEN_PARTICLE_BRANCHES)
