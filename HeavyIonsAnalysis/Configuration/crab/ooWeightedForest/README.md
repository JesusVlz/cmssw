# CRAB production of the weighted OO HiForest

This campaign forests the central `QCD-dijet_pThat15-event-weighted` OO MiniAODSIM. It retains all events, the generator weight and `pthat`. For the PYTHIA hard-scale selection, apply `hiEvtAnalyzer/HiTree.pthat > 80` GeV and use `weight` in the analysis. The generator-particle tree labels PYTHIA with `sube=0` and the embedded HIJING particles with `sube=1`, so the forest supports SS, SB and BB pairs. Gen jets use the signal collection. The workflow builds on the [single-file closure test](../../test/README_oo_recovered_sube.md).

## Prepare the submission on lxplus

Use the `CMSSW_15_0_11` x86-64 EL9 area in which the PR's recovery producer and the standard OO forest already run. For the existing test installation with a separate sparse checkout, update the Python and shell files with the following commands.

```bash
WORK="/afs/cern.ch/work/${USER:0:1}/$USER/oo_pthat80/sube-pr2"
cd "$WORK/CMSSW_15_0_11/src"
source /cvmfs/cms.cern.ch/cmsset_default.sh
eval "$(scram runtime -sh)"
PKG=HeavyIonsAnalysis/Configuration/crab/ooWeightedForest
git -C "$WORK/overlay" fetch --depth=1 --filter=blob:none https://github.com/gkrintir/cmssw.git recover-oo-hijing-sube
git -C "$WORK/overlay" archive FETCH_HEAD "$PKG" | tar -x -C "$CMSSW_BASE/src"
```

If the repository is already checked out in the release, updating that checkout makes the same files available. Start the CRAB client in that runtime. Use one persistent working directory for the catalog, plan and submission receipts.

```bash
source /cvmfs/cms.cern.ch/common/crab-setup.sh
voms-proxy-init -voms cms -valid 192:00
TOOLS="$CMSSW_BASE/src/HeavyIonsAnalysis/Configuration/crab/ooWeightedForest"
RUN="/afs/cern.ch/work/${USER:0:1}/$USER/oo_pthat80/crab-weighted-v1"
mkdir -p "$RUN"
cd "$RUN"
crab checkwrite --site=T2_US_Nebraska --lfn=/store/user/gkrintir/HiForest/OO2025/MC

CAT="$RUN/oo_hijing_catalog.sqlite.gz"
python3 "$TOOLS/hijing_catalog.py" --output "$CAT" --work "$RUN/hijing-header-cache" --workers 2
python3 "$TOOLS/campaign.py" plan --plan plan
```

Build the catalog once while a grid proxy is valid. The builder streams the original HIJING file headers through XRootD, checks that every run/lumi/event is unique, and compares the totals with DBS. A failed scan can reuse its completed checkpoints. After success, reuse `$CAT` and `plan/plan.json` for all tasks. The plan records the actual file and event counts, the DBS blocks, and 12 pilot LFNs spanning the primary file sizes or event counts.

## Measure one and two cores on the same events

```bash
COMMON=(--plan plan --tag v1 --catalog "$CAT")
python3 "$TOOLS/campaign.py" submit "${COMMON[@]}" --mode pilot1
python3 "$TOOLS/campaign.py" submit "${COMMON[@]}" --mode pilot2
P1=crab_oo_sube/crab_OOsube_v1_pilot1
P2=crab_oo_sube/crab_OOsube_v1_pilot2
crab status -d "$P1"
crab status -d "$P2"
```

| Setting | One-core pilot | Two-core pilot |
| --- | ---: | ---: |
| Requested cores | 1 | 2 |
| Requested memory | 2500 MB | 4000 MB |
| Primary files per job | 1 | 1 |
| Concurrent HIJING readers | 1 | Up to 2 |
| Forest threads / streams | 1 / 1 | 1 / 1 |
| Requested wall time | 20 h | 20 h |

These are two CRAB tasks, normally 12 jobs each. A job streams the original HIJING events recorded in its assigned MiniAOD, grouping reads by source file. Its temporary table is deleted after foresting. Each job checks the output tree event counts and the primary event IDs before CRAB transfers the forest. Full production stages the ROOT output to Nebraska. Pilot tasks also stage their logs. The CRAB scheduler places jobs at eligible input sites. The local lxplus Condor accounting group and job flavour apply only to submissions through that batch system.

After both tasks finish successfully, download the pilot logs and compare delivered events per allocated core-hour, including prior attempts where CRAB provides their durations.

```bash
crab getlog -d "$P1" --quantity=all
crab getlog -d "$P2" --quantity=all
NFILES=$(python3 -c 'import json; print(json.load(open("plan/plan.json"))["nfiles"])')
NEVENTS=$(python3 -c 'import json; print(json.load(open("plan/plan.json"))["nevents"])')
python3 "$TOOLS/size_jobs.py" "$P1" "$P2" --dataset-files "$NFILES" --dataset-events "$NEVENTS"
```

Start with one core and one file per job. Choose two cores if the same selected events cost fewer allocated core-hours and the measured peak memory fits. The sizing script reports output TB, resource use and a candidate number of files per job. It uses the observed slow tail and a six-hour target. With 12 jobs the reported empirical 95th percentile is the slowest job. Check memory at the **actual** grouping because the recovery producer loads all distinct background events for that job. The requested wall time leaves room for variable remote-read speeds and stage-out.

## Validate the chosen grouping and submit the full sample

Set these variables to the chosen values. The defaults below reproduce the one-core pilot. If you changed them, use the bounded `pilotSized` first. Check that CRAB actually grouped the requested number of files on at least one job, since locality and file sizes can affect splitting.

```bash
CORES=1
MEMORY=2500
FILES=1
SIZE=(--splitting FileBased --cores "$CORES" --memory "$MEMORY" --units "$FILES")
python3 "$TOOLS/campaign.py" submit "${COMMON[@]}" --mode pilotSized "${SIZE[@]}"
PS=crab_oo_sube/crab_OOsube_v1_pilotSized
crab status -d "$PS"
# When it finishes
crab getlog -d "$PS" --quantity=all
python3 "$TOOLS/size_jobs.py" "$P1" "$PS" --dataset-files "$NFILES" --dataset-events "$NEVENTS"
```

If a single primary file runs out of memory or time, use `--mode pilotLumi`. Retrieve its logs and run `size_jobs.py PROJECT --unit lumi`. Use `--splitting LumiBased --units N` with the chosen cores and memory for a sized pilot and for production. Here `N` counts lumisections. The worker applies CRAB's lumi mask to both the HIJING preparation and forest. The lumi baseline and sized pilot accept `--pilot-lumis 24` to test a larger common set. Use a fresh tag for a second `pilotSized` with different settings and compare it with the matching baseline.

```bash
python3 "$TOOLS/campaign.py" submit "${COMMON[@]}" --mode full "${SIZE[@]}" --preview
python3 "$TOOLS/campaign.py" submit "${COMMON[@]}" --mode full "${SIZE[@]}"
for task in crab_oo_sube/crab_OOsube_v1_full*; do crab status -d "$task"; done
```

File-based production uses disjoint input groups of at most 8000 primary files per task. A block larger than that is split by explicit file lists. The preview prints all tasks and their eligible inputs. For planning, divide the DBS file count by the validated files/job and round up. The actual count can be larger at block and site boundaries. Lumi-based production uses one DBS block per task. All full tasks use separate output dataset tags under `/store/user/gkrintir/HiForest/OO2025/MC` at `T2_US_Nebraska`. Use `--storage SITE --outlfn PREFIX` on every submission to choose another destination.

Repeat the same full command after a partially submitted campaign. It skips tasks with confirmed receipts. If a submission outcome is unknown, inspect the recorded CRAB request and local project before retrying. Use `crab resubmit -d PROJECT` for failed jobs in an existing task. Keep pilot ROOT files separate from full production because the pilots intentionally repeat primary events.

## Check coverage

After the full tasks finish, check their CRAB reports and registered output file names against the frozen primary dataset. For the final event total, the second command also reads the entry count of each staged forest's `hiEvtAnalyzer/HiTree` over XRootD.

```bash
python3 "$TOOLS/check_coverage.py" --plan plan --work-area crab_oo_sube --tag v1
python3 "$TOOLS/check_coverage.py" --plan plan --work-area crab_oo_sube --tag v1 --count-output-events
```
