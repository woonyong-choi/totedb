# 데이터

## 출처

| 항목 | 값 |
|---|---|
| 수집 방법 | 고정 before·after worktree에서 기존 공식 YCSB runner를 호출하고 상위 도구가 전후를 교대 |
| 수집 기간 | 2026-10-19 |
| 개수 | Linux 정식 20회·preflight 20회·baseline load 20회. Mac 중단 자료는 정식 2회·preflight 2회와 load 2회 |
| 표본 여부 | 계획된 Linux 5쌍 전수, Mac은 첫 child 완료 뒤 중단된 부분 자료 |
| 라벨 | 자동 source_phase·source_repetition·엔진·phase. 사람 평가 없음 |
| 알려진 문제 | Mac 첫 ToteDB preflight timeout. Linux native sample 미지원, 요청 난수열 고정 불가 |
| 개인정보 | 업무 입력 없이 합성 키·값 사용. 실행 환경 경로·소스 식별자 포함 |
| 라이선스 | 저장소 [LICENSE](../../../../LICENSE), 공식 YCSB 원문의 별도 라이선스 보존 |

## 파일

| 파일 | 내용 | 만드는 스크립트 |
|---|---|---|
| `raw/20261019T030026Z-c18643c/` | 실패한 Mac 첫 child와 상위 manifest. 수정·대체하지 않는 부분 원자료 | `../scripts/measure.py collect` |
| `raw/20261019T031416Z-b590d61/` | Linux 10개 child 전체 raw·env·집계와 상위 source·build snapshot | `../scripts/measure.py collect` |
| `SHA256SUMS-*` | 실행별 상위 raw 전체 파일 집합과 SHA-256 | `../scripts/measure.py collect` |
| `processed/records.jsonl` | Linux child trial에 전후 phase와 outer 반복 번호를 추가한 40행 | `../scripts/measure.py analyze` |
| `../results/summary.json`, `../results/summary.csv` | 전후·엔진별 지표와 대응 bootstrap | `../scripts/measure.py analyze` |
| `../machine.json` | CI VM CPU·RAM·이미지·UTC·세 worktree SHA | CI의 환경 수집 단계 |
| `../profile/` | Linux after의 별도 진단, 정식 전후 통계에서 제외 | 기존 `bench/ycsb/run.py profile` |
| `../validation/` | 정확성 실행·별칭 red/green·독립 검증과 원문 해시 | 실행 기록 보존 |

Mac child의 자체 요약은 해당 raw 안에 그대로 있다. 최상위 processed·results는 Linux 실행만 포함한다. DB 파일은 수집 당시 worktree의 build 폴더에서 사용했고 이 자료에 포함하지 않는다. Linux 정식·사전 실행은 모두 성공했으며 reopen audit 결과를 보존했다.

## 필드

### `processed/records.jsonl`

| 필드 | 타입 | 단위 | 제약 | 뜻 | 예 |
|---|---|---|---|---|---|
| `run_id` | string | 없음 | child 실행 id | 원래 collector의 실행 식별자 | `20261019T031416Z-b590d61-r1-after` |
| `trial_id` | string | 없음 | child 안에서 고유 | 행 수·E·worker·phase·엔진 식별 | `10000-E-t1-measured-1-totedb` |
| `condition` | object | 행·개 | records·workload·threads·engine·operations | 원래 수집 조건 | `records=10000` |
| `ts_utc` | datetime | UTC | 필수 | child process 시작 시각 | ISO 8601 |
| `input_identifier` | string | 없음 | SHA-256 | baseline의 canonical 전체 해시 | 64자리 hex |
| `source_phase` | string | 없음 | before 또는 after | 제품 소스 구분 | `after` |
| `source_repetition` | integer | 회 | 1~5 | 전후 대응 outer 반복 | `1` |
| `phase` | string | 없음 | measured 또는 preflight | 정식·사전 실행 구분 | `measured` |
| `metrics` | object | 연산·행·바이트·µs | 기존 YCSB parser 계약 | 공식 지표·상태·native 시도·실제 반환 | `logical_operations=1000` |
| `process` | object | s·B·건 | exit·timeout 포함 | CPU·wall·RSS·fault·부하와 실제 명령 | `timeout=false` |
| `audit_before`, `audit_after` | object | 행·바이트 | 별도 재개방 결과 | 키·값·형식·추가·누락 검사 | `missing_rows=0` |
| `problems` | array | 건 | 정상은 빈 배열 | 기존 검증기의 실패 이유 | `[]` |

상위 manifest는 source SHA·파일별 source/build SHA-256·실행 schedule·child exit를 남긴다. 각 child manifest는 실제 버전·설정과 원래 source SHA를 남긴다. Linux VM의 초기 RAM과 CPU 정보는 machine.json에 있으며 실행별 부하는 child process에 있다. 일반적인 독립성이나 물리 host 독점을 보장하는 자료는 아니다.

`analyze`는 원자료를 읽어 processed와 results를 다시 만든다. `verify`는 raw 파일 집합·해시·child 검증·생성물의 바이트 일치를 확인한다. Mac 자료는 matrix가 불완전하여 상위 전후 검증의 성공 자료로 취급하지 않는다. 기존 runner로 child의 보존 상태와 요약 재현만 확인한다.
