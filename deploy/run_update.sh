#!/bin/bash
# Daily entry point (cron / systemd): one update cycle for the given municipality config; idle when no new acquisition.
cd "$(dirname "$0")/.."
source ${BIM_CONDA:-/home/insar/miniforge3}/etc/profile.d/conda.sh && conda activate b2s
exec python -m bim update --config "${1:?config yaml}" >> logs/update_$(date +%Y%m).log 2>&1
