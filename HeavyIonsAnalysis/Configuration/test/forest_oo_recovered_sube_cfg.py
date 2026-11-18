"""Forest the central weighted OO MiniAOD with the original HIJING generator particles."""

import os
import runpy

import FWCore.ParameterSet.Config as cms
from FWCore.ParameterSet.VarParsing import VarParsing

options = VarParsing()
options.register("mini", "", VarParsing.multiplicity.singleton, VarParsing.varType.string, "MiniAODSIM input file")
options.register("backgroundFile", "", VarParsing.multiplicity.singleton, VarParsing.varType.string, "HIJING truth table")
options.register("forestOutput", "HiForest.root", VarParsing.multiplicity.singleton, VarParsing.varType.string, "Output ROOT file")
options.register("events", 10, VarParsing.multiplicity.singleton, VarParsing.varType.int, "Maximum events from the start of the file")
options.parseArguments()
if not options.mini or not options.backgroundFile or options.events < 1:
    raise ValueError("Supply mini=FILE, backgroundFile=TABLE and a positive events=N")

standard = os.path.join(os.environ["CMSSW_BASE"], "src/HeavyIonsAnalysis/Configuration/forest_miniAOD_run3_MC.py")
process = runpy.run_path(standard)["process"]
source = options.mini
if source.startswith("/store/"):
    source = "root://cms-xrd-global.cern.ch/" + source
elif ":" not in source:
    source = "file:" + os.path.abspath(source)
process.source.fileNames = cms.untracked.vstring(source)
process.maxEvents.input = cms.untracked.int32(options.events)
process.TFileService.fileName = cms.string(options.forestOutput)

process.ooRecoveredGenParticles = cms.EDProducer(
    "OOHiGenParticleRecovery",
    src=cms.InputTag("packedGenParticles"),
    signal=cms.InputTag("packedGenParticlesSignal"),
    pileup=cms.InputTag("slimmedAddPileupInfo"),
    backgroundFile=cms.string(os.path.abspath(options.backgroundFile)),
)
process.forest.insert(0, process.ooRecoveredGenParticles)
process.HiGenParticleAna.genParticleSrc = cms.InputTag("ooRecoveredGenParticles")
process.HiGenParticleAna.signalGenParticleSrc = cms.InputTag("ooRecoveredGenParticles", "signal")
process.HiForestInfo.info.append("OO generator sube: PYTHIA=0, HIJING=1 from BX0 EventID")
