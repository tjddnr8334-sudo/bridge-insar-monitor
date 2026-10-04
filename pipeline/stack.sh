#!/bin/bash
# Gangwon-wide screening, one topsStack of the bursts ISCE selects around (LAT,LON) (2 bursts), frame 117 path 54 asc, all dates.
# Per date: geo2rdr -> resample run back to back; stream_cut.py (started by the driver) cuts every tile window from the
# coregistered bursts and deletes them, so the root disk only holds the dates in flight.
# usage: stack.sh NAME SW LAT LON SLCDIR [S N W E]
set -u
NAME=$1; SW=$2; CLAT=$3; CLON=$4
SLCDIR=${5:?SLC directory (SAFE dirs of this track)}; BBOX="${6:-} ${7:-} ${8:-} ${9:-}"   # optional: SLC dir (north bursts) and S N W E bbox
[ -f $SLCDIR/../DOWNLOAD_FINISHED ] || echo 'note: download not marked finished'
GW=$BIM_WORK; WORK=$GW/stack_$NAME; LOG=$WORK/logs; mkdir -p $WORK/{SLC,orbits,logs} && cd $WORK
exec >> $LOG/stack.log 2>&1
echo "=== stack $NAME start $(date) ==="
export CONDA_ENV=$BIM_CONDA/envs/isce2_mintpy; export ISCE_HOME=${CONDA_ENV}/lib/python3.11/site-packages/isce; export ISCE_STACK=${CONDA_ENV}/share/isce2
export PYTHONPATH=${ISCE_HOME}:${ISCE_HOME}/applications:${ISCE_HOME}/components:${ISCE_STACK}
export PATH=${ISCE_HOME}/bin:${ISCE_HOME}/applications:${ISCE_STACK}/topsStack:${CONDA_ENV}/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PROJ_DATA=${CONDA_ENV}/share/proj; export PROJ_LIB=${CONDA_ENV}/share/proj; export OMP_NUM_THREADS=2
for s in $SLCDIR/S1*_IW_SLC__1S*.SAFE; do b=$(basename "$s"); d=$(echo $b | grep -o '_20[0-9]\{6\}T' | head -1 | tr -dc 0-9)
  grep -qx "$d" $BIM_DATA/bperp_excl_$BIM_TRACK.txt 2>/dev/null && continue    # |Bperp| > 200 m dates: removed by the QC filter anyway -> not coregistered
  [ -e SLC/$b ] || ln -s "$s" SLC/$b; done
for f in $BIM_ORBITS/*.EOF; do b=$(basename $f); [ -e orbits/$b ] || ln -s $(readlink -f $f) orbits/$b; done
[ -e aux ] || ln -s $BIM_AUX aux
echo "SLC $(ls SLC | wc -l)  orbits $(ls orbits | wc -l)"
if [ ! -d stack/run_files ] || [ -z "$(ls stack/run_files 2>/dev/null)" ]; then
  mkdir -p stack && cd stack
  python3 ${ISCE_STACK}/topsStack/stackSentinel.py -s $WORK/SLC -o $WORK/orbits -a $WORK/aux -d $BIM_DEM \
    -b "$( [ -n "${6:-}" ] && echo $BBOX || python3 -c "print('%.4f %.4f %.4f %.4f' % ($CLAT-0.004, $CLAT+0.004, $CLON-0.004, $CLON+0.004))")" -n "$SW" -m $BIM_MASTER -W slc -C geometry --num_proc 8 --num_proc4topo 4 2>&1 | tail -15
  cd $WORK
fi
cd $WORK/stack; ls run_files >/dev/null || exit 3
CS=$WORK/stack/coreg_secondarys; IW=IW$SW
( while true; do sleep 30; sync; echo 1 | sudo -n tee /proc/sys/vm/drop_caches >/dev/null 2>&1; done ) & DROPPER=$!
trap 'kill $DROPPER 2>/dev/null' EXIT
run_plain () { local rf=run_files/$1 P=${2:-1}; [ -f $LOG/done_$1 ] && { echo "skip $1"; return 0; }
  echo "--- $1 start $(date) ---"; grep -v '^\s*$' $rf | sed 's/ &$//' | xargs -d '\n' -P $P -I CMD bash -c 'CMD' >> $LOG/exec_$1.log 2>&1
  touch $LOG/done_$1; echo "--- $1 done $(date) ---"; }
run_plain run_01_unpack_topo_reference 1
[ "$(ls $WORK/stack/reference/$IW/burst_*.slc.vrt 2>/dev/null | wc -l)" -ge 1 ] || { echo "FATAL: no reference burst"; exit 4; }
run_plain run_02_unpack_secondary_slc 6
run_plain run_03_average_baseline 6
touch $LOG/GEOM_READY
# per-date geo2rdr+resample; a date is finished when stream_cut.py has cut it (cut.done) or its bursts are resampled
if [ ! -f $LOG/done_coreg ]; then
  : > $LOG/cmds_coreg.txt
  for g in $(grep -o "config_fullBurst_geo2rdr_[0-9]*" run_files/run_04_fullBurst_geo2rdr); do
    dt=${g##*_}; [ -f $CS/$dt/cut.done ] && continue
    G=$(grep "$g\b" run_files/run_04_fullBurst_geo2rdr | head -1 | sed 's/ &$//'); R=$(grep "config_fullBurst_resample_$dt\b" run_files/run_05_fullBurst_resample | head -1 | sed 's/ &$//')
    echo "$G > $LOG/c_$dt.log 2>&1 && $R >> $LOG/c_$dt.log 2>&1 && rm -f $CS/$dt/$IW/range_*.off* $CS/$dt/$IW/azimuth_*.off* && touch $CS/$dt/resampled" >> $LOG/cmds_coreg.txt
  done
  echo "coreg: $(wc -l < $LOG/cmds_coreg.txt) dates $(date)"
  # throttle: do not start more dates while > 12 resampled dates wait for the cutter (disk guard)
  while read -r line; do
    while [ "$(ls -d $CS/*/resampled 2>/dev/null | wc -l)" -gt 12 ] || [ "$(df --output=avail -BG / | tail -1 | tr -dc 0-9)" -lt 25 ]; do sleep 30; done
    while [ "$(jobs -rp | wc -l)" -ge ${P:-6} ]; do sleep 5; done
    bash -c "$line" < /dev/null &
  done < $LOG/cmds_coreg.txt
  for p in $(jobs -p); do [ "$p" = "$DROPPER" ] || wait $p; done   # do not wait for the cache dropper loop
  touch $LOG/done_coreg
fi
nf=0; for d in $CS/2*/; do [ -f $d/cut.done ] || [ -f $d/resampled ] || nf=$((nf+1)); done
echo "dates failed coreg: $nf (dropped downstream)"
touch $LOG/STACK_DONE; echo "=== stack $NAME done $(date) ==="
