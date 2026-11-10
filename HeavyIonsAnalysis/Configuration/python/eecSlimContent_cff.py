"""EEC output content for the OO and ppRef production configurations.

These allowlists are evaluated when TFileService branches are booked.
An analyzer with no branchesToKeep parameter retains its original output.
"""

import FWCore.ParameterSet.Config as cms


TRACK_BRANCHES = (
    "nTrk", "trkPt", "trkEta", "trkPhi", "trkPtError", "highPurity",
    "trkDzFirstVtx", "trkDzErrFirstVtx", "trkDxyFirstVtx", "trkDxyErrFirstVtx",
    "trkCharge",
    # The ordinary EEC reader still opens these legacy quality inputs.
    "trkNormChi2", "trkNLayers", "trkNHits", "pfEcal", "pfHcal",
)

JET_BRANCHES = (
    "run", "evt", "lumi", "nref", "rawpt", "jtpt", "jteta", "jtphi",
    "WTAeta", "WTAphi", "jtarea", "trackMax",
    # Current Run-3 PF Jet ID and whole-event veto inputs.
    "jtPfCHF", "jtPfNHF", "jtPfCEF", "jtPfNEF", "jtPfMUF",
    "jtPfCHM", "jtPfNHM", "jtPfCEM", "jtPfNEM", "jtPfMUM",
)

MC_JET_BRANCHES = (
    "pthat", "refpt", "refeta", "refphi", "refparton_flavorForB",
    "ngen", "genpt", "geneta", "genphi", "WTAgeneta", "WTAgenphi",
)

GEN_PARTICLE_BRANCHES = ("mult", "pt", "eta", "phi", "chg", "sube")


def configureEECSlimForest(process, isMC, isOO):
    """Configure output content after all R4/R8 analyzers have been created."""
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
            *(JET_BRANCHES + (MC_JET_BRANCHES if isMC else ()))
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
