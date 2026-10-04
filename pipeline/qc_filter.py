#!/usr/bin/env python3
"""Quality control before node extraction (per bridge stack):
 1) perpendicular baseline: drop dates with |Bperp| > BMAX (default 200 m)
 2) reference point: stable ground PS (StaMPS, 150-400 m from the bridge) with temporal coherence >= 0.95; reference cluster = PS within 50 m
 3) per-date reference coherence: gamma_d = |mean_k exp(j*phi_res_k,d)| over the reference cluster (phi_res = residual from each PS's own
    offset+velocity+annual model); drop dates with gamma_d < GMIN (default 0.95)
 4) every chain export is re-referenced to the same reference cluster (per-date mean removed) and cut to the kept dates
usage: qc_filter.py --work W --out W/deliverables_qc --lat --lon [--chains stamps_tropo miaplpy_tropo ...]"""
import os, glob, json, argparse, datetime, numpy as np
ap = argparse.ArgumentParser()
ap.add_argument('--work', required=True); ap.add_argument('--out', required=True); ap.add_argument('--lat', type=float, required=True); ap.add_argument('--lon', type=float, required=True)
ap.add_argument('--chains', nargs='+', default=['stamps_tropo', 'miaplpy_tropo', 'stamps_capon_relaxed_coh085', 'stamps_apes_relaxed_coh085'])
ap.add_argument('--bmax', type=float, default=200.0); ap.add_argument('--gmin', type=float, default=0.95); ap.add_argument('--cmin', type=float, default=0.95)
ap.add_argument('--master', default=os.environ.get('BIM_MASTER', '20220325')); ap.add_argument('--ref-chain', default='stamps_tropo'); ap.add_argument('--baselines', default=None)
ap.add_argument('--avoid', default=None, help='json list of [lon,lat] bridge points: reference PS must be >= --avoid-dist m from all of them (tile mode)')
ap.add_argument('--avoid-dist', type=float, default=100.0); ap.add_argument('--rmin', type=float, default=150.0); ap.add_argument('--rmax', type=float, default=400.0)
a = ap.parse_args(); os.makedirs(a.out, exist_ok=True)
LAM = 55.465763; KE = 111320 * np.cos(np.radians(a.lat))
D = os.path.join(a.work, 'deliverables')
def xy(e): return np.c_[(e['lon'] - a.lon) * KE, (e['lat'] - a.lat) * 111320.0]
# ---- 1) baselines
bp = {}
for d in glob.glob(os.path.join(a.baselines or os.path.join(a.work, 'stack', 'baselines'), '*_*')):
    ref, sec = os.path.basename(d).split('_'); txt = open(glob.glob(os.path.join(d, '*.txt'))[0]).read()
    bp[sec] = float([l for l in txt.splitlines() if l.startswith('Bperp')][0].split(':')[1])
# ---- 2) reference cluster from StaMPS
e0 = np.load(os.path.join(D, a.ref_chain, a.ref_chain + '_export.npz'), allow_pickle=True); dates = [str(x) for x in e0['dates']]
P = xy(e0); r = np.hypot(*P.T); coh = np.asarray(e0['coh'], float)
ok = (r >= a.rmin) & (r <= a.rmax)
if a.avoid:
    from scipy.spatial import cKDTree
    av = np.array(json.load(open(a.avoid))); AV = np.c_[(av[:, 0] - a.lon) * KE, (av[:, 1] - a.lat) * 111320.0]
    ok = ok & (cKDTree(AV).query(P)[0] >= a.avoid_dist)
cand = np.where(ok & (coh >= a.cmin))[0]
res = {'n_candidates_coh095': int(len(cand))}
if len(cand) == 0:
    json.dump(dict(res, status='FAIL_no_reference_coh%03d' % round(a.cmin * 100)), open(os.path.join(a.out, 'qc_summary.json'), 'w'), indent=1); raise SystemExit('no reference PS with coherence >= %.2f' % a.cmin)
best = None
for i in cand:
    nb = cand[np.hypot(*(P[cand] - P[i]).T) <= 50]
    sc = (len(nb), coh[nb].mean())
    if best is None or sc > best[0]: best = (sc, i, nb)
(_, ci, clus) = best
t = np.array([(datetime.datetime.strptime(d, '%Y%m%d') - datetime.datetime.strptime(a.master, '%Y%m%d')).days / 365.25 for d in dates])
A = np.c_[np.ones_like(t), t, np.sin(2 * np.pi * t), np.cos(2 * np.pi * t)]
X = e0['disp_mm'][clus]; resid = []   # common phase cancels in |mean exp(j phi)|, so this measures agreement inside the cluster
for x in X:
    m = np.isfinite(x); c = np.linalg.lstsq(A[m], x[m], rcond=None)[0]; resid.append(x - A @ c)
phi = 4 * np.pi / LAM * np.array(resid)                        # LOS mm -> rad
gam = np.abs(np.nanmean(np.exp(1j * phi), axis=0))
keep = np.array([abs(bp.get(d, 0.0)) <= a.bmax for d in dates]) & (gam >= a.gmin)
if a.master in dates: keep[dates.index(a.master)] = True
else: a.master = [d for d, k in zip(dates, keep) if k][0]
drop_b = [d for d in dates if abs(bp.get(d, 0.0)) > a.bmax]; drop_g = [d for d, g in zip(dates, gam) if g < a.gmin]
res.update(reference=dict(lat=float(e0['lat'][ci]), lon=float(e0['lon'][ci]), dist_m=float(r[ci]), n_cluster=int(len(clus)), coh_mean=float(coh[clus].mean())),
           n_dates=len(dates), n_kept=int(keep.sum()), dropped_baseline=drop_b, dropped_refcoh=[(d, round(float(g), 3)) for d, g in zip(dates, gam) if g < a.gmin],
           refcoh_median=float(np.median(gam)), bmax=a.bmax, gmin=a.gmin, cmin=a.cmin)
# ---- 4) re-reference and cut every chain
kd = [d for d, k in zip(dates, keep) if k]
for ch in a.chains:
    f = os.path.join(D, ch, ch + '_export.npz')
    if not os.path.exists(f): continue
    e = np.load(f, allow_pickle=True); ed = [str(x) for x in e['dates']]; cols = [ed.index(d) for d in kd if d in ed]
    Q = xy(e); near = np.hypot(*(Q - P[ci]).T) <= 50
    disp = e['disp_mm'][:, cols]
    if near.sum() >= 3:   # re-reference to the QC reference cluster; chains cropped around the deck (Capon/APES) keep dates only and are aligned later by ground PS pairs
        disp = disp - np.nanmean(disp[near], axis=0, keepdims=True); disp = disp - disp[:, [kd.index(a.master)]]
    out = {k: e[k] for k in e.files if k not in ('disp_mm', 'dates')}; out['disp_mm'] = disp; out['dates'] = np.array(kd)
    os.makedirs(os.path.join(a.out, ch), exist_ok=True); np.savez(os.path.join(a.out, ch, ch + '_export.npz'), **out)
    res.setdefault('chains', {})[ch] = dict(n_points=int(len(e['lon'])), ref_points=int(near.sum()), rereferenced=bool(near.sum() >= 3))
res['status'] = 'OK'
json.dump(res, open(os.path.join(a.out, 'qc_summary.json'), 'w'), indent=1, ensure_ascii=False)
print(json.dumps({k: (v if k not in ('dropped_refcoh',) else len(v)) for k, v in res.items()}, ensure_ascii=False)[:900])
