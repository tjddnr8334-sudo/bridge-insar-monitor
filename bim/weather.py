# -*- coding: utf-8 -*-
"""Acquisition-time weather (ERA5 via the Open-Meteo archive API) per 0.25 deg cell: 2 m temperature (thermal-expansion
correction), relative humidity and 24 h precipitation. The value at the satellite pass time is interpolated between the two
surrounding hours (ascending ~09:2x UTC, descending ~21:3x UTC over Korea)."""
import json, os, time, urllib.request


def fetch(tl, utc_hhmm, out_dir, start='2018-06-01', end=None):
    end = end or time.strftime('%Y-%m-%d')
    hh, mm = int(utc_hhmm[:2]), int(utc_hhmm[3:5]); w = mm / 60.0
    os.makedirs(out_dir, exist_ok=True)
    cells = sorted({(round(t['clat'] * 4) / 4, round(t['clon'] * 4) / 4) for t in tl})
    for la, lo in cells:
        f = os.path.join(out_dir, 'T_%.2f_%.2f.json' % (la, lo))
        old = json.load(open(f)) if os.path.exists(f) else None
        url = ('https://archive-api.open-meteo.com/v1/archive?latitude=%.2f&longitude=%.2f&start_date=%s&end_date=%s'
               '&hourly=temperature_2m,relative_humidity_2m,precipitation&timezone=GMT' % (la, lo, start, end))
        for k in range(6):
            try:
                j = json.load(urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'bridge-insar-monitor'}), timeout=120)); break
            except Exception:
                time.sleep(30 * (k + 1))
        else:
            continue
        h = j['hourly']; acq = dict(old['acq']) if old else {}
        for i in range(len(h['time']) - 1):
            if not h['time'][i].endswith('T%02d:00' % hh): continue
            T0, T1 = h['temperature_2m'][i], h['temperature_2m'][i + 1]
            if T0 is None or T1 is None: continue
            acq[h['time'][i][:10].replace('-', '')] = dict(T=round((1 - w) * T0 + w * T1, 2), RH=h['relative_humidity_2m'][i],
                                                          P24=round(sum(x or 0 for x in h['precipitation'][max(0, i - 24):i + 1]), 1))
        json.dump(dict(lat=la, lon=lo, utc=utc_hhmm, acq=acq), open(f, 'w')); time.sleep(2)
    return len(cells)
