#!/usr/bin/env python3
"""Second pass for a weak bridge: Capon (MVDR) and joint multi-image APES re-focusing, 2x oversampled, of a crop around the bridge
cut from its tile window (same algorithm as sr_psi_capon.py / apes_parallel.py: common stack covariance, diagonal loading,
López-Dekker & Mallorquí 2010 eq. 25-29 for APES). Writes StaMPS input trees <run2>/<bid>/{capon,apes}_in.
usage: refocus.py <tile> <bridge_id>"""
import os
for _v in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'): os.environ[_v] = '1'   # forked workers deadlock on inherited BLAS/OpenMP thread pools
import sys, json, glob, shutil, time, numpy as np
import multiprocessing as mp_
from numpy.lib.stride_tricks import sliding_window_view
from scipy.ndimage import zoom
T, BID = sys.argv[1], sys.argv[2]
E = os.environ['BIM_DATA']; WIN = E + '/win/' + T; OUT = os.environ['BIM_WORK'] + '/run2/' + BID; os.makedirs(OUT, exist_ok=True)
meta = json.load(open(WIN + '/meta.json'))
tile = [t for t in json.load(open(E + '/tiles.json', encoding='utf-8')) if t['tile'] == T][0]
br = [b for b in tile['bridges'] if b['id'] == BID][0]
ny, nx = meta['rows'][1] - meta['rows'][0], meta['cols'][1] - meta['cols'][0]
rdg = lambda n: np.fromfile(WIN + '/merged/geom_reference/%s.rdr' % n, np.float64).reshape(ny, nx)
lat, lon, hgt = rdg('lat'), rdg('lon'), rdg('hgt')
los = np.fromfile(WIN + '/merged/geom_reference/los.rdr', np.float32).reshape(2, ny, nx)
# crop: bridge geometry + 150 m, at least 64 az x 192 rg pixels (patch 32 x 64)
G0 = np.array([p for g in br['geo'] for p in g]) if br['geo'] else np.array([[br['lon'], br['lat']]])
KE = 111320 * np.cos(np.radians(br['lat'])); m = 150.0
S_, N_ = G0[:, 1].min() - m / 111320, G0[:, 1].max() + m / 111320; W_, E_ = G0[:, 0].min() - m / KE, G0[:, 0].max() + m / KE
mk = (lat >= S_) & (lat <= N_) & (lon >= W_) & (lon <= E_)
if mk.sum() < 50: sys.exit('bridge not inside window')
rr, cc = np.where(mk); cy, cx = (rr.min() + rr.max()) // 2, (cc.min() + cc.max()) // 2
ha, hr = max((rr.max() - rr.min()) // 2 + 1, 32), max((cc.max() - cc.min()) // 2 + 1, 96)
ya, yb = max(cy - ha, 0), min(cy + ha, ny); xa, xb = max(cx - hr, 0), min(cx + hr, nx)
if yb - ya < 32 or xb - xa < 64: sys.exit('crop too small %dx%d' % (yb - ya, xb - xa))
dates = sorted(os.path.basename(d) for d in glob.glob(WIN + '/merged/SLC/2*'))
S = np.stack([np.fromfile(WIN + '/merged/SLC/%s/%s.slc.full' % (d, d), np.complex64).reshape(ny, nx)[ya:yb, xa:xb] for d in dates])
D, NA, NR = S.shape; print(BID, br['n'], 'crop', S.shape, 'window rows', meta['rows'], flush=True)
# ------------------------------------------------ TOPS azimuth deramp with the reference burst ramp
sys.path.insert(0, os.path.join(os.environ.get('ISCE_HOME', ''), 'components'))
import isce  # noqa
from iscesys.Component.ProductManager import ProductManager
from isceobj.Sensor.TOPS.Sentinel1 import Sentinel1
pm = ProductManager(); pm.configure(); swath = pm.loadProduct(glob.glob(WIN + '/reference/IW*.xml')[0]); s1 = Sentinel1()
rows = np.arange(meta['rows'][0] + ya, meta['rows'][0] + yb); cols = np.arange(meta['cols'][0] + xa, meta['cols'][0] + xb)
ramp = np.ones((NA, NR), np.complex64)
if meta['mode'] == 'stream':
    segs = [(int(meta['burst']), np.ones(NA, bool), rows)]
else:   # v2 merged grid: burst_01 at line 0, burst_02 at line 1342, seam at the overlap middle (as fast_merge / gw_cut)
    mid = (1342 + 1507) // 2
    segs = [(1, rows < mid, rows), (2, rows >= mid, rows - 1342)]
for b, msk, yy in segs:
    if not msk.any(): continue
    Y, X = np.meshgrid(yy[msk].astype(float), cols.astype(float), indexing='ij')
    ramp[msk, :] = s1.computeRamp(swath.bursts[b - 1], position=(Y, X)).astype(np.complex64)
S *= ramp[None]
# ------------------------------------------------ spectral band, sub-apertures, steering vectors
Pa, Pr, beta = 32, 64, 0.1
Fa = np.zeros(Pa); Fr = np.zeros(Pr)
for i in range(0, NA - Pa + 1, Pa):
    for j in range(0, NR - Pr + 1, Pr):
        P = np.abs(np.fft.fft2(S[::7, i:i + Pa, j:j + Pr], axes=(1, 2))) ** 2; Fa += P.sum(axis=(0, 2)); Fr += P.sum(axis=(0, 1))
ka = np.fft.fftfreq(Pa, 1 / Pa).astype(int); kr = np.fft.fftfreq(Pr, 1 / Pr).astype(int)
def band(F, k, frac=0.15):
    o = np.argsort(k); Fs, ks = F[o], k[o]; keep = Fs > frac * Fs.max(); best = (0, 0); i = 0
    while i < len(keep):
        if keep[i]:
            j = i
            while j + 1 < len(keep) and keep[j + 1]: j += 1
            if j - i > best[1] - best[0]: best = (i, j)
            i = j + 1
        else: i += 1
    return ks[best[0]:best[1] + 1]
KA, KR = band(Fa, ka), band(Fr, kr)
Ma, Mr = max(len(KA) // 3, 3), max(len(KR) // 3, 4); M = Ma * Mr; nsa, nsr = len(KA) - Ma + 1, len(KR) - Mr + 1; K = nsa * nsr
Aa = np.exp(-2j * np.pi * np.outer(np.arange(Ma), np.arange(0, Pa, 0.5)) / Pa); Ar = np.exp(-2j * np.pi * np.outer(np.arange(Mr), np.arange(0, Pr, 0.5)) / Pr)
IA, IR = np.mod(KA, 2 * Pa), np.mod(KR, 2 * Pr)
A_uv = (Aa[:, :, None, None] * Ar[None, None, :, :]).transpose(1, 3, 0, 2).reshape(-1, M); UV = 4 * Pa * Pr
wa = np.hanning(2 * Pa + 2)[1:-1]; wr = np.hanning(2 * Pr + 2)[1:-1]; WINF = (wa[:, None] * wr[None, :]).astype(np.float32)
ai = list(range(0, NA - Pa + 1, Pa // 2)); ri = list(range(0, NR - Pr + 1, Pr // 2))
if ai[-1] != NA - Pa: ai.append(NA - Pa)
if ri[-1] != NR - Pr: ri.append(NR - Pr)


def patch(ij):
    i, j = ij
    Yb = np.fft.fft2(S[:, i:i + Pa, j:j + Pr], axes=(1, 2))[:, KA % Pa][:, :, KR % Pr]
    snaps = sliding_window_view(Yb, (Ma, Mr), axis=(1, 2)).reshape(D, K, M)
    R = np.einsum('dkm,dkn->mn', snaps, snaps.conj()) / (D * K); R += (beta * np.trace(R).real / M) * np.eye(M)
    B = np.linalg.inv(R); RA = A_uv @ B.T; den = np.einsum('um,um->u', A_uv.conj(), RA).real; C = (RA.conj() * A_uv) / den[:, None]
    Gm = np.zeros((M, D, UV), np.complex64); Zp = np.zeros((D, 2 * Pa, 2 * Pr), np.complex64); cap = np.zeros((D, UV), np.complex64)
    for mm in range(Ma):
        for nq in range(Mr):
            Zp[:] = 0; Zp[:, IA[mm:mm + nsa][:, None], IR[nq:nq + nsr][None, :]] = Yb[:, mm:mm + nsa, nq:nq + nsr]
            Z = (np.fft.ifft2(Zp, axes=(1, 2)) * (4 * Pa * Pr) / K).reshape(D, -1); Gm[mm * Mr + nq] = Z; cap += C[:, mm * Mr + nq][None, :] * Z
    B = B.astype(np.complex64); Ba = (B @ A_uv.T).T; aBa = np.einsum('um,um->u', A_uv.conj(), Ba).real
    ape = np.zeros((D, UV), np.complex64)
    for c0 in range(0, UV, 256):
        sl = slice(c0, min(UV, c0 + 256)); G = np.transpose(Gm[:, :, sl], (2, 0, 1)) * A_uv[sl][:, :, None]
        BG = np.einsum('mn,unk->umk', B, G); Tm = np.einsum('umk,uml->ukl', G.conj(), BG); Pm = D * np.eye(D, dtype=np.complex64)[None] - Tm
        cv = np.einsum('um,umk->uk', A_uv[sl].conj(), BG); x = np.linalg.solve(np.conj(np.transpose(Pm, (0, 2, 1))), cv.conj()[:, :, None])[:, :, 0]; cP = x.conj()
        ape[:, sl] = ((cv + np.einsum('uk,ukl->ul', cP, Tm)) / (aBa[sl] + np.einsum('uk,uk->u', cP, cv.conj()).real)[:, None]).T
    return i, j, cap.reshape(D, 2 * Pa, 2 * Pr) * WINF, ape.reshape(D, 2 * Pa, 2 * Pr) * WINF


jobs = [(i, j) for i in ai for j in ri]; t0 = time.time()
OC = np.zeros((D, 2 * NA, 2 * NR), np.complex64); OA = np.zeros_like(OC); CNT = np.zeros((2 * NA, 2 * NR), np.float32)
with mp_.get_context('fork').Pool(int(os.environ.get('NPROC', '6'))) as pool:
    for k, (i, j, c, a_) in enumerate(pool.imap_unordered(patch, jobs), 1):
        sl = (slice(None), slice(2 * i, 2 * i + 2 * Pa), slice(2 * j, 2 * j + 2 * Pr)); OC[sl] += c; OA[sl] += a_; CNT[sl[1:]] += WINF
print('patches %d, M %d, D %d, %.0f s' % (len(jobs), M, D, time.time() - t0), flush=True)
OC /= np.maximum(CNT, 1e-6); OA /= np.maximum(CNT, 1e-6)
# re-ramp is not needed: StaMPS works on interferograms with the master, and the same deramp was applied to every date
G2 = dict(hgt=zoom(hgt[ya:yb, xa:xb], 2, order=1), lat=zoom(lat[ya:yb, xa:xb], 2, order=1), lon=zoom(lon[ya:yb, xa:xb], 2, order=1),
          inc=zoom(los[0, ya:yb, xa:xb], 2, order=1), az=zoom(los[1, ya:yb, xa:xb], 2, order=1))
L2, W2 = 2 * NA, 2 * NR


def xmlw(path, w, l, dt, nb=1):
    open(path, 'w').write('<imageFile>\n  <property name="width"><value>%d</value></property>\n  <property name="length"><value>%d</value></property>\n'
                          '  <property name="data_type"><value>%s</value></property>\n  <property name="number_bands"><value>%d</value></property>\n</imageFile>' % (w, l, dt, nb))


VRT_TMPL = '''<VRTDataset rasterXSize="%d" rasterYSize="%d">
  <VRTRasterBand dataType="CFloat32" band="1" subClass="VRTRawRasterBand">
    <SourceFilename relativeToVRT="1">%s.slc.full</SourceFilename>
    <ByteOrder>LSB</ByteOrder><ImageOffset>0</ImageOffset><PixelOffset>8</PixelOffset><LineOffset>%d</LineOffset>
  </VRTRasterBand>
</VRTDataset>
'''


def write_stack(var, Z, G, Lx, Wx):
    o = OUT + '/%s_in' % var; gd = o + '/merged/geom_reference'; os.makedirs(gd, exist_ok=True)
    for n in ('hgt', 'lat', 'lon'):
        G[n][:Lx, :Wx].astype(np.float64).tofile(gd + '/%s.rdr' % n); xmlw(gd + '/%s.rdr.xml' % n, Wx, Lx, 'DOUBLE')
    np.concatenate([G['inc'][:Lx, :Wx].astype(np.float32).ravel(), G['az'][:Lx, :Wx].astype(np.float32).ravel()]).tofile(gd + '/los.rdr'); xmlw(gd + '/los.rdr.xml', Wx, Lx, 'FLOAT', 2)
    for k, d in enumerate(dates):
        od = o + '/merged/SLC/' + d; os.makedirs(od, exist_ok=True); Z[k].astype(np.complex64).tofile(od + '/%s.slc.full' % d)
        open(od + '/%s.slc.full.vrt' % d, 'w').write(VRT_TMPL % (Wx, Lx, d, Wx * 8))
    for n in ('baselines', 'reference'):
        if not os.path.exists(o + '/' + n): shutil.copytree(WIN + '/' + n, o + '/' + n)


for var, Z in (('capon', OC), ('apes', OA)): write_stack(var, Z, G2, L2, W2)
del OC, OA
# ------------------------------------------------ DS: phase linking (EMI, Ansari et al. 2018) on statistically homogeneous neighbours
# SHP: pixels in a 7 x 21 (az x rg) window whose amplitude time series pass a two-sample KS-like test (max CDF distance) against the centre.
A = np.abs(S); As = np.sort(A, axis=0); D_ = A.shape[0]; ha, hr = 3, 10; ks_crit = 1.36 * np.sqrt(2.0 / D_)
grid = np.linspace(0, 1, 33)[1:-1]
Q = np.quantile(A, grid, axis=0)                                  # amplitude quantiles per pixel (31 x NA x NR) as CDF proxy


def ds_row(y):
    out = np.zeros((D_, NR), np.complex64); gam = np.zeros(NR, np.float32); nsh = np.zeros(NR, np.int16)
    ya0, ya1 = max(0, y - ha), min(NA, y + ha + 1)
    for x in range(NR):
        xa0, xa1 = max(0, x - hr), min(NR, x + hr + 1)
        # CDF distance via quantile comparison: fraction of the centre quantiles lying below/above each neighbour's
        qc = Q[:, y, x]; qn = Q[:, ya0:ya1, xa0:xa1].reshape(len(grid), -1)
        dist = np.max(np.abs(np.mean(qn[:, None, :] <= qc[None, :, None], axis=0) - grid[:, None]), axis=0)
        m = dist <= ks_crit
        Zs = S[:, ya0:ya1, xa0:xa1].reshape(D_, -1)[:, m]; nsh[x] = Zs.shape[1]
        if Zs.shape[1] < 10: continue
        C = Zs @ Zs.conj().T / Zs.shape[1]; d = np.sqrt(np.real(np.diag(C))); C = C / np.outer(d, d)
        Gm = np.abs(C) + 1e-3 * np.eye(D_)
        try: w, V = np.linalg.eigh(np.linalg.inv(Gm) * C)
        except np.linalg.LinAlgError: continue
        th = np.angle(V[:, 0]); th = th - th[0]
        ph = np.angle(C) - (th[:, None] - th[None, :]); iu = np.triu_indices(D_, 1)
        gam[x] = np.abs(np.mean(np.exp(1j * ph[iu])))
        out[:, x] = np.mean(A[:, ya0:ya1, xa0:xa1].reshape(D_, -1)[:, m], axis=1).mean() * np.exp(1j * th)
    return y, out, gam, nsh


t1 = time.time(); ZD = np.zeros((D_, NA, NR), np.complex64); GAM = np.zeros((NA, NR), np.float32); NSH = np.zeros((NA, NR), np.int16)
with mp_.get_context('fork').Pool(int(os.environ.get('NPROC', '6'))) as pool:
    for y, o, g, nn in pool.imap_unordered(ds_row, range(NA)): ZD[:, y] = o; GAM[y] = g; NSH[y] = nn
dsm = (GAM >= 0.6) & (NSH >= 10)
ZD[:, ~dsm] = 0                                                   # only phase-linked DS pixels with temporal coherence >= 0.6 become StaMPS candidates
G1 = dict(hgt=hgt[ya:yb, xa:xb], lat=lat[ya:yb, xa:xb], lon=lon[ya:yb, xa:xb], inc=los[0, ya:yb, xa:xb], az=los[1, ya:yb, xa:xb])
write_stack('ds', ZD, G1, NA, NR)
print('DS phase linking: %d px with gamma >= 0.6 (median SHP %d), %.0f s' % (dsm.sum(), int(np.median(NSH[dsm])) if dsm.any() else 0, time.time() - t1), flush=True)
json.dump(dict(tile=T, bid=BID, crop=[int(ya), int(yb), int(xa), int(xb)], dates=len(dates), M=M, patches=len(jobs), ds_pixels=int(dsm.sum())), open(OUT + '/refocus.json', 'w'))
print('REFOCUS_DONE', BID, flush=True)
