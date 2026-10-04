#!/usr/bin/env python3
"""Streaming window cutter for a 2-burst gw stack: as soon as a date is resampled, cut every assigned tile window from its
coregistered burst, then delete the burst binaries (disk stays small). Writes stamps_in layout to $BIM_DATA/win/<tile>/.
usage: stream_cut.py <stack_dir> <SW>"""
import os, sys, json, glob, time, shutil, numpy as np
from osgeo import gdal
gdal.UseExceptions()
ST, SW = sys.argv[1], int(sys.argv[2]); IW = 'IW%d' % SW
WORK = os.path.dirname(ST); LOG = WORK + '/logs'; E = os.environ['BIM_DATA']; WIN = E + '/win'; CS = ST + '/coreg_secondarys'
while not os.path.exists(LOG + '/GEOM_READY'): time.sleep(30)
tiles = json.load(open(E + '/tiles.json', encoding='utf-8'))


def rd(p, x0=0, y0=0, nx=None, ny=None, band=1):
    ds = gdal.Open(p + '.vrt' if os.path.exists(p + '.vrt') else p)
    nx = nx or ds.RasterXSize - x0; ny = ny or ds.RasterYSize - y0
    return ds.GetRasterBand(band).ReadAsArray(x0, y0, nx, ny)


def xmlw(path, w, l, dt, nb=1):
    open(path, 'w').write('<imageFile>\n  <property name="width"><value>%d</value></property>\n  <property name="length"><value>%d</value></property>\n'
                          '  <property name="data_type"><value>%s</value></property>\n  <property name="number_bands"><value>%d</value></property>\n</imageFile>' % (w, l, dt, nb))


def slcvrt(path, w, l, raw):
    open(path, 'w').write('<VRTDataset rasterXSize="%d" rasterYSize="%d">\n  <VRTRasterBand dataType="CFloat32" band="1" subClass="VRTRawRasterBand">\n'
                          '    <SourceFilename relativeToVRT="1">%s</SourceFilename>\n    <ByteOrder>LSB</ByteOrder><ImageOffset>0</ImageOffset><PixelOffset>8</PixelOffset><LineOffset>%d</LineOffset>\n'
                          '  </VRTRasterBand>\n</VRTDataset>\n' % (w, l, raw, w * 8))


bursts = sorted(os.path.basename(p)[6:8] for p in glob.glob(ST + '/reference/%s/burst_*.slc.vrt' % IW))
G = {b: dict(lat=rd(ST + '/geom_reference/%s/lat_%s.rdr' % (IW, b)), lon=rd(ST + '/geom_reference/%s/lon_%s.rdr' % (IW, b))) for b in bursts}
ref = os.path.basename(glob.glob(ST + '/baselines/*_*')[0]).split('_')[0]
plan = []
sub = 8
RESUME = os.path.exists(LOG + '/cut_plan.json')
if RESUME:
    plan = json.load(open(LOG + '/cut_plan.json')); tiles = []
for t in tiles:
    o = WIN + '/' + t['tile']
    if os.path.isdir(o): continue   # cut or being cut by another cutter
    best = None
    for b in bursts:
        lat, lon = G[b]['lat'], G[b]['lon']; L, W = lat.shape
        m = (lat[::sub, ::sub] >= t['S']) & (lat[::sub, ::sub] <= t['N']) & (lon[::sub, ::sub] >= t['W']) & (lon[::sub, ::sub] <= t['E'])
        if m.sum() < 4: continue
        rr, cc = np.where(m); y0, y1 = max(rr.min() * sub - sub, 0), min(rr.max() * sub + sub, L); x0, x1 = max(cc.min() * sub - sub, 0), min(cc.max() * sub + sub, W)
        mm = (lat[y0:y1, x0:x1] >= t['S']) & (lat[y0:y1, x0:x1] <= t['N']) & (lon[y0:y1, x0:x1] >= t['W']) & (lon[y0:y1, x0:x1] <= t['E'])
        rr, cc = np.where(mm); y0, y1, x0, x1 = y0 + rr.min(), y0 + rr.max() + 1, x0 + cc.min(), x0 + cc.max() + 1
        exp_px = ((t['N'] - t['S']) * 111320 / 13.9) * ((t['E'] - t['W']) * 111320 * np.cos(np.radians(t['clat'])) / 2.33) * 0.62
        frac = mm.sum() / exp_px
        r = rd(ST + '/reference/%s/burst_%s.slc' % (IW, b), x0, y0, x1 - x0, y1 - y0); valid = np.count_nonzero(r) / r.size
        hg = rd(ST + '/geom_reference/%s/hgt_%s.rdr' % (IW, b), x0, y0, x1 - x0, y1 - y0)
        if (hg <= -400).mean() > 0.001: continue
        sc = min(frac, 1.0) * valid
        if best is None or sc > best[0]: best = (sc, b, int(y0), int(y1), int(x0), int(x1), frac, valid)
    if best and best[0] >= 0.75:
        plan.append(dict(tile=t['tile'], b=best[1], y0=best[2], y1=best[3], x0=best[4], x1=best[5], frac=best[6], valid=best[7]))
print('tiles planned' if not RESUME else 'resumed plan', len(plan), {b: sum(p['b'] == b for p in plan) for b in bursts}, flush=True)
json.dump(plan, open(LOG + '/cut_plan.json', 'w'))
for p in plan: os.makedirs(WIN + '/' + p['tile'], exist_ok=True); open(WIN + '/' + p['tile'] + '/CLAIM_stream', 'w').write(ST)
# geometry, baselines, reference date
for p in ([] if RESUME else plan):
    o = WIN + '/' + p['tile']; os.makedirs(o + '/merged/geom_reference', exist_ok=True); nx, ny = p['x1'] - p['x0'], p['y1'] - p['y0']
    gp = lambda n: ST + '/geom_reference/%s/%s_%s.rdr' % (IW, n, p['b'])
    for n in ('hgt', 'lat', 'lon'):
        rd(gp(n), p['x0'], p['y0'], nx, ny).astype(np.float64).tofile(o + '/merged/geom_reference/%s.rdr' % n); xmlw(o + '/merged/geom_reference/%s.rdr.xml' % n, nx, ny, 'DOUBLE')
    b1 = rd(gp('los'), p['x0'], p['y0'], nx, ny, 1).astype(np.float32); b2 = rd(gp('los'), p['x0'], p['y0'], nx, ny, 2).astype(np.float32)
    np.concatenate([b1.ravel(), b2.ravel()]).tofile(o + '/merged/geom_reference/los.rdr'); xmlw(o + '/merged/geom_reference/los.rdr.xml', nx, ny, 'FLOAT', 2)
    p['inc'] = float(np.nanmean(b1[b1 > 0]))
    if os.path.exists(o + '/baselines'): shutil.rmtree(o + '/baselines')
    shutil.copytree(ST + '/baselines', o + '/baselines'); os.makedirs(o + '/reference', exist_ok=True); shutil.copy(ST + '/reference/%s.xml' % IW, o + '/reference/')


def cut_date(d, src):
    cache = {}
    for p in plan:
        o = WIN + '/' + p['tile']; nx, ny = p['x1'] - p['x0'], p['y1'] - p['y0']
        if p['b'] not in cache:
            f = src(p['b'])
            if os.path.exists(f) and not f.endswith('reference/%s/burst_%s.slc' % (IW, p['b'])):
                ds = gdal.Open(f + '.vrt' if os.path.exists(f + '.vrt') else f)
                cache[p['b']] = np.fromfile(f, np.complex64).reshape(ds.RasterYSize, ds.RasterXSize)
            else:
                cache[p['b']] = rd(f)
        a = cache[p['b']][p['y0']:p['y1'], p['x0']:p['x1']]
        if np.count_nonzero(a) < 0.9 * nx * ny * p['valid']: continue
        od = '%s/merged/SLC/%s' % (o, d); os.makedirs(od, exist_ok=True)
        a.astype(np.complex64).tofile(od + '/%s.slc.full' % d); slcvrt(od + '/%s.slc.full.vrt' % d, nx, ny, '%s.slc.full' % d)


if not RESUME: cut_date(ref, lambda b: ST + '/reference/%s/burst_%s.slc' % (IW, b))
n = 0
while True:
    ready = [d for d in sorted(os.listdir(CS)) if os.path.exists('%s/%s/resampled' % (CS, d)) and not os.path.exists('%s/%s/cut.done' % (CS, d))]
    for d in ready:
        cut_date(d, lambda b, d=d: '%s/%s/%s/burst_%s.slc' % (CS, d, IW, b))
        for f in glob.glob('%s/%s/%s/burst_*.slc' % (CS, d, IW)): os.remove(f)
        open('%s/%s/cut.done' % (CS, d), 'w').write('ok'); os.remove('%s/%s/resampled' % (CS, d)); n += 1
        if n % 10 == 0: print('cut dates', n, time.strftime('%H:%M'), flush=True)
    if not ready:
        if os.path.exists(LOG + '/STACK_DONE'): break
        time.sleep(30)
for p in plan:
    o = WIN + '/' + p['tile']; nd = len(glob.glob(o + '/merged/SLC/2*'))
    json.dump(dict(tile=p['tile'], mode='stream', burst=p['b'], rows=[p['y0'], p['y1']], cols=[p['x0'], p['x1']], n_dates=nd, inc_mean=p.get('inc') or float(np.nanmean([v for v in np.fromfile(o + '/merged/geom_reference/los.rdr', np.float32)[:(p['x1'] - p['x0']) * (p['y1'] - p['y0'])] if v > 0])), valid=p['valid'],
                   area_frac=p['frac'], stack=ST), open(o + '/meta.json', 'w'))
    if nd >= 60: open(o + '/CUT_DONE', 'w').write('ok')
print('CUT_FINISHED stream', len(plan), 'dates', n, flush=True)
