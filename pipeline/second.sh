#!/bin/bash
# Second pass for one weak bridge: Capon + APES refocus -> StaMPS on each -> export -> merge with first pass -> re-score
T=$1; B=$2; E=$BIM_DATA; R2=$BIM_WORK/run2/$B; mkdir -p $R2 $E/logs2
[ -f $R2/merge2.json ] && exit 0
exec 9> $R2.lock; flock -n 9 || exit 0
exec > $E/logs2/$B.log 2>&1; echo "start $T $B $(date)"
export HDF5_USE_FILE_LOCKING=FALSE
[ -f $R2/refocus.json ] || NPROC=${NPROC:-6} $BIM_CONDA/envs/isce2_mintpy/bin/python $BIM_HOME/refocus.py $T $B || { echo "{\"bid\":\"$B\",\"info\":\"refocus failed\"}" > $R2/merge2.json; exit 0; }
read LAT LON <<< $(python3 -c "import json;t=[x for x in json.load(open(\"$E/tiles.json\")) if x[\"tile\"]==\"$T\"][0];b=[b for b in t[\"bridges\"] if b[\"id\"]==\"$B\"][0];print(b[\"lat\"],b[\"lon\"])")
for V in apes capon ds; do
  [ -f $R2/$V/stamps/PATCH_1/phuw2.mat ] || bash $BIM_HOME/stamps.sh $R2/${V}_in $R2/$V/stamps 0.42 $BIM_MASTER 1.0 > $R2/${V}_stamps.log 2>&1
  ( source $BIM_CONDA/etc/profile.d/conda.sh; conda activate miaplpy; python3 $BIM_HOME/export_stamps.py --work $R2/$V --name $B --lat $LAT --lon $LON --radius 3000 --stamps-dir $R2/$V/stamps --tag stamps --skip-miaplpy | tail -1 )
done
source $BIM_CONDA/etc/profile.d/conda.sh; conda activate miaplpy
python3 $BIM_HOME/merge2.py $T $B
[ -f $R2/merge2.json ] && rm -rf $R2/capon_in $R2/apes_in $R2/apes/stamps $R2/capon/stamps
echo "end $(date)"
