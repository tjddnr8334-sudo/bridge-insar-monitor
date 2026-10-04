#!/usr/bin/env python3
"""Generic export of PS (StaMPS) and DS (MiaplPy/MintPy) point time series around a bridge to npz.
Output layout matches the Naegok deliverables: lon, lat, dates, disp_mm (n x n_dates, LOS mm, + = toward satellite),
coh, dem_err, hgt, vel_mm_yr, seasonal_amp_mm, resid_rms_mm, disp_stability_mm.

usage: export_stamps.py --work RUN_DIR --name TILE --lat LAT --lon LON --stamps-dir STAMPS_DIR --tag stamps --skip-miaplpy
"""
import os, sys, glob, json, argparse, datetime
import numpy as np

ap = argparse.ArgumentParser()
ap.add_argument('--work', required=True); ap.add_argument('--name', required=True)
ap.add_argument('--lat', type=float, required=True); ap.add_argument('--lon', type=float, required=True)
ap.add_argument('--radius', type=float, default=400.0, help='keep points within this many metres of the bridge centre')
ap.add_argument('--master', default=os.environ.get('BIM_MASTER', '20220325'))
ap.add_argument('--stamps-dir', default=None, help='StaMPS dir containing PATCH_1 (default <work>/stamps)')
ap.add_argument('--tag', default='stamps', help='export tag for the StaMPS chain (deliverables/<tag>/<tag>_export.npz)')
ap.add_argument('--skip-miaplpy', action='store_true')
a = ap.parse_args()
WORK, OUT = a.work, os.path.join(a.work, 'deliverables'); os.makedirs(OUT, exist_ok=True)
LAMBDA = 0.05546576
KE = 111320 * np.cos(np.radians(a.lat)); KN = 111320.0

def years(dates):
    d0 = datetime.datetime.strptime(a.master, '%Y%m%d')
    return np.array([(datetime.datetime.strptime(s, '%Y%m%d') - d0).days / 365.25 for s in dates])

def metrics(disp, yrs):
    A = np.column_stack([np.ones(len(yrs)), yrs, np.sin(2 * np.pi * yrs), np.cos(2 * np.pi * yrs)])
    ok = np.isfinite(disp).all(axis=1)
    c = np.full((disp.shape[0], 4), np.nan)
    c[ok] = np.linalg.lstsq(A, disp[ok].T, rcond=None)[0].T
    fit = c @ A.T
    res = disp - fit
    return dict(vel_mm_yr=c[:, 1], seasonal_amp_mm=np.hypot(c[:, 2], c[:, 3]),
                resid_rms_mm=np.sqrt(np.nanmean(res ** 2, axis=1)),
                disp_stability_mm=np.nanstd(disp - np.outer(c[:, 1], yrs), axis=1))

def save(tag, lon, lat, dates, disp, coh, dem_err, hgt, extra):
    r = np.hypot((lon - a.lon) * KE, (lat - a.lat) * KN)
    k = r <= a.radius
    lon, lat, disp, coh, dem_err, hgt = lon[k], lat[k], disp[k], coh[k], dem_err[k], hgt[k]
    m = metrics(disp, years(dates))
    d = os.path.join(OUT, tag); os.makedirs(d, exist_ok=True)
    np.savez(os.path.join(d, tag + '_export.npz'), lon=lon, lat=lat, dates=np.array(dates), disp_mm=disp, coh=coh,
             dem_err=dem_err, hgt=hgt, **m)
    summ = dict(name=a.name, n_points=int(k.sum()), n_epochs=len(dates), master=a.master, radius_m=a.radius,
                coh_median=float(np.nanmedian(coh)), vel_median=float(np.nanmedian(m['vel_mm_yr'])),
                vel_std=float(np.nanstd(m['vel_mm_yr'])), stability_median=float(np.nanmedian(m['disp_stability_mm'])), **extra)
    json.dump(summ, open(os.path.join(d, tag + '_summary.json'), 'w'), indent=2, ensure_ascii=False)
    print(tag, json.dumps(summ, ensure_ascii=False))

# ------------------------------------------------------------------ StaMPS (PS)
P = os.path.join(a.stamps_dir or os.path.join(WORK, 'stamps'), 'PATCH_1')
if os.path.exists(os.path.join(P, 'phuw2.mat')) or os.path.exists(os.path.join(P, 'phuw2')):
    import scipy.io as sio, subprocess
    v7 = os.path.join(P, 'export_v7.mat')
    if not os.path.exists(v7):   # StaMPS under Octave writes some files in Octave text format -> convert with octave
        cmd = ("pkg load signal; addpath(genpath('" + os.environ.get('STAMPS', '/home/insar/StaMPS') + "/matlab')); cd('%s'); ps=load('ps2'); uw=load('phuw2'); out=struct();"
               "out.lonlat=ps.lonlat; out.day=ps.day; out.master_day=ps.master_day; out.ij=ps.ij; out.ph_uw=uw.ph_uw;"
               "if exist('scla2','file')||exist('scla2.mat','file'), s=load('scla2'); out.ph_scla=s.ph_scla; end;"
               "if exist('scn2','file')||exist('scn2.mat','file'), s=load('scn2'); out.ph_scn_slave=s.ph_scn_slave; end;"
               "if exist('hgt2','file')||exist('hgt2.mat','file'), s=load('hgt2'); out.hgt=s.hgt; end;"
               "if exist('pm2','file')||exist('pm2.mat','file'), s=load('pm2'); out.coh_ps=s.coh_ps; end;"
               "save('-v7','export_v7.mat','-struct','out'); disp('converted');") % P
        r = subprocess.run(['octave', '--no-gui', '--eval', cmd], capture_output=True, text=True)
        print(r.stdout[-300:], r.stderr[-300:])
    S = sio.loadmat(v7)
    ll = S['lonlat']; day = S['day'].ravel(); phuw = S['ph_uw']; corr = {}
    for key in ('ph_scla', 'ph_scn_slave'):
        if key in S and S[key].shape == phuw.shape: phuw = phuw - S[key]; corr[key] = True
        else: corr[key] = False
    hgt = S['hgt'].ravel() if 'hgt' in S else np.full(len(ll), np.nan)
    coh = S['coh_ps'].ravel() if 'coh_ps' in S else np.full(len(ll), np.nan)
    dem_err = np.full(len(ll), np.nan)
    disp = -phuw * LAMBDA / (4 * np.pi) * 1000.0
    dates = [(datetime.datetime.fromordinal(int(x)) + datetime.timedelta(days=x % 1) - datetime.timedelta(days=366)).strftime('%Y%m%d') for x in day]
    mi = dates.index(a.master) if a.master in dates else 0
    disp = disp - disp[:, [mi]]
    save(a.tag, ll[:, 0].astype(float), ll[:, 1].astype(float), dates, disp, coh, dem_err, hgt,
         dict(scatterer='PS (StaMPS)', corrections=corr, reference='StaMPS patch mean'))
else:
    print('StaMPS output not found:', P)

# ------------------------------------------------------------------ MiaplPy / MintPy (DS+PS)
import h5py
N = os.path.join(WORK, 'miaplpy', 'network_single_reference')
for tag, tsf in ([] if a.skip_miaplpy else (('miaplpy', 'timeseries_demErr.h5'), ('miaplpy_tropo', 'timeseries_ERA5_demErr.h5'))):
    f = os.path.join(N, tsf)
    if not os.path.exists(f):
        print('skip', tag, '(no', tsf + ')'); continue
    with h5py.File(f) as h:
        ts = h['timeseries'][:]; dates = [s.decode() for s in h['date'][:]]
    with h5py.File(os.path.join(N, 'inputs', 'geometryRadar.h5')) as h:
        glat = h['latitude'][:]; glon = h['longitude'][:]; ghgt = h['height'][:]
    with h5py.File(os.path.join(N, 'temporalCoherence.h5')) as h:
        tc = h['temporalCoherence'][:]
    mask = np.ones(tc.shape, bool)
    for mf in ('maskTempCoh.h5', 'maskPS.h5'):
        pass
    if os.path.exists(os.path.join(N, 'maskTempCoh.h5')):
        with h5py.File(os.path.join(N, 'maskTempCoh.h5')) as h: mask = h['mask'][:].astype(bool)
    dem_err = np.full(tc.shape, np.nan)
    if os.path.exists(os.path.join(N, 'demErr.h5')):
        with h5py.File(os.path.join(N, 'demErr.h5')) as h: dem_err = h['dem'][:]
    sel = mask & np.isfinite(glat) & (np.hypot((glon - a.lon) * KE, (glat - a.lat) * KN) <= a.radius)
    ii, jj = np.where(sel)
    disp = ts[:, ii, jj].T * 1000.0
    mi = dates.index(a.master) if a.master in dates else 0
    disp = disp - disp[:, [mi]]
    save(tag, glon[ii, jj].astype(float), glat[ii, jj].astype(float), dates, disp, tc[ii, jj], dem_err[ii, jj], ghgt[ii, jj],
         dict(scatterer='DS+PS via sequential EMI phase linking (MiaplPy) + MintPy', timeseries_file=tsf, tempcoh_threshold=0.5,
              reference='MintPy reference pixel (auto, max coherence)'))
print('EXPORT DONE')
