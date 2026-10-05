#!/usr/bin/env python3
"""Per-tile bridge results for the Gangwon-wide InSAR screening (PS only, after QC filter).
For each bridge of the tile: deck PS (OSM axis corridor) -> 3 m nodes -> vertical series (LOS / cos inc) -> thermal-expansion correction
(acquisition-time ERA5 2 m temperature, per node y = a + v t + kT dT) -> 15 m segments -> risk indicators V, ACC, DIFF, CUM -> level + confidence.
usage: post.py <tile>"""
import os, sys, json, datetime, numpy as np
T = sys.argv[1]
E = os.environ['BIM_DATA']; R = os.environ.get('GW_R', os.environ['BIM_WORK'] + '/run/' + T); ONLY = os.environ.get('GW_ONLY'); OUTF = os.environ.get('GW_OUT')
tile = [t for t in json.load(open(E + '/tiles.json', encoding='utf-8')) if t['tile'] == T][0]
if ONLY: tile['bridges'] = [b for b in tile['bridges'] if b['id'] == ONLY]
meta = json.load(open(E + '/win/%s/meta.json' % T))
qcs = json.load(open(R + '/qc/deliverables/qc_summary.json'))
out = dict(tile=T, qc=dict((k, qcs.get(k)) for k in ('status', 'n_dates', 'n_kept', 'refcoh_median', 'reference', 'n_candidates_coh095', 'cmin', 'gmin')), bridges=[])
out['qc']['drop_bperp'] = len(qcs.get('dropped_baseline', [])); out['qc']['drop_refcoh'] = len(qcs.get('dropped_refcoh', []))
IND = dict(V=[1, 2, 4], ACC=[1, 2, 3], DIFF=[1, 2, 3], CUM=[10, 20, 30])
LV = ['정상', '관심', '주의', '경고']
# ---- allowable displacement (InSAR = displacement since the first acquisition, relative to the surrounding ground)
import yaml
_CR = yaml.safe_load(open(os.environ['BIM_CRITERIA'], encoding='utf-8')) if os.environ.get('BIM_CRITERIA') else None
S_ALLOW = float(_CR['allowable']['total_settlement_mm']) if _CR else 25.0   # mm, inframon life.limits settlement_mm
SPAN = dict(_CR['allowable']['typical_span_m']) if _CR else {'RC슬래브교': 10, '라멘교': 15, 'PSCI거더교': 30, 'PSC박스거더교': 50, '강박스거더교': 45, 'default': 25}
SPAN_DEF = SPAN.pop('default', 25)
CONT = set(_CR['allowable']['continuous_types']) if _CR else {'라멘교', 'PSC박스거더교', '강박스거더교', '사장교', '현수교', '아치교', '트러스교'}
BETA_C = _CR["allowable"]["angular_distortion"]["continuous"] if _CR else 0.002
BETA_S = _CR['allowable']['angular_distortion']['simple'] if _CR else 0.002
RTH = dict(_CR['ratio_thresholds']) if _CR else {'1종': [0.3, 0.5, 0.7], '2종': [0.4, 0.6, 0.8], '3종': [0.5, 0.7, 0.9], '기타': [0.5, 0.7, 0.9]}
PROJ_Y = float(_CR['projection_years']) if _CR else 10.0
def rlev(r, th): return 3 if r >= th[2] else 2 if r >= th[1] else 1 if r >= th[0] else 0


def finish(b, reason):
    b.update(level=-1, st='판정불가', reason=reason); out['bridges'].append(b)


if qcs.get('status') != 'OK':
    for br in tile['bridges']:
        finish(dict(id=br['id'], n=br['n'], c=br['c'], lat=br['lat'], lon=br['lon'], len=br['len']), '기준점 결맞음 0.95 이상 PS 없음')
    json.dump(out, open(E + '/res/%s.json' % T, 'w', encoding='utf-8'), ensure_ascii=False); sys.exit(0)
e = np.load(R + '/qc/deliverables/stamps/stamps_export.npz', allow_pickle=True)
dates = [str(x) for x in e['dates']]; inc = meta['inc_mean']; COS = np.cos(np.radians(inc))
lon, lat, coh = np.asarray(e['lon'], float), np.asarray(e['lat'], float), np.asarray(e['coh'], float)
SRC = np.asarray(e['src']).astype(str) if 'src' in e.files else np.full(len(lon), 'PS')
Y = np.asarray(e['disp_mm'], float) / COS
M0 = datetime.datetime.strptime(os.environ.get('BIM_MASTER', '20220325'), '%Y%m%d')
t = np.array([(datetime.datetime.strptime(d, '%Y%m%d') - M0).days / 365.25 for d in dates])
cell = '%s/temps/T_%.2f_%.2f.json' % (E, round(tile['clat'] * 4) / 4, round(tile['clon'] * 4) / 4)
acq = json.load(open(cell))['acq']; Tc = np.array([acq[d]['T'] if d in acq else np.nan for d in dates]); Tc = np.where(np.isfinite(Tc), Tc, np.nanmean(Tc))
dT = Tc - Tc.mean()
AS = np.c_[np.ones_like(t), t, np.sin(2 * np.pi * t), np.cos(2 * np.pi * t)]; AT = np.c_[np.ones_like(t), t, dT]
tEnd = t.max(); cut = tEnd - 2; span = t.max() - t.min()


def vel(y, m=None, se=False):
    m = np.isfinite(y) if m is None else (m & np.isfinite(y))
    if m.sum() < 12: return (np.nan, np.nan) if se else np.nan
    A = AS[m]; c, r, *_ = np.linalg.lstsq(A, y[m], rcond=None)
    if not se: return float(c[1])
    s2 = float(np.sum((y[m] - A @ c) ** 2) / max(m.sum() - 4, 1))   # residual variance -> standard error of the velocity
    return float(c[1]), float(np.sqrt(s2 * np.linalg.inv(A.T @ A)[1, 1]))


def proxy(b, br, s, c, Lb, Wb):
    """Tier 3: no usable deck scatterer -> abutment / approach ground (10-100 m beyond each end, within 30 m of the axis) relative to the
    100-300 m ring; judged with the same allowable values, flagged as a proxy (reference grade)."""
    dg = np.hypot(np.maximum(np.abs(s) - Lb / 2, 0), np.maximum(np.abs(c) - Wb / 2, 0)); okY = np.isfinite(Y).all(1)
    ring = okY & (dg >= 100) & (dg <= 500)
    if ring.sum() < 5: return False
    Yr = np.nanmedian(Y[ring], axis=0); vr = np.linalg.lstsq(AS, Y[ring].T, rcond=None)[0][1]
    sig_r = 1.4826 * np.median(np.abs(vr - np.median(vr)))
    ends = []
    for sgn in (-1, 1):
        m = okY & (np.abs(c) <= 50) & (sgn * s >= Lb / 2 + 10) & (sgn * s <= Lb / 2 + 200)
        if m.sum() < 1: continue
        yz = np.nanmedian(Y[m], axis=0) - Yr; vz, sz = vel(yz, se=True); thr = max(2 * sig_r, 2 * sz)
        sg = bool(abs(vz) > thr); ends.append(dict(end='시점' if sgn < 0 else '종점', n=int(m.sum()), v=round(vz, 2), sig=sg, _y=yz))
    if not ends: return False
    ym = np.nanmean([e_.pop('_y') for e_ in ends], axis=0); rnd = lambda a_: [None if not np.isfinite(x) else round(float(x), 1) for x in a_]
    pts = dict(ts=rnd(ym), ts_vert=rnd(ym), ts_los=rnd(ym * COS), inc=round(float(inc), 2))
    rg = br.get('reg') or {}; typ = rg.get('type') or ''; cls = rg.get('cls') or '기타'
    Tobs = float(t.max() - t.min())
    Dn = max((abs(e['v']) * Tobs if e['sig'] else 0.0) for e in ends); D10 = max((abs(e['v']) * (Tobs + PROJ_Y) if e['sig'] else 0.0) for e in ends)
    span = float(min(SPAN.get(typ, SPAN_DEF), max(Lb, 1.0))); bet = BETA_C if (typ in CONT or not typ) else BETA_S; bn = b10 = 0.0
    if len(ends) == 2 and (ends[0]['sig'] or ends[1]['sig']):
        dv = abs(ends[1]['v'] - ends[0]['v']); bn = dv * Tobs / 1000 / max(Lb, 1); b10 = dv * (Tobs + PROJ_Y) / 1000 / max(Lb, 1)
    r_now = max(Dn / S_ALLOW, bn / bet); r_10 = max(D10 / S_ALLOW, b10 / bet); th = RTH.get(cls, RTH['기타'])
    lnow = rlev(r_now, th); level = max(lnow, min(rlev(r_10, th), lnow + 1))
    b.update(level=int(level), st=LV[level], conf='낮음', proxy=True, items={'V': None, 'ACC': None, 'DIFF': None, 'CUM': None}, lv={}, sig={},
             kT_med=None, std_raw=None, std_corr=None, v_nodes=[], where={}, **pts,
             allow=dict(cls=cls, type=typ or '미상', span=span, beta_allow=bet, S_allow=S_ALLOW, Tobs=round(Tobs, 1), D_now=round(Dn, 1), D_10y=round(D10, 1),
                        beta_now=round(bn, 6), beta_10y=round(b10, 6), r_now=round(r_now, 3), r_10y=round(r_10, 3), th=th, zones=ends, review=True,
                        note='교량 본체 산란체 없음 → 교대부(양 끝 10~200 m) 지반 거동으로 대체 판정 (참고)', year=rg.get('year'), grade=rg.get('grade'), insp=rg.get('insp')))
    out['bridges'].append(b); return True


for br in tile['bridges']:
    b = dict(id=br['id'], n=br['n'], c=br['c'], lat=br['lat'], lon=br['lon'], len=br['len'])
    G = np.array([p for g in br['geo'] for p in g]) if br['geo'] else np.array([[br['lon'], br['lat']]])
    KE = 111320 * np.cos(np.radians(br['lat']))
    g = np.c_[(G[:, 0] - br['lon']) * KE, (G[:, 1] - br['lat']) * 111320.0]
    if len(g) < 2: finish(b, 'OSM 형상 없음'); continue
    u, s_, vt = np.linalg.svd(g - g.mean(0)); ax = vt[0] if vt[0][1] >= 0 else -vt[0]; cr = np.array([-ax[1], ax[0]])
    sp, cp = g @ ax, g @ cr; c0 = ax * (sp.min() + sp.max()) / 2 + cr * (cp.min() + cp.max()) / 2
    Lb = max(float(np.ptp(sp)), br['len']); Wb = float(np.ptp(cp)) if br['poly'] else (br['width'] or 10.0) + float(np.ptp(cp))
    Wb = min(max(Wb, 6.0), 40.0)
    P = np.c_[(lon - br['lon']) * KE, (lat - br['lat']) * 111320.0] - c0
    s, c = P @ ax, P @ cr
    on = (np.abs(c) <= Wb / 2 + 6.0) & (np.abs(s) <= Lb / 2 + 3.0) & np.isfinite(Y).all(1)
    b['n_src'] = {k: int(((SRC == k) & on).sum()) for k in np.unique(SRC)}
    b.update(width=round(Wb, 1), n_ps=int(on.sum()), bearing=round(float(np.degrees(np.arctan2(ax[0], ax[1])) % 180), 1))
    ns = np.arange(-Lb / 2, Lb / 2 + 1e-6, 3.0); nodes = []
    for x in ns:
        k = on & (np.abs(s - x) <= 3.0)
        nodes.append(np.nanmedian(Y[k], axis=0) if k.any() else None)
    cov = np.mean([n is not None for n in nodes]); b['node_cover'] = round(float(cov), 2); b['n_nodes'] = len(ns)
    if on.sum() == 0:
        if not proxy(b, br, s, c, Lb, Wb): finish(b, '교량 위·교대부 모두 산란체 없음')
        continue
    kT, Yc, vn = [], [], []
    for y in nodes:
        if y is None: kT.append(np.nan); Yc.append(np.full(len(t), np.nan)); vn.append(np.nan); continue
        m = np.isfinite(y); cc = np.linalg.lstsq(AT[m], y[m], rcond=None)[0]; yc = y - cc[2] * dT
        kT.append(cc[2]); Yc.append(yc); vn.append(vel(yc))
    Yc = np.array(Yc); kT = np.array(kT)
    # ground reference ring (30-300 m from the deck outline): regional motion and the realistic noise level of this tile
    dg = np.hypot(np.maximum(np.abs(s) - Lb / 2, 0), np.maximum(np.abs(c) - Wb / 2, 0))
    gm = (dg >= 30) & (dg <= 300) & np.isfinite(Y).all(1)
    if gm.sum() >= 8:
        Yg = Y[gm]; cg = np.linalg.lstsq(AS, Yg.T, rcond=None)[0][1]
        cg2 = np.linalg.lstsq(AS[t >= cut], Yg[:, t >= cut].T, rcond=None)[0][1]; cg1 = np.linalg.lstsq(AS[t < cut], Yg[:, t < cut].T, rcond=None)[0][1]
        ag = cg2 - cg1; mad = lambda x: 1.4826 * np.median(np.abs(x - np.median(x)))
        G = dict(n=int(gm.sum()), v_med=float(np.median(cg)), v_sig=float(mad(cg)), a_med=float(np.median(ag)), a_sig=float(mad(ag)))
    else:
        G = None
    b['ground'] = None if G is None else {k: (round(v, 3) if isinstance(v, float) else v) for k, v in G.items()}
    # 15 m segments; indicators are deck motion RELATIVE to the surrounding ground, significant only beyond 2x the ground scatter
    seg = []
    for a0 in np.arange(-Lb / 2, Lb / 2, 15.0):
        k = (ns >= a0) & (ns < a0 + 15.0) & np.array([np.isfinite(r).any() for r in Yc])
        if not k.any(): seg.append(None); continue
        ys = np.nanmean(Yc[k], axis=0)
        v, sv_ = vel(ys, se=True); v2, s2_ = vel(ys, t >= cut, se=True); v1, s1_ = vel(ys, t < cut, se=True)
        acc, sacc = v2 - v1, float(np.hypot(s2_, s1_))
        if G is not None:
            vr, ar = v - G['v_med'], acc - G['a_med']; tv, ta = max(2 * G['v_sig'], 2 * sv_), max(2 * G['a_sig'], 2 * sacc)
        else:
            vr, ar = v, acc; tv, ta = 2 * sv_, 2 * sacc
        seg.append(dict(s0=round(float(a0), 1), v_abs=round(v, 2), v=round(vr, 2), v_sig=bool(abs(vr) > tv), v_thr=round(tv, 2),
                        acc=round(ar, 2) if np.isfinite(ar) else None, acc_sig=bool(np.isfinite(ar) and abs(ar) > ta), acc_thr=round(ta, 2) if np.isfinite(ta) else None,
                        n=int(k.sum())))
    sv = [x for x in seg if x]
    V = max(sv, key=lambda x: abs(x['v'])); accs = [x for x in sv if x['acc'] is not None]
    A_ = max(accs, key=lambda x: abs(x['acc'])) if accs else None
    D_ = 0.0; Dw = None
    for k in range(len(seg) - 1):
        if seg[k] and seg[k + 1] and abs(seg[k]['v'] - seg[k + 1]['v']) > D_: D_ = abs(seg[k]['v'] - seg[k + 1]['v']); Dw = (seg[k]['s0'], seg[k + 1]['s0'])
    dthr = 2 * np.sqrt(2) * (G['v_sig'] if G else max(x['v_thr'] for x in sv) / 2)
    items = dict(V=abs(V['v']), ACC=abs(A_['acc']) if A_ else np.nan, DIFF=D_ if len(sv) > 1 else np.nan, CUM=abs(V['v']) * span)
    sig = dict(V=V['v_sig'], ACC=bool(A_ and A_['acc_sig']), DIFF=bool(D_ > dthr), CUM=V['v_sig'])
    sigv = [x for x in sv if x['v_sig']]
    if sigv:   # V / CUM from the largest significant relative segment velocity
        Vs = max(sigv, key=lambda x: abs(x['v'])); V = Vs; items['V'] = abs(Vs['v']); items['CUM'] = abs(Vs['v']) * span; sig['V'] = sig['CUM'] = True
    sacc = [x for x in sv if x['acc'] is not None and x['acc_sig']]
    if sacc: A_ = max(sacc, key=lambda x: abs(x['acc'])); items['ACC'] = abs(A_['acc']); sig['ACC'] = True
    lv = {k: (-1 if not np.isfinite(v) else 0 if not sig[k] else (3 if v >= IND[k][2] else 2 if v >= IND[k][1] else 1 if v >= IND[k][0] else 0)) for k, v in items.items()}
    # ---- allowable-displacement judgement
    rg = br.get('reg') or {}; typ = rg.get('type') or ''; cls = rg.get('cls') or '기타'
    span = float(min(SPAN.get(typ, SPAN_DEF), max(Lb, 1.0))); bet = BETA_C if (typ in CONT or not typ) else BETA_S
    gts = np.nanmedian(Y[gm], axis=0) if G is not None else np.zeros(len(t))       # ground series of the ring (removed: relative motion)
    Tobs = float(t.max() - t.min()); vthr = 2 * G['v_sig'] if G is not None else None
    sup = np.arange(-Lb / 2, Lb / 2 + 1e-6, span); sup = sup if len(sup) >= 2 else np.array([-Lb / 2, Lb / 2])
    zones = []
    for x in sup:
        k = (np.abs(ns - x) <= max(4.5, span / 4)) & np.array([np.isfinite(r_).any() for r_ in Yc])
        if not k.any(): zones.append(None); continue
        yz = np.nanmean(Yc[k], axis=0) - gts; vz, sz = vel(yz, se=True)
        thr = max(vthr, 2 * sz) if vthr is not None else 2 * sz
        sigz = bool(np.isfinite(vz) and abs(vz) > thr)
        zones.append(dict(s=round(float(x), 1), v=round(vz, 2), sig=sigz, D=round(abs(vz) * Tobs, 1) if sigz else 0.0, D10=round(abs(vz) * (Tobs + PROJ_Y), 1) if sigz else 0.0))
    zz = [z for z in zones if z]
    if not zz:
        if not proxy(b, br, s, c, Lb, Wb): finish(b, '지점부·교대부 산란체 없음')
        continue
    Dn = max(z['D'] for z in zz); D10 = max(z['D10'] for z in zz)
    bn = b10 = 0.0; bw = None
    for k in range(len(zones) - 1):
        z0, z1 = zones[k], zones[k + 1]
        if not (z0 and z1): continue
        dv = (z1['v'] - z0['v']) if (z0['sig'] or z1['sig']) else 0.0
        x = abs(dv) * Tobs / 1000.0 / span; x10 = abs(dv) * (Tobs + PROJ_Y) / 1000.0 / span
        if x > bn: bn, bw = x, (z0['s'], z1['s'])
        b10 = max(b10, x10)
    r_now = max(Dn / S_ALLOW, bn / bet); r_10 = max(D10 / S_ALLOW, b10 / bet)
    th = RTH.get(cls, RTH['기타']); lnow = rlev(r_now, th); level = max(lnow, min(rlev(r_10, th), lnow + 1))
    review = bool(level >= 2 and (on.sum() < 5 or cov < 0.25 or G is None))
    b['allow'] = dict(cls=cls, type=typ or '미상', span=span, beta_allow=bet, S_allow=S_ALLOW, Tobs=round(Tobs, 1), D_now=round(Dn, 1), D_10y=round(D10, 1),
                      beta_now=round(bn, 6), beta_10y=round(b10, 6), beta_where=bw, r_now=round(r_now, 3), r_10y=round(r_10, 3), th=th, zones=zones, review=review,
                      year=rg.get('year'), grade=rg.get('grade'), insp=rg.get('insp'))
    if Lb < 20: b['allow']['note'] = '연장 20 m 미만: Sentinel-1 해상도 한계 (참고)'
    conf = '높음' if cov >= 0.5 else '보통' if cov >= 0.25 else '낮음'
    if G is None: conf = '낮음'
    ok = np.isfinite(Yc).any(1)
    raw_sd = [np.nanstd(n - AT[:, :2] @ np.linalg.lstsq(AT[np.isfinite(n)], n[np.isfinite(n)], rcond=None)[0][:2]) for n in nodes if n is not None]
    cor_sd = [np.nanstd(Yc[i] - AS[:, :2] @ np.linalg.lstsq(AS[np.isfinite(Yc[i])][:, :2], Yc[i][np.isfinite(Yc[i])], rcond=None)[0]) for i in np.where(ok)[0]]
    mean_ts = np.nanmean(Yc[ok], axis=0)
    # series for the program: bridge mean relative to the surrounding ground ring
    #   ts_los   : LOS [mm] (raw, before vertical conversion and thermal correction)
    #   ts_vert  : vertical [mm] = LOS / cos(incidence), before thermal correction
    #   ts       : vertical [mm] after thermal-expansion correction (used for the judgement)
    raw_mean = np.nanmean(np.array([n for n in nodes if n is not None]), axis=0)
    rnd = lambda a: [None if not np.isfinite(x) else round(float(x), 1) for x in a]
    b['ts_vert'] = rnd(raw_mean - gts); b['ts_los'] = rnd((raw_mean - gts) * COS); b['inc'] = round(float(inc), 2)
    b['zone_ts'] = [dict(s=z['s'], ts=rnd(np.nanmean(Yc[(np.abs(ns - z['s']) <= max(4.5, span / 4)) & ok], axis=0) - gts)) for z in zz] if 'zz' in dir() else []
    b.update(level=int(level), st=LV[level], conf=conf, items={k: (round(float(v), 2) if np.isfinite(v) else None) for k, v in items.items()}, lv=lv, sig=sig, segs=sv,
             where=dict(V=V['s0'], ACC=A_['s0'] if A_ else None, DIFF=Dw), kT_med=round(float(np.nanmedian(kT)), 3),
             std_raw=round(float(np.median(raw_sd)), 2), std_corr=round(float(np.median(cor_sd)), 2),
             v_nodes=[None if not np.isfinite(x) else round(float(x), 2) for x in vn], ts=[None if not np.isfinite(x) else round(float(x), 1) for x in (mean_ts - gts)])
    out['bridges'].append(b)
out['dates'] = dates; out['inc'] = round(inc, 2); out['cell'] = os.path.basename(cell)
os.makedirs(E + '/res', exist_ok=True)
json.dump(out, open(OUTF or (E + '/res/%s.json' % T), 'w', encoding='utf-8'), ensure_ascii=False)
print(T, [(b['n'], b.get('st'), b.get('n_ps'), b.get('items', {}).get('V')) for b in out['bridges']])
