# 데이터

## 출처

| 항목 | 값 |
|---|---|
| 수집 방법 | 공개 Python 큐 경로를 새 child 프로세스·새 DB에서 실행하고 작업자 결과와 재개방 audit를 JSON Lines로 저장 |
| 수집 기간 | 2026-10-19~2026-10-19, UTC |
| 정식 실행 id | `20261019T031416Z-b590d61` |
| 프로파일 실행 id | `20261019T032700Z-f7dd4a5-profile` |
| 개수 | 정식 폴더 432행. 정식 360회와 사전 72회. 별도 프로파일 6행은 사전 3회와 분석 3회 |
| 표본 여부 | 사전에 고정한 합성 부하 matrix의 전수 실행. 실제 서비스 요청에서 추출한 표본은 아님 |
| 라벨 | 수집기가 완료·저장 검사로 `ok` 또는 `failed`를 부여. 사람 라벨 없음 |
| 알려진 문제 | 작은 작업 묶음, VM 잡음, Python 바인딩 비용 차이. Linux native sample 미지원, cProfile은 controller thread만 관측 |
| 개인정보 | 합성 payload에 개인정보 없음. 환경 기록에는 실행 경로·PID·공개 소스 SHA 포함 |
| 라이선스 | 저장소의 [MIT License](../../../../LICENSE) |
| 원본 보관 | [CI 실행 39193707075](https://github.com/woonyong-choi/totedb/actions/runs/39193707075)의 `queue-contention-linux-comparison` artifact |

가설·표본·통계 방법은 [Linux 재측정 계획](../linux-design.md)과 [기존 계획](../design.md), 결과는 [실험 보고서](../report.md)에 있다. 실제 큐 소스는 `f7dd4a57c2d6455036f8047d2423bd03d0e9d6b8`이다. 공통 실행 id의 `b590d61`은 workflow checkout이며 큐 소스와 다르다.

## 파일

아래 경로의 `R`은 정식 실행 id, `P`는 프로파일 실행 id, `T`는 trial id다. 데이터 폴더 밖 경로는 `../`로 표시한다. 정식 자료와 프로파일 자료는 같은 schema를 사용하고 서로 다른 manifest를 갖는다.

| 파일 | 내용 | 만드는 스크립트 |
|---|---|---|
| `raw/R/raw.jsonl` | 실행별 조건·요청 시간·작업자 기록·audit를 담은 원자료 | `bench/queue_contention/run.py collect` |
| `raw/R/manifest.json` | matrix·실행 id·수집 상태·명령·파일 해시 | `bench/queue_contention/run.py collect` |
| `raw/R/trials/T/spec.json` | 단일 child 실행의 입력 조건·DB 경로·timeout | `bench/queue_contention/run.py` |
| `raw/R/trials/T/stdout.txt` | child의 결과 JSON 원문. 실패 시 부분 출력도 보존 | `bench/queue_contention/run.py` |
| `raw/R/trials/T/stderr.txt` | child 오류 출력 원문 | `bench/queue_contention/run.py` |
| `raw/R/trials/T/worker-N.json` | 완료된 작업자의 종료 전후 결과 | `bench/queue_contention/trial.py` |
| `SHA256SUMS` | 원자료·trial 증거·환경·manifest의 SHA-256 목록 | `bench/queue_contention/run.py` |
| `../env.json` | 수집 전후 도구·소스·native library·부하 환경 | `bench/queue_contention/run.py` |
| `../machine.json` | CPU·RAM·runner 이미지·공통 실행 id·소스 SHA | CI의 환경 수집 단계 |
| `../results/summary.json` | 실행별 지표·조건별 분포·대응 비율·H2 구간 | `bench/queue_contention/analysis.py` |
| `../results/summary.csv` | 실행별 주요 지표를 평탄화한 표 | `bench/queue_contention/analysis.py` |
| `../logs/*.log` | collect·verify·analyze·profile의 명령과 전체 출력 | `../run.sh` |
| `../profile/data/raw/P/` | 별도 프로파일 raw·manifest·trial 증거 | `bench/queue_contention/run.py profile` |
| `../profile/data/raw/P/trials/T/python.prof` | controller thread의 cProfile 자료 | `bench/queue_contention/run.py` |
| `../profile/data/raw/P/trials/T/python-profile.json` | cProfile 관측 범위와 제외 항목 | `bench/queue_contention/run.py` |
| `../profile/data/raw/P/trials/T/sampling.json` | native sample 가용 여부와 대체 파일 | `bench/queue_contention/run.py` |
| `../profile/data/SHA256SUMS` | 프로파일 원자료 파일별 해시 | `bench/queue_contention/run.py` |
| `../profile/env.json` | 프로파일 수집 전후 환경 | `bench/queue_contention/run.py` |
| `../profile/results/summary.json`, `../profile/results/summary.csv` | 프로파일 실행별 지표. 정식 비교 집계에서 제외 | `bench/queue_contention/analysis.py` |

`raw.jsonl`과 trial 증거는 수집한 그대로 보존한다. JSON Lines는 UTF-8, BOM 없음, 한 줄에 JSON 하나와 줄바꿈으로 구성한다. JSON 결측값은 `null`, CSV 결측값은 빈 칸이다. CSV에는 머리 행이 있으며 복합 필드는 JSON 문자열로 담는다. 요약 파일은 생성물이다. 원자료를 보존하면 [재현 방법](../report.md#재현-방법)의 analyze 명령으로 다시 만든다.

`spec.directory`와 `manifest.database_root`는 수집 당시 runner의 DB 위치다. 자료를 내려받은 로컬 위치를 뜻하지 않는다. DB·WAL·SHM 자체와 저장된 파일 크기를 혼동하지 않는다. 원자료를 지우면 재검증 근거가 사라지므로 요약 파일로 원자료를 대체하지 않는다.

## 필드

### `raw.jsonl`

한 행은 하나의 엔진·조건·phase·반복에 해당한다. `run_id`와 `trial_id`의 조합이 고유하다. `input_identifier`는 정렬·들여쓰기·끝 줄바꿈을 포함한 `condition`의 정규 JSON 바이트에 대한 SHA-256이다. 엔진과 반복을 제외하므로 같은 입력 조건의 엔진·반복끼리 대응할 수 있다.

| 필드 | 타입 | 단위 | 제약 | 뜻 | 예 |
|---|---|---|---|---|---|
| `run_id` | string | 없음 | 필수, manifest와 일치 | 수집 실행 식별자 | `20261019T031416Z-b590d61` |
| `trial_id` | string | 없음 | 실행 안에서 고유 | phase·반복·부하·작업자·payload·작업 수·엔진 식별자 | `measured-r0-mixed-w4-b256-n256-totedb` |
| `ts_utc` | datetime string | UTC | 필수 | controller가 결과 행을 기록한 시각 | `2026-10-19T03:23:02.353842+00:00` |
| `input_identifier` | string | 없음 | SHA-256 64자리 | 입력 조건 식별자 | `120a275fb1b0e5a81ebfffd0c4e3e291a2b7a85bed1720e87de3dea62e329a5e` |
| `condition` | object | 없음 | 필수 | 아래 조건 객체 | `{"workload":"mixed","workers":4,"payload_bytes":256,"jobs":256}` |
| `condition.workload` | string | 없음 | `enqueue`, `drain`, `mixed`, `outbox` | 요청과 완료의 계약 | `mixed` |
| `condition.workers` | integer | 역할당 명 | `1`, `4`, `8` | 해당 역할의 작업자 수. mixed는 생산자·소비자 각각 이 수 | `4` |
| `condition.payload_bytes` | integer | B | `256`, `4096` | 작업별 binary payload 크기 | `256` |
| `condition.jobs` | integer | 개 | `256` | 실행의 기대 완료 작업 수 | `256` |
| `engine` | string | 없음 | `totedb`, `sqlite-delete`, `sqlite-wal` | 공개 API와 저장 설정 | `totedb` |
| `phase` | string | 없음 | `preflight`, `measured`, `profile` | 사전·정식·진단 구분 | `measured` |
| `repetition` | integer | 없음 | 정식 `0`~`4`, 사전·profile `0` | 엔진·작업자 조건의 대응 반복 번호 | `0` |
| `status` | string | 없음 | `ok`, `failed` | 실행·저장 검사 결과 | `ok` |
| `problems` | array of string | 없음 | 정상은 빈 배열 | child·audit·완료 대조의 실패 이유 | `[]` |
| `command` | array of string | 없음 | 필수 | 실제 child 실행 인수 | `["/usr/bin/python3", ".../run.py", "_trial", "--spec", ".../spec.json"]` |
| `exit_code` | integer | 없음 | controller가 기록 | child 종료 코드 | `0` |
| `pid` | integer | 없음 | 정상 child 결과에 존재 | trial 프로세스 ID | `7716` |
| `controller_started_at`, `controller_finished_at` | number | Unix 초 | controller 시각 | child 시작·종료를 포함하는 바깥 구간 | `1792380182.08853` |
| `started_at`, `finished_at` | number | Unix 초 | 정상 child 결과에 존재 | gate 시작 시각·audit 이후 결과 기록 시각 | `1792380182.1650183` |
| `elapsed_s` | number 또는 null | 초 | 성공 시 양수 | gate부터 마지막 worker 요청 완료까지 | `0.1149620149999464` |
| `setup_s` | number | 초 | 정상 child 결과에 존재 | 입력 검사·payload 생성·설치·drain preload·작업자 준비 | `0.0051157519999378565` |
| `cleanup_s` | number | 초 | 정상 child 결과에 존재 | 마지막 요청 완료부터 모든 worker join까지. 재개방 audit 제외 | `0.0009469700000863668` |
| `completed_jobs` | integer | 개 | 성공 시 기대 jobs와 일치 | enqueue·outbox 저장 완료 또는 drain·mixed ack 완료 | `256` |
| `throughput_jobs_s` | number 또는 null | jobs/s | 정상 요청 구간에서 계산 | `completed_jobs / elapsed_s` | `2226.822485671631` |
| `measurement_scope` | object of string | 없음 | 정상 child 결과에 존재 | elapsed·setup·파일 크기의 경계와 포함·제외 비용 | `{"payload_generation":"before start gate"}` |
| `settings` | object | 없음 | 엔진별 키가 다름 | setup 연결에서 읽은 실제 설정 | `{"clock_ms":1000000,"lease_ms":600000}` |
| `resources_before`, `resources_after` | object | 없음 | 정상 child 결과에 존재 | 요청 전·audit 후 프로세스 자원 snapshot | 아래 자원 필드 |
| `file_sizes` | object | 없음 | 정상 child 결과에 존재 | 모든 worker·audit 연결을 닫은 뒤 파일 크기 | `{"phase":"after_close","bytes":{"db":245760,"wal":0,"shm":0}}` |
| `workers` | array of object | 없음 | 작업자별 결과. 실패 시 부분 집합 가능 | 아래 작업자 필드 | `[]` |
| `audit` | object 또는 null | 없음 | 실패 시 null 가능 | 재개방 전수 검사 결과 | 아래 audit 필드 |

`finished_at - started_at`을 요청 처리 시간으로 쓰지 않는다. 처리량 분모는 단조 시계로 측정한 `elapsed_s`다. 실패 결과는 일부 정상 필드가 없을 수 있다. 정상 수집에서는 실패 실행이 없었지만 실패를 읽을 때도 원문과 오류를 보존한다.

### `workers[]`와 `worker-N.json`

작업자마다 별도 연결을 열고 설정을 기록한다. `ready_at`은 gate를 기다리기 전 시각이다. 혼합 부하의 생산자 종료와 소비자 최종 ack 종료는 `role`과 `requests_finished_at`으로 구분한다.

| 필드 | 타입 | 단위 | 제약 | 뜻 | 예 |
|---|---|---|---|---|---|
| `worker` | integer | 없음 | trial 안에서 고유 | 작업자 번호 | `0` |
| `role` | string | 없음 | `producer`, `consumer` | 생산·소비 역할 | `producer` |
| `ready_at`, `started_at`, `requests_finished_at`, `finished_at` | number | Unix 초 | 작업자별 기록 | 연결 준비·gate 통과·요청 종료·close 종료 시각 | `1792380182.1647954` |
| `requests_finished_perf` | number | 단조 시계 초 | 성공한 요청 종료 | `elapsed_s` 계산용 종료 시각 | `582.308281324` |
| `close_s` | number | 초 | 0 이상 | 연결 close 시간 | `0.00003192999997736479` |
| `put` | array of tuple | 없음 | `[ID, input index, payload SHA-256]` | 생산 완료의 ID와 입력·payload 대응 | `[[1,0,"ec3dd8f57c448361a57da29e9314e7bf87f360fedfb10f34c95d24d3e15c0bd3"]]` |
| `acked` | array of tuple | 없음 | `[ID, input index, fence, payload SHA-256]` | ack 완료와 fence 대응 | `[[1,0,1,"ec3dd8f57c448361a57da29e9314e7bf87f360fedfb10f34c95d24d3e15c0bd3"]]` |
| `busy` | integer | 건 | 0 이상 | 미적용 연산의 BUSY 재시도 수 | `0` |
| `empty` | integer | 건 | 0 이상 | 빈 claim 응답 수 | `0` |
| `retry_sleep_s` | number | 초 | 0 이상 | BUSY 뒤 실제 대기 시간 합계 | `0.0` |
| `latency_s` | object of array of number | 초 | `put`, `claim`, `ack`, `outbox`, `empty_claim` | 각 호출과 해당 재시도를 포함한 지연 표본 | `{"claim":[],"ack":[]}` |
| `error` | string 또는 null | 없음 | 정상은 null | 작업자 예외 | `null` |
| `settings` | object | 없음 | 엔진별 실제 연결 설정 | setup 연결과 별도로 확인할 설정 | `{"busy_timeout_s":0}` |

### `audit`, `file_sizes`, 자원과 설정

| 필드 | 타입 | 단위 | 제약 | 뜻 | 예 |
|---|---|---|---|---|---|
| `audit.rows` | integer | 행 | 정상 시 jobs와 일치 | 재개방 뒤 큐 행 수 | `256` |
| `audit.business_rows` | integer | 행 | outbox는 jobs, 나머지는 0 | 업무 행 수 | `0` |
| `audit.items` | array of tuple | 없음 | `[ID, input index, payload SHA-256]` | 재개방 큐 행의 입력 대응 | 작업자의 `put`와 같은 tuple 형식 |
| `audit.row_hash` | string | 없음 | SHA-256 64자리 | 큐 행 순서의 8바이트 ID와 payload를 이어 붙인 digest | `27a250b7a8fdac2fa02cfb0d5c65dab7fc5e0c9fc303c63a67cfc8693c22b3f0` |
| `audit.business_hash` | string | 없음 | SHA-256 64자리 | 업무 행 순서의 payload를 이어 붙인 digest | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `audit.problems` | array of string | 없음 | 정상은 빈 배열 | 전체 payload 바이트·ID·상태·fence·attempts·업무 행 대조 문제 | `[]` |
| `file_sizes.phase` | string | 없음 | `after_close` | worker와 재개방 audit 연결을 모두 닫은 뒤 측정 | `after_close` |
| `file_sizes.bytes.db`, `wal`, `shm` | integer | B | 파일이 없으면 0 | 해당 시점의 잔존 파일 크기 | `245760`, `0`, `0` |
| `resources_*.user_cpu_s`, `system_cpu_s` | number | 초 | 프로세스 누적 값 | 사용자·커널 CPU 시간 | `0.089751` |
| `resources_*.peak_rss_bytes` | integer | B | 프로세스 최고치 | 최대 resident memory | `27185152` |
| `resources_*.load_average` | array of number | 없음 | 1·5·15분 순서 | host 부하 평균 | `[1.49072265625,1.1201171875,0.6015625]` |
| `settings.api`, `durability`, `busy_timeout_s` | string 또는 number | 항목별 | ToteDB | 공개 API·COW commit·내부 대기 설정 | `public Python Database/Queue` |
| `settings.clock_ms`, `lease_ms` | integer | ms | 양쪽 정상 부하 고정값 | 논리 시계·lease 길이 | `1000000`, `600000` |
| `settings.sqlite_version`, `journal_mode`, `synchronous` | string 또는 integer | 없음 | SQLite | 버전·journal·동기화 설정 | `3.45.1`, `wal`, `2` |
| `settings.page_size`, `cache_size`, `mmap_size`, `statement_cache` | integer | B·페이지·B·문장 | SQLite | 페이지·cache·메모리 매핑·준비 문장 cache | `8192`, `256`, `0`, `128` |
| `settings.busy_timeout`, `fullfsync`, `wal_autocheckpoint` | integer | ms·없음·페이지 | SQLite | 내장 대기·fullfsync·자동 checkpoint | `0`, `0`, `1000` |
| `settings.claim_query_plans` | object of array | 없음 | SQLite | `jobs_ready`, `jobs_expired` 쿼리 계획 | `SEARCH jobs USING COVERING INDEX jobs_ready (state=?)` |

상태·fence·attempts의 모든 원시 열을 audit 결과에 복사하지는 않는다. 수집기는 재개방한 행을 기대값과 전수 대조하고 불일치를 `audit.problems`에 남긴다. 정상 enqueue·outbox는 READY, drain·mixed는 ACKED를 기대한다. 원자료 독립 검증은 보존된 ID·payload·digest와 검사 결과를 대조하는 범위다.

`file_sizes`는 최대 WAL이나 총 쓰기 바이트가 아니다. 자원 snapshot도 순수 요청 구간만의 CPU·RSS를 뜻하지 않는다. summary에는 setup과 파일 크기가 포함되지만 `cleanup_s`, 작업자 `close_s`, 전체 audit 객체는 raw에서 확인한다.

### `manifest.json`과 `spec.json`

| 필드 | 타입 | 단위 | 제약 | 뜻 | 예 |
|---|---|---|---|---|---|
| `manifest.schema` | integer | 없음 | `1` | 자료 schema 버전 | `1` |
| `manifest.run_id` | string | 없음 | raw와 일치 | 수집 실행 식별자 | `20261019T031416Z-b590d61` |
| `manifest.kind` | string | 없음 | `collect`, `profile` | 수집 종류 | `collect` |
| `manifest.formal_measurement` | boolean | 없음 | profile은 false | 정식 수집 폴더 여부. 개별 사전 행은 별도 phase | `true` |
| `manifest.state` | string | 없음 | 완료 검증은 `complete` 필요 | 수집 상태 | `complete` |
| `manifest.started_at`, `finished_at` | number | Unix 초 | 수집 구간 | controller 전체 수집 시간 | `1792380182.087583` |
| `manifest.matrix` | object | 없음 | engines·workloads·workers·payloads·jobs·repetitions | 기대 조건 조합 | `{"jobs":256,"repetitions":5}` |
| `manifest.expected_records`, `failed_records` | integer | 행 | 기대 수·실패 수 | 누락·실패 대조 기준 | `432`, `0` |
| `manifest.command` | array of string | 없음 | 실제 인수 | 수집 명령 | `collect` 인수를 포함한 배열 |
| `manifest.output`, `database_root` | string | 경로 | 수집 환경의 절대 경로 | 증거와 DB 위치 | `/home/runner/work/totedb/totedb.wt/queue-contention/...` |
| `manifest.hashes` | object of string | 없음 | 실험 루트 상대 경로와 SHA-256 | env와 raw·trial 증거의 무결성 목록 | `data/raw/R/raw.jsonl` 키 |
| `spec.condition`, `engine` | object, string | 없음 | raw 조건과 일치 | child의 입력 | `totedb` |
| `spec.directory`, `evidence_directory` | string | 경로 | 수집 환경의 절대 경로 | DB 폴더·trial 증거 폴더 | `.../database` |
| `spec.fault` | string | 없음 | 정상 실측은 빈 문자열 | 정확성 시험용 실패 주입 | `""` |
| `spec.timeout_s` | number | 초 | 사전 계획값 | child 제한 시간 | `180` |

manifest는 자신의 해시를 `hashes`에 넣지 않는다. `SHA256SUMS`가 manifest 해시를 추가한다. 환경 파일과 원자료 파일 집합도 검증하므로 한 파일의 해시만 맞는 것으로 실행 전체를 검증하지 않는다.

### `summary.json`과 `summary.csv`

| 필드 | 타입 | 단위 | 제약 | 뜻 | 예 |
|---|---|---|---|---|---|
| `schema` | integer | 없음 | `1` | 요약 schema 버전 | `1` |
| `workload_origin` | string | 없음 | 자체 합성 큐 부하 | 공식 YCSB와 구분 | `synthetic queue workload; not an official YCSB workload` |
| `bootstrap` | object | 없음 | samples·seed·confidence | percentile bootstrap 설정 | `{"samples":10000,"seed":226,"confidence":0.95}` |
| `runs[]` | array of object | 항목별 | 모든 phase 보존 | raw에서 추출한 실행별 지표 | `trial_id`, `phase`, `condition`, `engine`으로 대응 |
| `runs[].busy`, `empty`, `retry_sleep_s`, `errors` | integer, integer, number, array | 건·건·초·없음 | 작업자 결과에서 집계 | 재시도·빈 응답·대기·오류 | `0`, `0`, `0.0`, `[]` |
| `runs[].latency_s` | object | 초 | 연산별 count·p50·p95·p99 | 한 실행의 작업자 표본을 합친 지연 요약 | 표본 없음은 count 0, 분위수 null |
| `runs[].worker_fairness` | object | 개·없음 | 역할별 counts·min·max·jain_index | 작업자 완료 분포 | 작업자가 없으면 counts 빈 배열, 지수 null |
| `aggregates[]` | array of object | 항목별 | 정식 phase만 | 조건·엔진별 처리량과 지연 분위수의 median·min·max | 조건당 5회 |
| `aggregates[].comparable` | boolean | 없음 | 사전·정식 모두 정상일 때 true | 실패 조건의 비교 방지 | `true` |
| `comparisons[]` | array of object | 비율 | 같은 조건·반복의 엔진 대응 | ToteDB와 SQLite 기준선의 비교 | baseline `sqlite-wal` |
| `comparisons[].median_ratio`, `bootstrap_95_ci` | number, array of number | 비율 | 비교 불가 시 null | 대응 비율의 중앙값·95% 구간 | `0.831`, `[0.773,0.883]` |
| `comparisons[].pairs`, `status`, `metric` | integer, string, string | 쌍·없음·없음 | `comparable` 또는 `incomparable` | 대응 수·비교 가능 여부·지표 이름 | `5`, `comparable` |
| `totedb_mixed_worker_scaling[]` | array of object | 비율 | payload별 대응 | H2의 mixed 작업자 8/1 비교 | payload·jobs·pairs·median_ratio·bootstrap_95_ci |

`runs`에는 원자료의 `run_id`, `ts_utc`, `input_identifier`가 반복되지 않는다. 정규 관측의 식별자는 raw에 있으며 manifest와 `trial_id`로 결합한다. CSV는 `condition`을 workload·workers·payload_bytes·jobs로 펼치고 `latency_s`, `worker_fairness`, `resources_*`, `file_sizes`, `errors`를 JSON 문자열로 담는다. 각 실행의 p95를 반복 간 요약한 값과 전체 실행의 표본을 한꺼번에 합친 p95는 다르다.

프로파일 summary는 `preflight`와 `profile` 실행을 `runs`에 담는다. 정식 phase가 없으므로 정식 `aggregates`·`comparisons`·작업자 확장성 비교는 비어 있다. `sampling.json.status`는 `unavailable`, `fallback`은 `python.prof`다. `python-profile.json.scope`는 `controller-thread`이며 worker 호출과 native stack을 제외한다. `native_evidence`의 파일 이름은 지원 플랫폼용 참조이고 이 Linux 실행에 native sample 파일이 있다는 뜻은 아니다.

### 환경 metadata

| 파일·필드 | 타입 | 단위 | 제약 | 뜻 | 예 |
|---|---|---|---|---|---|
| `env.before`, `env.after` | object | 없음 | 수집 전후 | Python·platform·CPU 수·부하·timestamp·환경 변수 | Python `3.12.3` |
| `env.*.git_and_tools` | object | 없음 | stdout·stderr·exit_code 보존 | git HEAD·status·diff·컴파일러 결과 | HEAD `f7dd4a5...` |
| `env.*.source_sha256` | object of string | 없음 | 경로와 SHA-256 | 수집·분석 소스 식별 | `bench/queue_contention/analysis.py` |
| `env.*.native_library` | object | 없음 | path·sha256 | 실제 로드한 native library | `libtotedb.so` |
| `machine.run_id`, `ts_utc` | string | 없음·UTC | 공통 CI 식별자 | 환경 snapshot 시각과 실행 | `20261019T031416Z-b590d61` |
| `machine.cpu_count`, `load_average` | integer, array | 개·없음 | runner 관측 | 논리 CPU와 부하 | `4` |
| `machine.lscpu`, `meminfo`, `uname` | string | 원문 | 명령·파일 출력 보존 | CPU·RAM·kernel | `AMD EPYC 7763 64-Core Processor` |
| `machine.runner` | object of string | 없음 | CI 환경 변수 | 실행 번호·시도·OS·이미지 | `GITHUB_RUN_ID=39193707075` |
| `machine.sources` | object of string | 없음 | 각 checkout의 전체 SHA | BASELINE_ROOT·CHANGED_ROOT·QUEUE_ROOT 구분 | 큐 소스 `f7dd4a5...` |
