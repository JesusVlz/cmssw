"""CRAB Analysis configuration for the central weighted oxygen MiniAODSIM sample."""

import os
from pathlib import Path

from CRABClient.UserUtilities import config as new_config

DATASET = ("/QCD-dijet_pThat15-event-weighted_TuneCP5_5p36TeV_pythia8/"
           "HINOOSpring25MiniAOD-CustomTrack_150X_mcRun3_2025_forOO_realistic_v9-v2/MINIAODSIM")
HERE = Path(__file__).resolve().parent
CATALOG = "oo_hijing_catalog.sqlite.gz"


def make_config(*, name, catalog, files=None, blocks=None, splitting="FileBased", units=1, total=-1,
                cores=1, memory=2500, logs=False, storage="T2_US_Nebraska",
                outlfn="/store/user/gkrintir/HiForest/OO2025/MC", work_area="crab_oo_sube"):
    if files and blocks:
        raise ValueError("Choose either a file mask or a block mask")
    if splitting not in ("FileBased", "LumiBased") or (files and splitting != "FileBased"):
        raise ValueError("A file mask requires FileBased splitting")
    if cores not in (1, 2) or not 0 < memory <= (3000 if cores == 1 else 5000):
        raise ValueError("Choose 1 core up to 3000 MB, or 2 cores up to 5000 MB")
    if units < 1 or (total != -1 and total < 1):
        raise ValueError("Invalid unitsPerJob or totalUnits")
    catalog = Path(catalog).expanduser().resolve()
    if catalog.name != CATALOG:
        raise ValueError(f"Use the worker input filename {CATALOG}")

    cfg = new_config()
    cfg.General.requestName = name
    cfg.General.workArea = str(work_area)
    cfg.General.transferOutputs = True
    cfg.General.transferLogs = logs

    cfg.JobType.pluginName = "Analysis"
    cfg.JobType.psetName = str(HERE / "forest_crab_cfg.py")
    cfg.JobType.scriptExe = str(HERE / "run_forest.sh")
    cfg.JobType.inputFiles = [str(HERE / "run_forest.py"), str(HERE / "hijing_catalog.py"),
                              str(HERE.parents[1] / "test" / "build_oo_hijing_truth.py"), str(catalog)]
    cfg.JobType.pyCfgParams = [f"cores={cores}"]
    cfg.JobType.numCores = cores
    cfg.JobType.maxMemoryMB = memory
    cfg.JobType.maxJobRuntimeMin = 1200

    cfg.Data.inputDataset = DATASET
    cfg.Data.inputDBS = "global"
    if files:
        cfg.Data.userInputFiles = list(files)
    if blocks:
        cfg.Data.inputBlocks = list(blocks)
    cfg.Data.splitting = splitting
    cfg.Data.unitsPerJob = units
    cfg.Data.totalUnits = total
    cfg.Data.publication = False
    cfg.Data.ignoreLocality = False
    cfg.Data.outLFNDirBase = outlfn
    cfg.Data.outputDatasetTag = name
    cfg.Site.storageSite = storage
    return cfg


# Direct submission with this file describes one full-dataset task. campaign.py
# calls make_config with explicit input masks for matched pilots and disjoint production tasks.
config = make_config(name="OOsube_v1_all", catalog=os.environ.get("OO_HIJING_CATALOG", CATALOG))
