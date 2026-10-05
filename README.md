# bridge-insar-monitor

Sentinel-1 무료 위성 자료만으로 **지자체 단위의 모든 교량**을 자동으로 계속 감시하는 프로그램입니다.
지자체(예: 강원특별자치도 강릉시) 하나를 정하면 다음을 스스로 합니다.

1. 국토교통부 전국교량표준데이터에서 그 지자체의 교량을 모두 가져옵니다 (교량명, 종별, 형식, 연장, 준공연도, 안전점검 결과, 좌표).
2. 그 지역을 지나는 Sentinel-1 궤도(상승·하강)를 모두 찾아, 교량이 있는 버스트만 내려받습니다.
3. 새 영상이 들어올 때마다(약 12일 간격) 새 날짜만 정합하고 교량 구역의 변위를 다시 계산합니다.
4. 교량마다 **첫 촬영일 이후 누적 변위**를 주변 지반 대비로 구하고, **허용변위 대비 비율**로 판정합니다.
5. 문제가 생긴 교량만 공무원에게 알립니다 (`alerts.md`, 대시보드의 알람 목록). 문제가 없는 교량도 누적변위·변위속도·위험도 이력은 모두 저장합니다.

```
python -m bim setup  --config config/gangneung.yaml   # 교량·구역·궤도 확인
python -m bim update --config config/gangneung.yaml   # 한 주기 실행 (새 영상이 없으면 바로 종료)
python -m bim report --config config/gangneung.yaml   # 알림·대시보드만 다시 생성
python -m bim status --config config/gangneung.yaml
```

## 처리 단계

| 단계 | 내용 | 코드 |
|---|---|---|
| 교량 목록 | 전국교량표준데이터 → 지자체 교량, 1.5 km 구역 | `bim/registry.py` |
| 궤도 탐색 | 지자체를 지나는 모든 상승·하강 궤도, 촬영 시각, 기준일 | `bim/tracks.py` |
| 자료 수집 | ASF 버스트 SLC (교량 구역 버스트만, VV) + 궤도 파일, 증분 다운로드 | `bim/download.py` |
| 기상 | 촬영 시각 기온·습도·강수 (ERA5, Open-Meteo) | `bim/weather.py` |
| 정합 | ISCE2 topsStack 기하 정합, 날짜별 정합 → 즉시 구역 절단 후 원본 삭제 | `pipeline/stack.sh`, `pipeline/stream_cut.py` |
| PS 추출 | StaMPS (산악지 적응형 후보 선정: 날짜별 진폭 보정, 적설기 제외, 적응 임계값) | `pipeline/tile.sh`, `pipeline/prep_stamps.py`, `pipeline/stamps.sh` |
| 품질 필터 | 수직 기선 200 m 초과 제외, 기준점 결맞음 0.95 (부족 시 0.92→0.80 단계 완화·표시), 시기별 기준점 결맞음 | `pipeline/qc_filter.py` |
| 열 신축 보정 | 노드별 기온 계수 추정 후 제거 | `pipeline/post.py` |
| 판정 | 주변 지반 대비 누적 수직변위 / 허용 총침하 25 mm, 인접 지점 부등변위 / 허용 각변위 1/500 (inframon 기준), 종별 기준, 10년 예측 | `pipeline/post.py`, `config/criteria.yaml` |
| 보강 | 교량 위 점이 부족한 교량: Capon·APES 2배 재초점 + DS 위상연결, 그래도 없으면 교대부 대체 판정 | `pipeline/second.sh`, `pipeline/refocus.py`, `pipeline/merge2.py` |
| 결과 | 궤도별 결과 통합, SQLite 이력, 알림, 대시보드 | `bim/collect.py`, `bim/alerts.py`, `bim/report.py` |

판정 기준과 근거는 [docs/methodology.md](docs/methodology.md), 운영 방법은 [docs/operations.md](docs/operations.md)에 있습니다.

## 환경

WSL2 Ubuntu 22.04에서 검증했습니다. conda 환경 4개를 씁니다: `isce2_mintpy` (ISCE2 topsStack, GDAL), `miaplpy` (h5py, scipy), `b2s` (burst2safe, asf_search, pandas, shapely, pyyaml), `isce2` (sentineleof). StaMPS는 Octave로 실행합니다.
계정 정보는 저장소에 넣지 않습니다: NASA Earthdata `~/.netrc`, Copernicus 계정(궤도), CDS `~/.cdsapirc`.

## 한계

- Sentinel-1 화소는 약 4 m × 14 m입니다. 폭이 좁고 짧은 교량, 산림 속 교량은 교량 위 산란체가 없을 수 있으며, 이 경우 교대부 대체 판정(참고)으로 표시합니다.
- InSAR는 첫 촬영일 이후의 상대 변위만 측정합니다. 준공 이후 전체 침하량은 알 수 없습니다.
- 하강 궤도는 2022–2024년 촬영이 없습니다 (Sentinel-1B 고장 이후 Sentinel-1C 운용 전). 하강 궤도 결과는 2018–2021, 2025– 두 기간으로 나뉩니다.
- 경간은 대장에 없어 형식별 대표값을 씁니다. 실제 경간을 알면 `criteria.yaml` 또는 교량별 보정으로 바꿀 수 있습니다.
