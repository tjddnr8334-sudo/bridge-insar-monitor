# -*- coding: utf-8 -*-
"""Bridge list of a municipality from 국토교통부 전국교량표준데이터 (data.go.kr 15081953) and processing tiles.
Each bridge: name, facility class (1/2/3종, 기타), superstructure type, length, width, height, completion year, last safety
inspection grade/date, start/end coordinates (axis), managing organisation."""
import json, os, re, urllib.request, numpy as np, pandas as pd

PAGE = 'https://www.data.go.kr/data/15081953/fileData.do'
CLS = {1: '1종', 2: '2종', 3: '3종', 99: '기타'}


def download(out_csv):
    """Fetch the latest national bridge standard data CSV (file id read from the dataset page)."""
    ua = {'User-Agent': 'Mozilla/5.0'}
    html = urllib.request.urlopen(urllib.request.Request(PAGE, headers=ua), timeout=60).read().decode('utf-8', 'ignore')
    m = re.search(r'fileDownload\.do\?atchFileId=(FILE_\d+)&fileDetailSn=(\d+)', html)
    if not m: raise RuntimeError('download link not found on ' + PAGE)
    url = 'https://www.data.go.kr/cmm/cmm/fileDownload.do?atchFileId=%s&fileDetailSn=%s&insertDataPrcus=N' % m.groups()
    req = urllib.request.Request(url, headers=dict(ua, Referer=PAGE))
    os.makedirs(os.path.dirname(out_csv), exist_ok=True)
    with urllib.request.urlopen(req, timeout=300) as r, open(out_csv, 'wb') as f: f.write(r.read())
    return out_csv


def bridges(csv, sido, sigungu=None, min_length=0.0):
    d = pd.read_csv(csv, encoding='utf-8-sig', low_memory=False)
    g = d[d['시도명'].astype(str).str.contains(sido)]
    if sigungu: g = g[g['시군구명'].astype(str).isin([sigungu] if isinstance(sigungu, str) else sigungu)]
    out = []
    for i, r in g.iterrows():
        v = lambda k: (float(r[k]) if pd.notna(r[k]) else np.nan)
        la0, lo0, la1, lo1 = v('교량시작점위도'), v('교량시작점경도'), v('교량종료점위도'), v('교량종료점경도')
        if not np.isfinite([la0, lo0]).all(): continue
        if not np.isfinite([la1, lo1]).all(): la1, lo1 = la0, lo0
        L = v('교량연장'); L = 0.0 if not np.isfinite(L) else L
        if L < min_length: continue
        out.append(dict(id='r%05d' % i, n=str(r['교량명']), c=str(r['시군구명']), lat=round((la0 + la1) / 2, 6), lon=round((lo0 + lo1) / 2, 6), len=L,
                        width=v('교량폭') if np.isfinite(v('교량폭')) else None, poly=False, brg=0.0,
                        geo=[[[round(lo0, 6), round(la0, 6)], [round(lo1, 6), round(la1, 6)]]],
                        reg=dict(cls=CLS.get(int(r['시설물종별등급구분']), '기타') if pd.notna(r['시설물종별등급구분']) else '기타',
                                 type=str(r['상부구조형식']), year=int(r['교량준공연도']) if pd.notna(r['교량준공연도']) else None,
                                 grade=str(r['최종안전점검결과']) if pd.notna(r['최종안전점검결과']) else '',
                                 insp=str(r['최종안전점검일자']) if pd.notna(r['최종안전점검일자']) else '', road=str(r['도로종류']),
                                 route=str(r['도로노선명']), h=v('교량높이') if np.isfinite(v('교량높이')) else None,
                                 org=str(r['관리기관명']), tel=str(r['관리기관전화번호']), load=str(r['설계활하중']))))
    return out


def tiles(brs, size_m=1500.0, margin_m=300.0, min_half_m=800.0):
    """Group bridges on a 1.5 km grid; window = bridge extents + 300 m, at least 1.6 km (enough ground PS for StaMPS and the reference)."""
    lat0 = np.mean([b['lat'] for b in brs]); KE = 111320 * np.cos(np.radians(lat0)); groups = {}
    for b in brs:
        k = (int((b['lon'] - 120) * KE // size_m), int((b['lat'] - 30) * 111320 // size_m)); groups.setdefault(k, []).append(b)
    out = []
    for k, bs in sorted(groups.items()):
        P = np.array([p for b in bs for g in b['geo'] for p in g])
        S, N = P[:, 1].min() - margin_m / 111320, P[:, 1].max() + margin_m / 111320
        W, E = P[:, 0].min() - margin_m / KE, P[:, 0].max() + margin_m / KE
        cy, cx = (S + N) / 2, (W + E) / 2; hy, hx = max((N - S) / 2, min_half_m / 111320), max((E - W) / 2, min_half_m / KE)
        out.append(dict(tile='t_%d_%d' % k, sw=0, burst=0, S=cy - hy, N=cy + hy, W=cx - hx, E=cx + hx, clat=cy, clon=cx, bridges=bs))
    return out


def aoi_wkt(tl, pad_deg=0.01):
    from shapely.geometry import box
    from shapely.ops import unary_union
    return unary_union([box(t['W'] - pad_deg, t['S'] - pad_deg, t['E'] + pad_deg, t['N'] + pad_deg) for t in tl]).convex_hull.wkt
