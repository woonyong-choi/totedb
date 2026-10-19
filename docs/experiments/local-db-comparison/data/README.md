# 데이터

## 출처

| 항목 | 값 |
|---|---|
| 수집 방법 | [Linux CI 39244451196](https://github.com/woonyong-choi/totedb/actions/runs/39244451196)의 공개 Python API 비교, 새 프로세스·새 DB |
| 수집 기간 | 2026-10-19 |
| 개수 | DB matrix 216회와 별도 API 검사 6회, 큐 120회. 정식 분석은 DB 180회·큐 100회 |
| 표본 여부 | [design.md](../design.md)의 대표 조건별 예열 1회·정식 5회, 로컬 DB 전수 표본 아님 |
| 라벨 | 수집기·검증기가 완료량·ID·본문·재개방 상태로 PASS와 실패를 판정 |
| 알려진 문제 | 단일 client 성공 경로, 정식 장애 주입 없음, RSS 측정 없음, API·직렬화·보존 정책 차이 |
| 개인정보 | 결정적으로 생성한 합성 ID·본문, 사용자 데이터 없음 |
| 라이선스 | 저장소 [LICENSE](../../../../LICENSE) 적용, 외부 패키지의 별도 라이선스 유지 |

## 파일

원본은 CI의 `local-db-queue-evidence` artifact에 보존했다. 다운로드 명령은 [report.md](../report.md#재현-방법), artifact 식별자·보존 정보는 [provenance.json](../results/provenance.json)에 있다. 원자료는 수정하지 않는다.

| 파일 | 내용 | 만드는 스크립트 |
|---|---|---|
| artifact의 `local-db-comparison/manifest.json` | 실행 id·입력·matrix·API 검사·소스·설치·빌드 동일성 | `bench/local_db_compare.py collect` |
| artifact의 DB `preflight/`·`trials/` | worker 결과·명령·stderr·DB·payload audit·지연 원자료 | `bench/local_db_compare.py collect` |
| artifact의 DB `summary.json`·`SHA256SUMS` | 36조건 요약·대응 비·원자료 파일 해시 | `bench/local_db_compare.py collect` |
| artifact의 `python-queue-comparison/` | schedule·worker 출력·DB·개별 API 시간·본문·기능 검사·전체 요약 | `bench/python_queue_compare.py collect` |
| artifact의 `local-comparison-install.json`·`local-comparison-*.txt`·`local-comparison-*.json` | wheel URL·digest·패키지 버전·호스트·부하·빌드 근거 | CI workflow |
| [summary.json](../results/summary.json) | 정식 통계·큐 반복 시간·계약·원본 요약 SHA-256 | `publish.py` |
| [env.json](../env.json) | 실제 호스트·Python·패키지·ToteDB 빌드·라이브러리 해시 | `publish.py` |

공개 요약은 큐의 반복 payload와 패키지 파일 해시 목록을 덜어 냈지만 성능 지표는 바꾸지 않았다. 해당 원문·프로세스 CPU 시간·전체 직렬화 길이는 artifact에 남아 있다. 예열·실패 처리와 통계 경계는 [report.md](../report.md#데이터-처리)에 있다.

## 필드

원자료는 JSON과 수집기별 파일 묶음이며 JSON Lines로 정규화한 새 관측 행은 만들지 않았다. 실행 시각·run id·입력 조건은 manifest와 schedule·worker 결과에 연결했다. 공개 요약의 핵심 필드는 다음 표와 같다.

| 필드 | 타입 | 단위 | 제약·뜻 |
|---|---|---|---|
| `counts` | object | 회 | DB·큐 정식·예열·별도 API 검사 수 |
| `database.groups` | array | 해당 없음 | 엔진·작업·본문 크기별 36조건, 정식 5회 |
| `ops_per_s`·`jobs_per_s` | object | 작업/s | 정식 반복의 `median`·`min`·`max` |
| `transaction_latency_ns` | object | ns | 한 트랜잭션의 p50·p95·p99, batch는 32쌍 |
| `queues.groups` | array | 해당 없음 | 5엔진×작업 수 2종×본문 크기 2종의 20조건 |
| `phase_latency_ns` | object | ns | 개별 API 분위수를 구한 뒤 반복별 중앙값·범위 요약 |
| `publication_comparisons` | array | 비 | ACK 그룹의 대응 ToteDB/기준선 비·5쌍·명목 95% 구간 |
| `queues.trials` | array | ns·작업/s | 예열 포함 120회의 id·조건·상태·총시간·API 시간 |
| `backend_contracts` | object | 해당 없음 | 실제 버전·sync 조회값·기능 검사·미지원 계약 |
| `raw_summary_sha256` | object | 해당 없음 | artifact의 DB·큐 원본 요약 바이트 SHA-256 |

이 README와 보고서는 수동 작성한 설명이며 통계 정본은 원자료와 재생성한 요약이다. 설명을 지워도 재분석은 가능하지만 입력·비교 경계의 안내가 사라진다. 생성물은 [report.md](../report.md#재현-방법)의 `publish.py` 명령으로 다시 만든다.
