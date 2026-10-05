# -*- coding: utf-8 -*-
"""Incremental Sentinel-1 download for one track: only the bursts over the AOI (ASF burst SLCs -> SAFE via burst2stack), VV,
plus precise/restituted orbits (sentineleof). Credentials: ~/.netrc (urs.earthdata.nasa.gov) and the Copernicus account of
sentineleof; nothing is stored in this repository."""
import datetime, glob, os, shutil, subprocess


def have_dates(slc_dir):
    out = set()
    for s in glob.glob(os.path.join(slc_dir, 'S1*_IW_SLC__1S*.SAFE')):
        b = os.path.basename(s); out.add(b.split('_')[5][:8])
    return out


def fetch_date(env, aoi_bounds, path, date, slc_dir, log):
    """burst2stack for one date; returns True when a SAFE was produced."""
    d0 = datetime.datetime.strptime(date, '%Y%m%d'); d1 = d0 + datetime.timedelta(days=1)
    tmp = os.path.join(os.path.dirname(slc_dir), 'tmp_%s' % date); os.makedirs(tmp, exist_ok=True)
    ext = aoi_bounds if isinstance(aoi_bounds, str) else '%.4f %.4f %.4f %.4f' % tuple(aoi_bounds)   # geometry file (AOI polygon) or W S E N
    cmd = ('source %s/etc/profile.d/conda.sh && conda activate b2s && cd %s && burst2stack --rel-orbit %d --start-date %s --end-date %s '
           '--extent %s --pols VV --output-dir %s') % (env['BIM_CONDA'], tmp, path, d0.date(), d1.date(), ext, tmp)
    with open(log, 'a') as lf:
        rc = subprocess.run(['bash', '-c', cmd], stdout=lf, stderr=subprocess.STDOUT).returncode
    safes = glob.glob(os.path.join(tmp, 'S1*.SAFE'))
    for s in safes: shutil.move(s, os.path.join(slc_dir, os.path.basename(s)))
    shutil.rmtree(tmp, ignore_errors=True)
    return rc == 0 and bool(safes)


def fetch_orbit(env, date, mission, orbit_dir, log):
    cmd = 'source %s/etc/profile.d/conda.sh && conda activate isce2 && eof --date %s --mission %s --save-dir %s' % (
        env['BIM_CONDA'], date, mission, orbit_dir)
    with open(log, 'a') as lf:
        return subprocess.run(['bash', '-c', cmd], stdout=lf, stderr=subprocess.STDOUT).returncode == 0


def update(env, aoi_bounds, path, dates, slc_dir, orbit_dir, log):
    """Download every listed date not yet present (BIM_DL_WORKERS in parallel, default 3); returns the new dates."""
    os.makedirs(slc_dir, exist_ok=True); os.makedirs(orbit_dir, exist_ok=True)
    have = have_dates(slc_dir); todo = [d for d in dates if d not in have]

    def one(d):   # download is network-bound: a few dates in parallel (each in its own tmp_<date> folder)
        if not fetch_date(env, aoi_bounds, path, d, slc_dir, log): return None
        for s in glob.glob(os.path.join(slc_dir, 'S1*%s*.SAFE' % d)):
            fetch_orbit(env, d, os.path.basename(s)[:3], orbit_dir, log)
        return d
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=int(env.get('BIM_DL_WORKERS', '3'))) as ex:
        new = [d for d in ex.map(one, todo) if d]
    return sorted(new)
