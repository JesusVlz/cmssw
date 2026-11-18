"""Forest the central weighted OO sample; CRAB assigns each job's files and lumis."""

import os
import runpy

import FWCore.ParameterSet.Config as cms
from FWCore.ParameterSet.VarParsing import VarParsing

options = VarParsing()
options.register("cores", 1, VarParsing.multiplicity.singleton, VarParsing.varType.int, "Worker CPU reservation")
options.parseArguments()
if options.cores not in (1, 2):
    raise ValueError("Use cores=1 or cores=2 for HIJING preparation")

standard = os.path.join(os.environ["CMSSW_BASE"], "src/HeavyIonsAnalysis/Configuration/forest_miniAOD_run3_MC.py")
process = runpy.run_path(standard)["process"]
process.source.fileNames = cms.untracked.vstring()
process.maxEvents.input = cms.untracked.int32(-1)
process.TFileService.fileName = cms.string("HiForest.root")

# CRAB checks the reservation here. The worker serializes the forest after parallel HIJING preparation.
process.options.numberOfThreads = cms.untracked.uint32(options.cores)
process.options.numberOfStreams = cms.untracked.uint32(1)
process.options.numberOfConcurrentLuminosityBlocks = cms.untracked.uint32(1)
process.options.numberOfConcurrentRuns = cms.untracked.uint32(1)

process.ooRecoveredGenParticles = cms.EDProducer(
    "OOHiGenParticleRecovery",
    src=cms.InputTag("packedGenParticles"),
    signal=cms.InputTag("packedGenParticlesSignal"),
    pileup=cms.InputTag("slimmedAddPileupInfo"),
    backgroundFile=cms.string("oo_hijing_truth.txt"),
)
process.forest.insert(0, process.ooRecoveredGenParticles)
process.HiGenParticleAna.genParticleSrc = cms.InputTag("ooRecoveredGenParticles")
process.HiGenParticleAna.signalGenParticleSrc = cms.InputTag("ooRecoveredGenParticles", "signal")
process.HiForestInfo.info.append("OO generator sube: PYTHIA=0, HIJING=1 from BX0 EventID")
