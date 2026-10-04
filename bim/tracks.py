# -*- coding: utf-8 -*-
"""Sentinel-1 tracks (relative orbits) and bursts covering the municipality AOI, via ASF (asf_search)."""
import collections, datetime


def find(aoi_wkt, start='2018-06-01', end=None, min_dates=30):
    import asf_search as asf
    end = end or datetime.date.today().isoformat()
    r = asf.search(platform=['Sentinel-1'], processingLevel='SLC', beamMode='IW', intersectsWith=aoi_wkt, start=start, end=end)
    by = collections.defaultdict(set); t = {}
    for x in r:
        p = x.properties; k = (p['flightDirection'], p['pathNumber']); by[k].add(p['startTime'][:10].replace('-', '')); t.setdefault(k, p['startTime'][11:16])
    out = []
    for (d, path), ds in sorted(by.items()):
        for k, per in enumerate(periods(sorted(ds))):   # acquisition gaps > 1 year (e.g. descending 2022-2024) split the track
            if len(per) < min_dates: continue
            tag = '' if k == 0 and per[-1] == sorted(ds)[-1] else '_%s' % per[0][:4]
            out.append(dict(dir=d, path=int(path), n_dates=len(per), first=per[0], last=per[-1], utc=t[(d, path)], reference_date=reference(per),
                            start='%s-%s-%s' % (per[0][:4], per[0][4:6], per[0][6:]), end='%s-%s-%s' % (per[-1][:4], per[-1][4:6], per[-1][6:]), tag=tag))
    return out


def periods(dates, gap_days=365):
    d = [datetime.datetime.strptime(x, '%Y%m%d') for x in dates]; out = [[dates[0]]]
    for i in range(1, len(d)):
        if (d[i] - d[i - 1]).days > gap_days: out.append([])
        out[-1].append(dates[i])
    return out


def reference(dates):
    """Reference (master) date: the acquisition closest to the middle of the time span (short temporal baselines both ways)."""
    d = [datetime.datetime.strptime(x, '%Y%m%d') for x in dates]
    mid = d[0] + (d[-1] - d[0]) / 2
    return min(dates, key=lambda x: abs(datetime.datetime.strptime(x, '%Y%m%d') - mid))


def bursts(aoi_wkt, path, date):
    """Burst ids (subswath, burst index) and footprints of one acquisition over the AOI."""
    import asf_search as asf
    d0 = datetime.datetime.strptime(date, '%Y%m%d'); d1 = d0 + datetime.timedelta(days=1)
    r = asf.search(dataset='SLC-BURST', relativeOrbit=path, start=d0.isoformat(), end=d1.isoformat(), intersectsWith=aoi_wkt, polarization='VV')
    return [dict(sw=int(x.properties['burst']['subswath'][-1]), idx=x.properties['burst']['burstIndex'], full=x.properties['burst']['fullBurstID'],
                 poly=x.geometry['coordinates'][0]) for x in r]


def available_dates(aoi_wkt, path, start, end=None):
    if end and len(end) == 8: end = '%s-%s-%s' % (end[:4], end[4:6], end[6:])
    import asf_search as asf
    end = end or datetime.date.today().isoformat()
    c = collections.Counter()
    for attempt in range(4):   # CMR occasionally returns incomplete pages: repeat and keep the union
        try:
            r = asf.search(dataset='SLC-BURST', relativeOrbit=path, start=start, end=end, intersectsWith=aoi_wkt, polarization='VV')
        except Exception:
            continue
        cc = collections.Counter(x.properties['startTime'][:10].replace('-', '') for x in r)
        for k_, v_ in cc.items(): c[k_] = max(c[k_], v_)
        if len(cc) and len(cc) == len(c) and attempt >= 1: break
    if not c: return []
    full = max(c.values())
    return sorted(d for d, n in c.items() if n >= 0.8 * full)     # dates covering (almost) all AOI bursts
