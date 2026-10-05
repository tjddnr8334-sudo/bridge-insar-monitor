# -*- coding: utf-8 -*-
"""Processing health: error / warning alarms of the monitoring system itself (separate from bridge risk alarms).
  - 자료: no new acquisition for > 24 days (two revisits), dates listed at ASF but not downloaded
  - 정합: dates that failed coregistration, stacks that failed
  - 구역: tiles whose StaMPS run or reference search failed
  - 보강: second-pass (Capon / APES / DS) failures
  - 저장공간: free disk below the limit"""
import datetime, glob, json, os, shutil, sqlite3, time
from . import config as C


def _free_gb(p):
    try: return shutil.disk_usage(p).free / 1e9
    except Exception: return None


def check(c, trs):
    ev = []; now = datetime.datetime.now()
    add = lambda level, kind, msg, track='': ev.append(dict(level=level, kind=kind, track=track, msg=msg))
    for tr in trs:
        key, data, work = C.track_dirs(c, tr)
        slc = os.path.join(c['paths']['data'], 'slc', key)
        have = sorted({os.path.basename(s).split('_')[5][:8] for s in glob.glob(os.path.join(slc, 'S1*.SAFE'))} |
                      {d for f in glob.glob(os.path.join(data, 'res', '*.json')) if not f.endswith('_qc.json') for d in (json.load(open(f, encoding='utf-8')).get('dates') or [])[-1:]})
        if have:
            age = (now - datetime.datetime.strptime(have[-1], '%Y%m%d')).days
            if age > 24 and not tr.get('end'): add('경고', '자료', '마지막 영상 %s 이후 %d일 동안 새 영상 없음 (재방문 12일)' % (have[-1], age), key)
        else:
            add('주의', '자료', '내려받은 영상 없음', key)
        lg = os.path.join(data, 'download.log')
        if os.path.exists(lg):
            txt = open(lg, encoding='utf-8', errors='ignore').read()[-200000:]
            n = txt.count('Traceback') + txt.count('ERROR')
            if n: add('주의', '자료', '다운로드 로그 오류 %d건 (재시도 대상)' % n, key)
        for s in glob.glob(os.path.join(work, 'stack_*', 'logs', 'stack.log')):
            t = open(s, encoding='utf-8', errors='ignore').read()
            for line in t.splitlines():
                if line.startswith('dates failed coreg:'):
                    k = int(line.split(':')[1].split()[0])
                    if k: add('주의', '정합', '%s: 정합 실패 날짜 %d개 (해당 날짜 제외됨)' % (os.path.basename(os.path.dirname(os.path.dirname(s))), k), key)
                if 'FATAL' in line: add('경고', '정합', '%s: %s' % (os.path.basename(os.path.dirname(os.path.dirname(s))), line.strip()[:120]), key)
        fails = {}
        for f in glob.glob(os.path.join(data, 'res', '*.json')):
            if f.endswith('_qc.json'): continue
            st = (json.load(open(f, encoding='utf-8')).get('qc') or {}).get('status') or ''
            if st != 'OK': fails[st] = fails.get(st, 0) + 1
        for st, n in fails.items():
            add('주의', '구역', '%s 구역 %d개 (%s)' % ({'FAIL_stamps': 'StaMPS 실패'}.get(st, '기준점 결맞음 미달' if 'reference' in st else st), n, st), key)
        bad2 = 0
        for m in glob.glob(os.path.join(work, 'run2', '*', 'merge2.json')):
            try: info = json.load(open(m, encoding='utf-8')).get('info')
            except Exception: info = 'unreadable'
            if isinstance(info, str): bad2 += 1
        if bad2: add('정보', '보강', '2차 처리(Capon·APES·DS) 실패 교량 %d개' % bad2, key)
    for name, p in (('작업 디스크', c['paths']['work']), ('자료 디스크', c['paths']['data'])):
        g = _free_gb(p)
        if g is not None and g < float(c.get('min_free_gb', 30)): add('경고', '저장공간', '%s 여유 %.0f GB' % (name, g))
    root = os.path.join(c['paths']['data'], c['name']); os.makedirs(root, exist_ok=True)
    out = dict(checked=time.strftime('%Y-%m-%d %H:%M'), events=ev)
    json.dump(out, open(os.path.join(root, 'health.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    db = sqlite3.connect(os.path.join(root, 'history.sqlite'))
    db.execute('create table if not exists events(checked text, level text, kind text, track text, msg text)')
    db.executemany('insert into events values (?,?,?,?,?)', [(out['checked'], e['level'], e['kind'], e['track'], e['msg']) for e in ev])
    db.commit(); db.close()
    return out
