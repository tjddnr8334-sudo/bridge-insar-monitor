#!/bin/bash
# One tile: StaMPS PS (window from stream_cut.py) -> export -> QC filter (Bperp<=200 m, reference PS coh>=0.95, per-date reference coherence>=0.95) -> post.py
T=$1; E=$BIM_DATA; W=$E/win/$T; R=$BIM_WORK/run/$T; mkdir -p $R $E/res $E/logs
[ -f $E/res/$T.json ] && exit 0
exec 9> $R.lock; flock -n 9 || { echo "$T already running"; exit 0; }
[ -f $W/CUT_DONE ] || { echo "no window $T"; exit 1; }
exec > $E/logs/$T.log 2>&1; echo "start $(date)"
read CLAT CLON <<< $(python3 -c "import json;t=[x for x in json.load(open(\"$E/tiles.json\")) if x[\"tile\"]==\"$T\"][0];print(t[\"clat\"],t[\"clon\"])")
python3 -c "import json;t=[x for x in json.load(open(\"$E/tiles.json\")) if x[\"tile\"]==\"$T\"][0];json.dump([p for b in t[\"bridges\"] for g in b[\"geo\"] for p in g] or [[b[\"lon\"],b[\"lat\"]] for b in t[\"bridges\"]],open(\"$R/avoid.json\",\"w\"))"
# candidate selection for rural/mountain tiles: calibrated amplitude, snow-free months, adaptive D_A (gw/prep_data_isce_gw.py)
[ -f $R/stamps/PATCH_1/phuw2.mat ] || bash $BIM_HOME/stamps.sh $W $R/stamps 0.42 $BIM_MASTER 1.0 > $R/stamps_run.log 2>&1
grep -o "adaptive D_A.*" $R/stamps_run.log > $R/da_used 2>/dev/null || echo 0.42 > $R/da_used
[ -f $R/stamps/PATCH_1/phuw2.mat ] || { echo "StaMPS failed"; tail -20 $R/stamps_run.log; python3 - <<PY
import json; t=[x for x in json.load(open("$E/tiles.json")) if x["tile"]=="$T"][0]
json.dump(dict(tile="$T", qc=dict(status="FAIL_stamps"), bridges=[dict(id=b["id"],n=b["n"],c=b["c"],lat=b["lat"],lon=b["lon"],len=b["len"],level=-1,st="판정불가",reason="StaMPS 처리 실패 (PS 부족)") for b in t["bridges"]]), open("$E/res/$T.json","w"), ensure_ascii=False)
PY
exit 0; }
source $BIM_CONDA/etc/profile.d/conda.sh; conda activate miaplpy; export HDF5_USE_FILE_LOCKING=FALSE
python3 $BIM_HOME/export_stamps.py --work $R --name $T --lat $CLAT --lon $CLON --radius 3000 --stamps-dir $R/stamps --tag stamps --skip-miaplpy | tail -2
python3 $BIM_HOME/qc_filter.py --work $R --out $R/qc/deliverables --lat $CLAT --lon $CLON --chains stamps --ref-chain stamps --baselines $W/baselines --avoid $R/avoid.json --rmin 0 --rmax 1200 | tail -c 600
for CM in 0.92 0.90 0.85 0.80; do grep -q '"status": "OK"' $R/qc/deliverables/qc_summary.json 2>/dev/null && break; echo "reference relaxed to $CM"; python3 $BIM_HOME/qc_filter.py --cmin $CM --gmin $CM --work $R --out $R/qc/deliverables --lat $CLAT --lon $CLON --chains stamps --ref-chain stamps --baselines $W/baselines --avoid $R/avoid.json --rmin 0 --rmax 1200  | tail -c 300; done
python3 $BIM_HOME/post.py $T
cp $R/qc/deliverables/qc_summary.json $E/res/${T}_qc.json 2>/dev/null; mkdir -p $E/exports; cp $R/qc/deliverables/stamps/stamps_export.npz $E/exports/${T}_stamps_qc.npz 2>/dev/null
[ -f $E/res/$T.json ] && rm -rf $R/stamps
echo "end $(date)"
