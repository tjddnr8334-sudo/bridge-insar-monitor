#!/bin/bash
# usage: run_stamps_variant.sh <stamps_in dir> <stamps out dir> <DA thresh> <master> [weed_standard_dev]
# StaMPS PS chain (prep_data_isce.py -> octave stamps 1..8) with the same parameters as the site phaseC scripts.
IN=$1; OUTD=$2; DA=${3:-0.42}; MASTER=${4:-$BIM_MASTER}; WSD=${5:-1.0}
export CONDA_ENV=$BIM_CONDA/envs/isce2_mintpy
export ISCE_HOME=${CONDA_ENV}/lib/python3.11/site-packages/isce
export ISCE_STACK=${CONDA_ENV}/share/isce2
export PYTHONPATH=${ISCE_HOME}:${ISCE_HOME}/applications:${ISCE_HOME}/components:${ISCE_STACK}
export PATH=${ISCE_HOME}/bin:${ISCE_HOME}/applications:${ISCE_STACK}/topsStack:${CONDA_ENV}/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export STAMPS=${STAMPS:-/home/insar/StaMPS}; export PATH=$STAMPS/bin:$PATH
mkdir -p $OUTD; LOG=$OUTD/run.log
echo "=== StaMPS variant start $(date): in=$IN out=$OUTD DA=$DA WSD=$WSD ===" | tee -a $LOG
if [ ! -d $OUTD/PATCH_1 ]; then
  python3 $BIM_HOME/prep_stamps.py --isce-dir $IN --output-dir $OUTD --da-thresh $DA --master-date $MASTER --min-cand 1500 --pct 12 --da-max 0.70 2>&1 | tail -40 | tee -a $LOG
fi
cd $OUTD/PATCH_1 || exit 1
if [ ! -f phuw2.mat ]; then
octave --no-gui --eval "
    pkg load signal;
    addpath(genpath('$STAMPS/matlab'));
    stamps(1,1);
    setparm('density_rand', 80); setparm('percent_rand', 60);
    setparm('weed_standard_dev', $WSD); setparm('weed_max_noise', inf); setparm('weed_zero_elevation', 'n');
    setparm('merge_resample_size', 0);
    setparm('unwrap_grid_size', 50); setparm('unwrap_gold_n_win', 8); setparm('unwrap_prefilter_flag', 'y'); setparm('unwrap_method', '3D');
    setparm('filter_grid_size', 12); setparm('filter_weighting', 'P-square'); setparm('scla_deramp', 'y'); setparm('scn_time_win', 180);
    stamps(2,2); stamps(3,3); stamps(4,4); stamps(5,5); stamps(6,6); stamps(7,7); stamps(8,8);
    fprintf('=== StaMPS complete ===\n');
" 2>&1 | grep -E "PS_|Number|selected|weeded|kept|error|Error|complete|=== " | tail -40 | tee -a $LOG
fi
ls -la phuw2.mat ps2.mat 2>/dev/null | tee -a $LOG
echo "=== StaMPS variant end $(date) ===" | tee -a $LOG
