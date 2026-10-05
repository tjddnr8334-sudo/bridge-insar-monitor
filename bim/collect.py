# -*- coding: utf-8 -*-
"""Collect per-track tile results into one judgement per bridge, keep every cycle in a SQLite history.
Final judgement per bridge = the track result with the strongest evidence (deck PS > abutment proxy; more deck PS; higher node cover).
All track results are kept (agreement between ascending and descending geometry is reported)."""
import glob, json, os, sqlite3, time
from . import config as C

RANK = {'높음': 3, '보통': 2, '낮음': 1}


def score(b):
    if b.get('level', -1) < 0: return (-1,)
    return (0 if b.get('proxy') else 1, RANK.get(b.get('conf'), 0), b.get('n_ps', 0), b.get('node_cover', 0))


def collect(c, brs, trs):
    per = {}; ids = {b['id'] for b in brs}
    byname = {(b['n'], round(b['lat'], 4), round(b['lon'], 4)): b['id'] for b in brs}   # results from runners with another numbering
    for tr in trs:
        key, data, work = C.track_dirs(c, tr)
        for f in glob.glob(os.path.join(data, 'res', '*.json')):
            if f.endswith('_qc.json'): continue
            r = json.load(open(f, encoding='utf-8'))
            for b in r['bridges']:
                bid = b['id'] if b['id'] in ids else byname.get((b.get('n'), round(b.get('lat', 0), 4), round(b.get('lon', 0), 4)))
                if bid is None: continue
                b = dict(b, id=bid, track=key, dates=r.get('dates'), qc=r.get('qc'), tile=r.get('tile'),
                         npz=os.path.join(data, 'exports', '%s_stamps_qc.npz' % r.get('tile')), inc=b.get('inc') or r.get('inc'))
                per.setdefault(bid, []).append(b)
    out = []
    for br in brs:
        rs = per.get(br['id'], [])
        best = max(rs, key=score) if rs else None
        lv = [x['level'] for x in rs if x.get('level', -1) >= 0]
        out.append(dict(bridge=br, best=best, all=rs, tracks={x['track']: dict(level=x.get('level'), st=x.get('st'), n_ps=x.get('n_ps'), proxy=bool(x.get('proxy')),
                                                                   r_now=(x.get('allow') or {}).get('r_now')) for x in rs},
                        agree=(len(set(lv)) <= 1) if len(lv) > 1 else None))
    save_history(c, out)
    return out


def save_history(c, out):
    db = sqlite3.connect(os.path.join(c['paths']['data'], c['name'], 'history.sqlite'))
    db.execute('create table if not exists cycles(id integer primary key, run_time text)')
    db.execute('''create table if not exists judgements(cycle integer, bridge text, name text, track text, level integer, status text,
                  r_now real, r_10y real, d_now_mm real, n_ps integer, conf text, review integer, proxy integer, last_date text)''')
    db.execute('create table if not exists series(bridge text, track text, kind text, date text, disp_mm real, primary key(bridge, track, kind, date))')
    cur = db.execute('insert into cycles(run_time) values (?)', (time.strftime('%Y-%m-%d %H:%M:%S'),)); cid = cur.lastrowid
    for o in out:
        b = o['best']
        if not b: continue
        a = b.get('allow') or {}
        db.execute('insert into judgements values (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (cid, o['bridge']['id'], o['bridge']['n'], b['track'], b.get('level'), b.get('st'), a.get('r_now'), a.get('r_10y'), a.get('D_now'),
                    b.get('n_ps'), b.get('conf'), int(bool(a.get('review'))), int(bool(b.get('proxy'))), (b.get('dates') or [''])[-1]))
    for o in out:   # every track, three kinds: LOS, vertical, vertical after thermal correction (all relative to the surrounding ground)
        for b in o.get('all', []):
            for kind, key in (('los', 'ts_los'), ('vert', 'ts_vert'), ('vert_tc', 'ts')):
                for d, v in zip(b.get('dates') or [], b.get(key) or []):
                    if v is not None: db.execute('insert or replace into series values (?,?,?,?,?)', (o['bridge']['id'], b['track'], kind, d, v))
    db.commit(); db.close()
    return cid
