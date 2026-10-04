# -*- coding: utf-8 -*-
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bim import registry, tracks

COLS = ['교량명', '시설물종별등급구분', '도로종류', '도로노선명', '도로노선번호', '도로노선방향', '시도명', '시군구명', '시군구코드', '소재지지번주소',
        '교량시작점위도', '교량시작점경도', '교량종료점위도', '교량종료점경도', '교량연장', '교량폭', '교량보도폭', '교량높이', '차로수', '상하행선분리여부',
        '상부구조형식', '교량준공연도', '최종안전점검일자', '최종안전점검결과', '최종안전점검유형', '내진설계적용여부', '내진성능확보여부', '교량보수보강내역',
        '교량보수보강비용', '설계활하중', '허용통행하중', '하부통과제한높이', '관리기관명', '관리기관전화번호', '데이터기준일자']


def _csv(rows):
    f = tempfile.NamedTemporaryFile('w', suffix='.csv', delete=False, encoding='utf-8-sig')
    f.write(','.join(COLS) + '\n')
    for r in rows: f.write(','.join(str(r.get(c, '')) for c in COLS) + '\n')
    f.close(); return f.name


def test_bridges_and_tiles():
    rows = [dict(교량명='내곡교', 시설물종별등급구분=2, 시도명='강원특별자치도', 시군구명='강릉시', 교량시작점위도=37.7467, 교량시작점경도=128.8878,
                 교량종료점위도=37.7481, 교량종료점경도=128.8873, 교량연장=162, 교량폭=25, 상부구조형식='RC슬래브교', 교량준공연도=1993, 최종안전점검결과='B'),
            dict(교량명='각동교', 시설물종별등급구분=3, 시도명='강원특별자치도', 시군구명='영월군', 교량시작점위도=37.121, 교량시작점경도=128.539,
                 교량종료점위도=37.1215, 교량종료점경도=128.541, 교량연장=210, 상부구조형식='PSCI거더교')]
    p = _csv(rows)
    b = registry.bridges(p, '강원', '강릉시')
    assert len(b) == 1 and b[0]['n'] == '내곡교' and b[0]['reg']['cls'] == '2종' and b[0]['reg']['type'] == 'RC슬래브교'
    t = registry.tiles(registry.bridges(p, '강원'))
    assert len(t) == 2 and all(x['N'] - x['S'] >= 1600 / 111320 * 0.99 for x in t)


def test_reference_date_is_middle():
    d = ['20180101', '20200101', '20220101', '20240101', '20260101']
    assert tracks.reference(d) == '20220101'


def test_periods_split_at_long_gaps():
    p = tracks.periods(['20180601', '20190101', '20211201', '20220909', '20250301', '20260101'])
    assert [x[0] for x in p] == ['20180601', '20211201', '20250301']
