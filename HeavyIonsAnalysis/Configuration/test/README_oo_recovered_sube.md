# Recover generator subevents for the central weighted OO EEC sample

The central sample `/QCD-dijet_pThat15-event-weighted_TuneCP5_5p36TeV_pythia8/HINOOSpring25MiniAOD-CustomTrack_150X_mcRun3_2025_forOO_realistic_v9-v2/MINIAODSIM` contains embedded reconstructed events. Its central DIGI configuration rebuilt the generator collection from PYTHIA. In one central event all 218 packed particles belonged to the signal reference collection. To measure an EEC with both sources, the pilot reads the background event ID stored at bunch crossing zero in `slimmedAddPileupInfo` and retrieves that exact event from the original HIJING GEN-SIM dataset.

The optional forest configuration writes the usual `HiGenParticleAna/hi` tree. Each selected PYTHIA particle has `sube=0` and each selected HIJING particle has `sube=1`. For each unordered pair of different particles, labels `(0,0)`, `(0,1)` or `(1,0)`, and `(1,1)` give signal–signal, signal–background and background–background respectively. Both mixed orderings contribute to the same term. Gen jets still use the standard signal-only inputs. This workflow supplies generator-particle origin. Reconstructed-track origin requires a separate truth association.

## Run a ten-event test on lxplus

Use an x86-64 EL9 session, a CMS grid proxy or equivalent working DAS/XRootD credentials, and a built `CMSSW_15_0_11` HiForest checkout containing this change. From that release's `src` directory run:

```bash
eval "$(scram runtime -sh)"
scram b -j 4

DATASET='/QCD-dijet_pThat15-event-weighted_TuneCP5_5p36TeV_pythia8/HINOOSpring25MiniAOD-CustomTrack_150X_mcRun3_2025_forOO_realistic_v9-v2/MINIAODSIM'
MINI=$(dasgoclient -query="file dataset=$DATASET" -limit 1)
TEST="$CMSSW_BASE/src/HeavyIonsAnalysis/Configuration/test"
bash "$TEST/test_oo_recovered_sube.sh" "$MINI" 10 "$PWD/oo-sube-test01"
```

If you already have a central MiniAOD LFN in `MINI`, use that same file. Supply a new output directory for each run. The test streams the original inputs remotely, keeps its temporary particle table in worker scratch and writes `HiForest.root`, `cmsRun.log` and `validation.log` in the output directory. AODSIM is optional for an independent playback-file cross-check. The test has no AODSIM input and runs no detector simulation, digitization, reconstruction or PAT.

For each run and luminosity block, the lookup queries all valid DBS-listed files in the HIJING bank and checks the full run/lumi/event ID. The pilot fails if the candidate list cannot be checked completely, a key is absent or ambiguous, or a primary packed particle is outside the signal reference set. Repeated use of the same background event by different signal events is supported. The in-memory table and per-lumi lookups are sized for a bounded pilot. For campaign-scale processing, reuse a validated HIJING index across file shards.

## Read the result

The last JSON record in `validation.log` reports the number of events, selected particles from each source, qualifying jets, pair counts and weighted EEC bin sums. The verifier compares the forest's signal particles against the central MiniAOD signal references, and repacks the original HIJING `genParticles::SIM` directly from the identified GEN-SIM file. It checks event alignment, generator weight and hard scale, and compares all three pair categories in 16 separation bins using the same AK4 gen-jet axes (`pt >= 80`, `abs(eta) <= 2.1`). It requires selected background particles. A short run can contain zero pairs of a given type.

The test reads the first ten entries without a generator-scale cut. For the target physics sample, select `hiEvtAnalyzer/HiTree.pthat > 80` GeV and use the stored `weight`. This event selection is independent of the jet threshold used in the diagnostic.

[Central DIGI request](https://cms-pdmv-prod.web.cern.ch/mcm/public/restapi/requests/get/HIN-HINOOSpring25Digi-00030) · [Central MiniAOD request](https://cms-pdmv-prod.web.cern.ch/mcm/public/restapi/requests/get/HIN-HINOOSpring25MiniAOD-00030) · [HIJING bank](https://cms-pdmv-prod.web.cern.ch/mcm/public/restapi/requests/get/HIN-HINOOSpring25GS-00001)
