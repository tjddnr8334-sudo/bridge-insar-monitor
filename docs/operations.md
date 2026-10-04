# 운영

## 주기
Sentinel-1은 같은 궤도를 12일마다 촬영합니다 (2025년부터 1A·1C 두 위성). 공개까지 하루 안팎이 걸리므로 `bim update`를 매일 한 번 실행하면 됩니다. 새 영상이 없으면 아무것도 하지 않고 끝납니다.

```
# systemd (deploy/bim-update.service, deploy/bim-update.timer)
sudo cp deploy/bim-update.* /etc/systemd/system/ && sudo systemctl enable --now bim-update.timer
# 또는 cron
0 3 * * *  cd /path/to/bridge-insar-monitor && bash deploy/run_update.sh config/gangneung.yaml
```

## 한 주기에서 하는 일
1. 궤도별 새 날짜 확인 → 교량 구역 버스트만 다운로드, 궤도 파일
2. 기존 스택에 새 날짜만 정합 (기준 영상·지오메트리는 유지) → 교량 구역 절단
3. 새 날짜가 들어온 구역만 StaMPS 재실행 → 품질 필터 → 열 신축 보정 → 판정
4. 궤도별 결과 통합 → 이력 DB(`history.sqlite`) 저장
5. 알림(`alerts.json`, `alerts.md`) — 신규 경보, 등급 상승, 확인 필요만
6. 대시보드(`dashboard.html`)

## 결과 위치 (`paths.data/<name>/`)
| 파일 | 내용 |
|---|---|
| `alerts.md` | 공무원용 알림 (확인이 필요한 교량만) |
| `dashboard.html` | 지도·교량별 기록 |
| `history.sqlite` | 주기별 판정(`judgements`), 교량별 변위 시계열(`series`) |
| `<궤도>/res/*.json` | 구역별 상세 결과 |

## 처음 시작
첫 실행(`update`)은 과거 전체(2018–) 영상을 받고 정합하므로 오래 걸립니다 (지자체 하나, 궤도 하나에 수일). 이후 주기는 새 날짜 하나만 처리합니다.
