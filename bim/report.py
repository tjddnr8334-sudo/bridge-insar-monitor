# -*- coding: utf-8 -*-
"""Dashboard (single static HTML) for the municipality: map (시·군 -> 교량), alarm list, per-bridge record
(registry data, allowable-displacement ratios now / projected, QC, thermal correction, time series)."""
import json, os
from . import config as C

KEYS = ['V', 'ACC', 'DIFF', 'CUM']


def entry(b):
    e = dict(len=b['len'], w=b.get('width'), nps=b.get('n_ps', 0), cov=b.get('node_cover', 0), lv=b['level'], trk=b['track'])
    if b['level'] < 0:
        e['why'] = b.get('reason', ''); return e
    it = b.get('items') or {}; e['it'] = [it.get(k) for k in KEYS]; e['sg'] = [1 if (b.get('sig') or {}).get(k, True) else 0 for k in KEYS]
    w = b.get('where') or {}
    e['wh'] = ['교축 %+.0f~%+.0f m 구간' % (w['V'], w['V'] + 15) if w.get('V') is not None else '',
               '교축 %+.0f~%+.0f m 구간' % (w['ACC'], w['ACC'] + 15) if w.get('ACC') is not None else '',
               '교축 %+.0f m ↔ %+.0f m 구간' % tuple(w['DIFF']) if w.get('DIFF') else '', '최대 변위속도 × 관측기간']
    q = b.get('qc') or {}; ref = q.get('reference') or {}
    e.update(conf=b.get('conf'), kT=b.get('kT_med'), sd=[b.get('std_raw'), b.get('std_corr')],
             q=[q.get('n_dates'), q.get('n_kept'), q.get('drop_bperp'), q.get('drop_refcoh'), ref.get('n_cluster'), round(q.get('refcoh_median') or 0, 3)])
    a = b.get('allow')
    if a:
        e.update(rn=a['r_now'], r10=a['r_10y'], cls=a['cls'], typ=a['type'], dn=a['D_now'], d10=a['D_10y'], sa=a['S_allow'], bn=a['beta_now'], b10=a['beta_10y'],
                 ba=a['beta_allow'], sp=a['span'], tob=a['Tobs'], rv=a['review'], nt=a.get('note', ''))
    e['ts'] = b.get('ts'); e['dates'] = b.get('dates')
    return e


def build(c, res, al):
    root = os.path.join(c['paths']['data'], c['name']); web = os.path.join(C.REPO, 'web')
    muni = json.load(open(os.path.join(C.REPO, c['boundaries']), encoding='utf-8'))
    lst = []; alld = set()
    for o in res:
        br = o['bridge']; g = br.get('reg') or {}
        x = dict(id=br['id'], n=br['n'], c=br['c'], lat=br['lat'], lon=br['lon'], t=g.get('type'), cls=g.get('cls'), len=br['len'], yr=g.get('year'),
                 gr=g.get('grade'), ins=g.get('insp'))
        if o['best']:
            x['r'] = entry(o['best']); alld.update(o['best'].get('dates') or [])
        lst.append(x)
    alld = sorted(alld)
    for x in lst:   # time series on the common date axis of the page
        r = x.get('r')
        if r and r.get('ts') is not None:
            m = dict(zip(r.pop('dates') or [], r['ts'])); r['ts'] = [m.get(d) for d in alld]
        elif r: r.pop('dates', None)
    D = dict(muni=muni, dates=[], bridges=[], osm=lst,
             gwinfo=dict(dates=alld, period=('%s.%s ~ %s.%s' % (alld[0][:4], alld[0][4:6], alld[-1][:4], alld[-1][4:6])) if alld else '',
                         reg_total=len(lst), n=sum(1 for x in lst if 'r' in x), tiles=0, inframe=len(lst)),
             alerts=al['alerts'], title=c['title'])
    head = open(os.path.join(web, 'page_head.html'), encoding='utf-8').read().replace('강원도 교량 변위 지도', '%s 교량 변위 지도' % c['title'])
    body = open(os.path.join(web, 'page_body.html'), encoding='utf-8').read()
    html = head + body.replace('<script src="data.js"></script>', '<script>const DATA=' + json.dumps(D, ensure_ascii=False, separators=(',', ':')) + ';</script>')
    out = os.path.join(root, 'dashboard.html'); open(out, 'w', encoding='utf-8').write(html)
    return out
