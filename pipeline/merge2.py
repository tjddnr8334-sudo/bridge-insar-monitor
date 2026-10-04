#!/usr/bin/env python3
"""Second pass merge for one bridge: align the Capon / APES StaMPS points to the first-pass QC reference (off-deck ground point pairs,
per-date median, as make_combined.py), keep the first-pass QC dates, pick per location APES > Capon > first-pass PS, then rerun gw_post
for this bridge and replace its entry in the tile result.
usage: merge2.py <tile> <bridge_id>"""
import os, sys, json, fcntl, subprocess, numpy as np
from scipy.spatial import cKDTree
T, BID = sys.argv[1], sys.argv[2]
E = os.environ['BIM_DATA']; R2 = os.environ['BIM_WORK'] + '/run2/' + BID; RES = E + '/res/%s.json' % T
tile = [t for t in json.load(open(E + '/tiles.json', encoding='utf-8')) if t['tile'] == T][0]
bl = [b for b in tile['bridges'] if b['id'] == BID]
if not bl: print(BID, 'not in tile bridge list any more (registry update)'); sys.exit(0)
br = bl[0]
KE = 111320 * np.cos(np.radians(br['lat']))
xy = lambda lo, la: np.c_[(np.asarray(lo, float) - br['lon']) * KE, (np.asarray(la, float) - br['lat']) * 111320.0]
G = xy(*np.array([p for g in br['geo'] for p in g]).T) if br['geo'] else np.zeros((1, 2))
gt = cKDTree(G)
e1 = np.load(E + '/exports/%s_stamps_qc.npz' % T, allow_pickle=True); d1 = [str(x) for x in e1['dates']]
P1 = xy(e1['lon'], e1['lat']); X1 = np.asarray(e1['disp_mm'], float)
parts = []; info = {}
for var, tag in (('apes', 'APES'), ('capon', 'Capon'), ('ds', 'DS')):
    f = R2 + '/%s/deliverables/stamps/stamps_export.npz' % var
    if not os.path.exists(f): info[var] = 'no StaMPS result'; continue
    e = np.load(f, allow_pickle=True); de = [str(x) for x in e['dates']]
    if not all(d in de for d in d1): info[var] = 'date mismatch'; continue
    X = np.asarray(e['disp_mm'], float)[:, [de.index(d) for d in d1]]; P = xy(e['lon'], e['lat'])
    ground = gt.query(P)[0] >= 30
    dd, ii = cKDTree(P1).query(P); pair = ground & (dd <= 12)
    if pair.sum() >= 5:
        off = np.nanmedian(X[pair] - X1[ii[pair]], axis=0); how = 'ground pairs %d' % pair.sum()
    elif ground.sum() >= 5:   # no first-pass PS nearby: deck relative to the surrounding ground of the crop
        loc1 = (cKDTree(P).query(P1)[0] <= 150) & (gt.query(P1)[0] >= 30)
        off = np.nanmedian(X[ground], axis=0) - (np.nanmedian(X1[loc1], axis=0) if loc1.sum() >= 3 else 0.0); how = 'local ground %d' % ground.sum()
    else:
        info[var] = 'no ground points for alignment'; continue
    X = X - off[None, :]
    parts.append((tag, np.asarray(e['lon'], float), np.asarray(e['lat'], float), np.asarray(e['coh'], float), X, P)); info[var] = '%d PS, %s' % (len(P), how)
if not parts:
    print(BID, 'second pass: nothing usable', info); json.dump(dict(bid=BID, info=info), open(R2 + '/merge2.json', 'w')); sys.exit(0)
# APES > Capon > first pass: a point is dropped when a higher-priority point lies within 3 m
keep = []; Pk = np.zeros((0, 2))
dsp = [p for p in parts if p[0] == 'DS']; parts = [p for p in parts if p[0] != 'DS']
for tag, lo, la, co, X, P in parts:
    m = np.ones(len(P), bool) if len(Pk) == 0 else cKDTree(Pk).query(P)[0] > 3.0
    keep.append((tag, lo[m], la[m], co[m], X[m])); Pk = np.vstack([Pk, P[m]])
m1 = cKDTree(Pk).query(P1)[0] > 3.0
keep.append(('PS', np.asarray(e1['lon'], float)[m1], np.asarray(e1['lat'], float)[m1], np.asarray(e1['coh'], float)[m1], X1[m1]))
Pk = np.vstack([Pk, P1[m1]])
for tag, lo, la, co, X, P in dsp:   # DS fills only where no PS (refocused or first pass) lies within 3 m
    m = np.ones(len(P), bool) if len(Pk) == 0 else cKDTree(Pk).query(P)[0] > 3.0
    keep.append((tag, lo[m], la[m], co[m], X[m]))
O = R2 + '/qc/deliverables/stamps'; os.makedirs(O, exist_ok=True)
np.savez(O + '/stamps_export.npz', lon=np.concatenate([k[1] for k in keep]), lat=np.concatenate([k[2] for k in keep]), coh=np.concatenate([k[3] for k in keep]),
         disp_mm=np.vstack([k[4] for k in keep]), dates=np.array(d1), src=np.concatenate([np.full(len(k[1]), k[0]) for k in keep]))
q = json.load(open(E + '/res/%s_qc.json' % T)); json.dump(q, open(R2 + '/qc/deliverables/qc_summary.json', 'w'))
env = dict(os.environ, GW_R=R2, GW_ONLY=BID, GW_OUT=R2 + '/post2.json')
subprocess.run(['python3', os.path.join(os.environ['BIM_HOME'], 'post.py'), T], env=env, check=True)
nb = json.load(open(R2 + '/post2.json', encoding='utf-8'))['bridges'][0]
with open(RES + '.lock', 'w') as lk:
    fcntl.flock(lk, fcntl.LOCK_EX)
    r = json.load(open(RES, encoding='utf-8'))
    for k, b in enumerate(r['bridges']):
        if b['id'] == BID:
            nb['pass1'] = dict(n_ps=b.get('n_ps', 0), node_cover=b.get('node_cover', 0), st=b.get('st'), level=b.get('level'))
            nb['pass2'] = dict(method='Capon·APES 재초점 (2배 오버샘플링) + DS 위상연결', info=info, n_src=nb.get('n_src'))
            r['bridges'][k] = nb
    json.dump(r, open(RES, 'w', encoding='utf-8'), ensure_ascii=False)
json.dump(dict(bid=BID, info=info, before=nb['pass1'], after=dict(n_ps=nb.get('n_ps'), node_cover=nb.get('node_cover'), st=nb.get('st'))), open(R2 + '/merge2.json', 'w'), ensure_ascii=False)
print(BID, br['n'], 'pass1', nb['pass1'], '-> pass2 n_ps', nb.get('n_ps'), 'cover', nb.get('node_cover'), nb.get('st'), nb.get('n_src'), info)
