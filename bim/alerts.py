# -*- coding: utf-8 -*-
"""Alerts for the municipality officials: only bridges that need attention in this cycle.
- 신규 경보: grade >= 주의 now and lower (or absent) in the previous cycle
- 등급 상승: grade went up since the previous cycle
- 확인 필요: grade >= 주의 but weak data (few deck PS, abutment proxy) -> field check recommended
Bridges without problems are not listed (their full record stays in the history database and the dashboard)."""
import json, os, sqlite3, time

ST = ['정상', '관심', '주의', '경고']


def previous_levels(c):
    db = sqlite3.connect(os.path.join(c['paths']['data'], c['name'], 'history.sqlite'))
    ids = [r[0] for r in db.execute('select id from cycles order by id desc limit 2')]
    prev = {}
    if len(ids) == 2:
        prev = {r[0]: r[1] for r in db.execute('select bridge, level from judgements where cycle=?', (ids[1],))}
    db.close(); return prev


def make(c, res):
    prev = previous_levels(c); al = []
    for o in res:
        b = o['best']; br = o['bridge']
        if not b or b.get('level', -1) < 2: continue
        a = b.get('allow') or {}; p = prev.get(br['id'])
        kind = '확인 필요' if a.get('review') else ('신규 경보' if p is None or p < 2 else ('등급 상승' if b['level'] > p else '지속'))
        g = br.get('reg') or {}
        al.append(dict(kind=kind, id=br['id'], name=br['n'], city=br['c'], lat=br['lat'], lon=br['lon'], cls=g.get('cls'), type=g.get('type'),
                       length=br['len'], year=g.get('year'), grade_inspection=g.get('grade'), org=g.get('org'), tel=g.get('tel'),
                       status=b.get('st'), prev_status=ST[p] if p is not None and p >= 0 else None, track=b['track'],
                       d_now_mm=a.get('D_now'), d_proj_mm=a.get('D_10y'), ratio_now=a.get('r_now'), ratio_proj=a.get('r_10y'),
                       proxy=bool(b.get('proxy')), n_ps=b.get('n_ps'), last_date=(b.get('dates') or [''])[-1],
                       ratio_lo=a.get('r_lo'), ratio_hi=a.get('r_hi'), d_lo_mm=a.get('D_lo'), d_hi_mm=a.get('D_hi'),
                       thermal_mm=a.get('thermal_mm'), uncertain=bool(a.get('uncertain')), gaps=a.get('gaps')))
    order = {'신규 경보': 0, '등급 상승': 1, '확인 필요': 2, '지속': 3}
    al.sort(key=lambda x: (order[x['kind']], -(x['ratio_now'] or 0)))
    root = os.path.join(c['paths']['data'], c['name'])
    out = dict(municipality=c['title'], made=time.strftime('%Y-%m-%d %H:%M'), n_bridges=len(res),
               n_judged=sum(1 for o in res if o['best'] and o['best'].get('level', -1) >= 0), alerts=al)
    json.dump(out, open(os.path.join(root, 'alerts.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    with open(os.path.join(root, 'alerts.md'), 'w', encoding='utf-8') as f:
        f.write('# %s 교량 InSAR 변위 점검 알림 (%s)\n\n' % (c['title'], out['made']))
        f.write('전체 %d개 교량 중 %d개 판정. 확인이 필요한 교량만 아래에 적습니다.\n\n' % (out['n_bridges'], out['n_judged']))
        if not al: f.write('이번 주기에 확인이 필요한 교량은 없습니다.\n')
        for x in al:
            rng = ' (범위 %d–%d %%)' % (round(100 * x['ratio_lo']), round(100 * x['ratio_hi'])) if x.get('ratio_lo') is not None else ''
            g = x.get('gaps') or {}
            unc = []
            if x.get('uncertain'): unc.append('오차 범위가 등급 경계를 넘음')
            if g.get('flag'): unc.append('자료 공백: 사용 %s/%s장, 최장 공백 %s일, 최근 영상 %s일 전' % (g.get('n_used'), g.get('n_expected'), g.get('max_gap_days'), g.get('stale_days')))
            if x.get('thermal_mm'): unc.append('온도 보정 영향 %s mm (ERA5 기온)' % x['thermal_mm'])
            f.write('- **[%s] %s** (%s, %s %s, 연장 %.0f m, %s년 준공, 안전점검 %s) — %s%s\n  누적 수직변위 %s mm, 허용변위 대비 %d %%%s (10년 예측 %d %%), 관리기관 %s %s%s\n%s'
                    % (x['kind'], x['name'], x['city'], x['cls'], x['type'], x['length'], x['year'], x['grade_inspection'], x['status'],
                       (' (이전 %s)' % x['prev_status']) if x['prev_status'] else '', x['d_now_mm'], round(100 * (x['ratio_now'] or 0)), rng,
                       round(100 * (x['ratio_proj'] or 0)), x['org'], x['tel'], ' · 교대부 대체 판정' if x['proxy'] else '',
                       ('  불확실성: ' + ' · '.join(unc) + '\n') if unc else ''))
    return out
