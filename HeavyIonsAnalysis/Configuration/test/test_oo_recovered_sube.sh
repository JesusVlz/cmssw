#!/usr/bin/env bash
# Produce and check one forest from the first N entries of a central weighted MiniAOD.
set -euo pipefail
if (( $# != 3 )); then
    echo "Usage: bash test_oo_recovered_sube.sh MINI_FILE EVENTS NEW_OUTPUT_DIRECTORY" >&2
    exit 2
fi
: "${CMSSW_BASE:?Activate the HiForest CMSSW area with cmsenv first}"
mini=$1
events=$2
out=$3
dir="$CMSSW_BASE/src/HeavyIonsAnalysis/Configuration/test"

[[ "$events" =~ ^[1-9][0-9]*$ ]] || { echo "EVENTS must be positive" >&2; exit 2; }
mkdir -- "$out"
out=$(cd -- "$out" && pwd -P)
work=$(mktemp -d "${TMPDIR:-/tmp}/oo-sube.XXXXXXXX")
trap 'rm -rf -- "$work"' EXIT

python3 "$dir/build_oo_hijing_truth.py" --mini "$mini" --events "$events" --output "$work/hijing.txt"
cmsRun "$dir/forest_oo_recovered_sube_cfg.py" "mini=$mini" "events=$events" \
    "backgroundFile=$work/hijing.txt" "forestOutput=$work/HiForest.root" > "$out/cmsRun.log" 2>&1 || {
    tail -n 50 "$out/cmsRun.log" >&2
    exit 1
}
python3 "$dir/check_oo_recovered_sube.py" --mini "$mini" --events "$events" \
    --index "$work/hijing.txt" --forest "$work/HiForest.root" | tee "$out/validation.log"
mv -- "$work/HiForest.root" "$out/HiForest.root"
echo "Verified forest: $out/HiForest.root"
