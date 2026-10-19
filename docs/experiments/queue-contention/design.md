# 큐 쓰기 경합: 실험 계획

| 항목 | 값 |
|---|---|
| 추적 | [YCSB 동등 조건 측정 #226](https://github.com/woonyong-choi/totedb/issues/226)의 추가 비교 |
| 관련 설계 | [아키텍처](../../architecture.md) |
| 사전 데이터 | [기존 YCSB 결과](../ycsb-sqlite/report.md)를 확인했다. 새 큐 도구의 정식 성능 데이터는 수집하지 않았다. 기존 큐 벤치의 enqueue는 순차이며 연결 공유 방식도 달랐다. |

## 목적

동시 생산자와 소비자가 같은 큐 파일을 사용할 때 실제 공개 Python API의 처리량·지연·경합·저장 결과를 비교한다. 일반 SQL 읽기 결과와 큐 사용 적합성을 구분한다. 제품 엔진의 최적화는 하지 않는다.

## 가설

| 가설 | 예측 | 적용 범위 |
|---|---|---|
| H1 | 모든 정식 실행이 작업 256개를 완료하고 중복 완료·누락·payload 불일치·재개방 저장 실패가 없다. | 아래 360회 |
| H2 | mixed의 worker 8에서 ToteDB의 완료 처리량은 worker 1의 두 배 미만이다. | payload별 5쌍 |

H2는 단일 writer와 동기 commit 때문에 작업자 수만큼 쓰기가 늘지 않는다는 소스 기반 예측이다. ToteDB와 SQLite의 성능 순위는 예측하지 않고 조건별 관측과 불확실성을 보고한다.

## 비교 조건

| 항목 | 값 |
|---|---|
| 기준 조건 | SQLite WAL/FULL, 자동 checkpoint 1,000페이지, 준비 문장 cache 128 |
| 추가 조건 | SQLite DELETE/FULL, 준비 문장 cache 128 |
| 비교 조건 | ToteDB 공개 Python Database·Queue, COW DURABLE commit |
| 실행 순서 | workload, payload, worker 순의 고정 matrix. 반복마다 세 엔진 시작 위치를 순환하여 한 엔진만 계속 먼저 실행하지 않는다. |
| 평가 방식 | 자동 수집·검사. 사람이 결과에 점수를 주지 않으므로 맹검 해당 없음 |

공식 YCSB에는 lease·fence·ack 큐 계약이 없으므로 이 실험은 자체 큐 부하다. 공식 YCSB A~F와 결과를 합치지 않는다. 같은 Python 프로세스 구조와 합성 payload를 쓰되 ToteDB는 ctypes 공개 큐 API, SQLite는 Python sqlite3와 lease SQL 구현이다. 이 API 비용 차이를 포함한 사용 경로 비교다.

| workload | 생산자 | 소비자 | 시작·완료 조건 |
|---|---|---|---|
| enqueue | 1·4·8 | 0 | 빈 큐에 고유 작업 256개 저장, 최종 READY |
| drain | 0 | 1·4·8 | 256개를 미리 넣고 claim과 ack를 각각 commit, 최종 ACKED |
| mixed | 1·4·8 | 생산자와 같은 수 | 빈 큐에서 생산·소비 동시 실행. 총 worker는 2·8·16, 최종 ACKED |
| outbox | 1·4·8 | 0 | 업무 행 INSERT와 enqueue를 한 트랜잭션으로 함께 commit, 최종 READY와 업무 행 대응 |

worker마다 연결과 Queue 객체를 하나씩 연다. 핸들 하나의 mutex로 호출 전체를 직렬화하지 않는다. 모든 연결을 준비한 뒤 시작 gate를 열어 요청을 보낸다. payload 생성·설치·연결 준비·사전 적재·재개방 검사는 요청 처리 시간 밖이다. payload 변환·공개 API 호출·commit·재시도·mixed의 빈 큐 대기는 요청 처리 시간 안이다. 시작 gate부터 마지막 worker의 요청 처리 종료까지를 처리량 분모로 쓰고 close·cleanup 비용은 별도로 남긴다.

## 측정 지표

| 지표 | 종류 | 정의 | 단위 |
|---|---|---|---|
| workload·worker·payload·엔진 | 조건 값 | 위 네 부하, 역할당 작업자 수, 값 크기, 저장 설정 | 개·B |
| 완료 처리량 | 계산 값 | 완료 jobs / 요청 처리 구간 wall time. enqueue·outbox는 저장 완료, drain·mixed는 ack 완료 | jobs/s |
| 연산 지연 | 측정·계산 값 | put·claim·ack·outbox의 실제 호출과 재시도 포함 시간, 실행별 p50·p95·p99와 반복 중앙값·범위 | µs |
| BUSY·대기·EMPTY·오류 | 측정 값 | worker별 미적용 경합 재시도 횟수·sleep 시간, 빈 claim, 예외·timeout | 건·s |
| 자원·파일 | 측정 값 | child wall·CPU·최대 RSS, DB·WAL 크기, setup·cleanup 시간 | s·B |
| 작업·저장 검증 | 측정 값 | 실제 ID 집합, payload 바이트·digest, fence·attempts·상태·업무 행과 재개방 결과 | 건·B·해시 |
| 엔진별 비교 | 계산 값 | 5회 중앙값 [최소, 최대], 반복 번호로 대응한 ToteDB/SQLite 비율 중앙값과 구간 | jobs/s·비율 |

성공 처리량과 같은 분모에 오류·미완료·재시도도 표시한다. 실패한 쌍이 있으면 해당 조건의 정상 성능 비교를 금지한다. 서로 다른 workload의 jobs/s를 같은 작업량처럼 합치지 않는다. mixed는 producer 완료와 최종 ack 완료 시각을 구분하고 일시적 EMPTY만으로 종료하지 않는다.

## 실험 환경

| 항목 | 값 |
|---|---|
| 운영체제 | macOS 26.5.2, build 25F84, arm64. 2026-10-19 확인 |
| 실행 방식 | 로컬 단일 프로세스의 다중 스레드. Docker·VM 사용하지 않는다. |
| 하드웨어 | Apple M4, 10코어, 메모리 24GiB. GPU 사용하지 않는다. |
| 도구 | 시스템 Python 3.9.6, sqlite3 3.51.0, Apple clang 17.0.0. 2026-10-19 확인. 실제 옵션·경로·빌드와 버전은 manifest에 저장 |
| 대상 | `YCSB` 브랜치의 제품 기반 `04f9f2d`, 연결 도구를 시험한 뒤 정식 수집 전에 소스 커밋과 파일 해시 고정 |
| 모델 | 해당 없음 |
| 공통 값 | 작업 256개, payload 256·4,096B, worker 1·4·8, 요청별 자동 commit, fixed clock 1,000,000ms, lease 600,000ms, max attempts 제한 없음, idempotency key 없음, ACKED purge 없음 |
| 입력 | index로 결정적으로 생성한 합성 binary payload. 입력 식별자와 파일별 소스 SHA-256을 manifest와 trial에 저장 |
| 시드 | 작업 할당은 고유 index를 worker에 분할하며 난수 없음. 스레드 실행 순서는 고정할 수 없다. bootstrap만 seed 226 |
| 실행 명령 | 아래 명령. 정식 수집 중 다른 빌드·시험은 실행하지 않는다. |

```sh
make SANITIZE= all
python3 bench/queue_contention/run.py test --output build/queue-contention/checks
./docs/experiments/queue-contention/run.sh all
```

### 조건별 값

| 설정 | ToteDB | SQLite DELETE | SQLite WAL |
|---|---|---|---|
| 경로 | 공개 Database·Queue API | sqlite3 lease 큐 | sqlite3 lease 큐 |
| commit | DURABLE COW/fsync | DELETE, FULL, fullfsync OFF | WAL, FULL, fullfsync OFF |
| 페이지·cache | 8,192B·기본 256페이지 | 8,192B·256페이지·mmap 0 | 8,192B·256페이지·mmap 0 |
| 문장 cache | 제품 공개 API에 없음 | sqlite3 cache 128 | sqlite3 cache 128 |
| 상태 색인 | state와 정수 ID 순서 | state·id 색인 | state·id 색인 |
| checkpoint | 해당 없음 | 해당 없음 | autocheckpoint 1,000페이지, 종료 close 비용 별도 기록 |

SQLite의 상태·정수 ID 색인과 문장 cache를 사용한다. 기존 YCSB의 문장 cache 없음 조건과 다르다. API/캐시 비용이나 내구성 보장이 완전히 같다는 뜻은 아니다. FULL/DURABLE은 정상 commit 동기화 의도를 맞춘 조건이며 전원 장애 동등성은 검증하지 않는다. WAL도 writer는 한 번에 하나다([SQLite 문서](https://www.sqlite.org/wal.html), 2026-10-19 확인).

자동 busy 대기를 끄고 두 엔진 모두 미적용 BUSY만 0.5ms부터 20ms까지 대기하며 최대 60초 재시도한다. 이미 시작된 SQLite commit의 BUSY는 같은 COMMIT만 재시도한다. 결과가 불확실한 commit·I/O 오류·소모된 ToteDB 트랜잭션 commit 실패를 연산 전체 재실행으로 숨기지 않는다. 고정 시계로 정상 부하의 lease 만료를 없애고, 만료·재claim·stale fence는 별도 결정적 시험에서 검사한다.

## 표본

| 항목 | 값 |
|---|---|
| 출처 | 결정적 합성 작업과 상태 변화 |
| 크기 | 4부하×2payload×3worker×3엔진×5반복 = 정식 360회. 같은 조건당 사전 실행 1회로 72회 별도 |
| 크기 근거 | 로컬 수집 한도. 최소 다섯 반복과 4KiB 경계를 포함한 두 payload. 검정력 기반 표본 수 산정은 아니다. |
| 중단 규칙 | child당 180초. 실패·timeout·부분 로그와 DB를 보존하고 다음 조건을 계속한다. 불리한 실행 대체·속도 기준 조기 중단 없음 |
| 반복과 예열 | 정식 5회, 사전 1회. 모두 새 프로세스·새 DB이며 사전 실행은 정식 통계에서 제외. 장시간 안정 상태를 뜻하지 않는다. |

## 분석 방법

| 가설 | 지표 | 방법 | 채택 기준 |
|---|---|---|---|
| H1 | 완료·payload·상태·재개방 | 계획된 정식 실행 전수 검사 | 360회 모두 256개 완료, 중복·누락·오류·저장 불일치 0건 |
| H2 | mixed worker 8/1 처리량 비율 | 같은 payload·반복을 대응, 비율 중앙값, seed 226·10,000회 percentile bootstrap 95% 구간 | 상한 <2이면 채택, 하한 ≥2이면 기각, 나머지 보류 |

Bootstrap percentile 정의는 [SciPy 문서](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.bootstrap.html)를 따른다. 표준 라이브러리로 계산한다. 엔진 간 비교도 같은 방법으로 대응 비율을 보고하되 가설 판정과 구분한다. 작은 표본의 구간은 정밀한 모집단 보장을 주지 않는다. 처리량·지연은 소수 첫째 자리, 비율·구간은 셋째 자리, 원자료의 정밀도는 그대로 보존한다.

| 항목 | 규칙 |
|---|---|
| 제외 기준 | 사전 실행·정확성 시험·profile·setup·audit는 정식 요청 처리량 통계에서 제외. 정식 실행은 삭제하지 않는다. |
| 실패한 실행 | 모든 오류·미완료·중복 완료·payload 불일치·timeout을 실패로 센다. 한 반복이라도 실패하면 해당 조건은 비교 불가 |
| 다중 비교 | 보정하지 않는다. 두 H2 조건은 탐색적 개별 판정이다. 여러 조건의 전체 DB 우위를 추론하지 않는다. |

## 판정

| 결과 | 설계 문서에 반영 |
|---|---|
| H1 채택 | 관측한 작업량에서 공개 큐 경로의 정상 완료와 재개방 결과 기록 |
| H1 기각 | 실패 조건·상태·원자료와 남은 문제 기록. 실패를 정상 성능으로 보고하지 않는다. |
| H2 채택·기각·보류 | 큐 쓰기 확장성의 조건별 관측만 보고. 실제 profile과 대조하고 기본값·엔진 코드는 바꾸지 않는다. |

## 추가 분석

- 정식 수집 뒤 mixed worker 4, payload 256B, 256작업, 각 엔진 1회와 사전 1회를 별도 profile 폴더에 수집한다. CPU·RSS·macOS sample을 보존하고 정식 통계에서 제외한다.
- enqueue·claim·ack 지연과 BUSY 대기, worker별 완료 분포를 대조한다. sample에 실제 DB 호출이 없으면 병목을 확정하지 않는다.

## 위험 요소와 대응

| 위험 요소 | 대응 |
|---|---|
| 기존 공유 핸들 mutex와 실제 파일 경합을 혼동 | worker별 Database·Queue 연결과 실제 연결 수를 기록. 공유 핸들 결과와 섞지 않는다. |
| Python GIL·바인딩 차이 | 공개 Python 경로 비교로 한정하고 native 엔진 전체 성능으로 확대하지 않는다. |
| 빠른 실패·EMPTY를 정상 처리로 셈 | 완료 ID·payload·상태·업무 행과 별도 재개방 전수 검사. worker 예외를 부모에게 전달 |
| 한 엔진만 느린 SQL·연결 설정 | SQLite 적절한 색인·문장 cache·WAL/FULL 포함, 실제 PRAGMA와 쿼리 계획 보존 |
| 작은 backlog·짧은 실행·머신 부하 | 256개 규모 명시, 초기 적재·폐기 비용 구분, 부하 평균과 전체 범위 보존. 큰 backlog로 일반화하지 않는다. |
| lease 만료·중복 전달로 작업 수 왜곡 | 정상 실측은 고정 시계로 만료 없음. lease/fence·중복 ack·rollback은 별도 결정적 정확성 검사 |
| 내구성 설정을 전원 장애 증명으로 오해 | 정상 종료와 reopen만 검증하고 전원 장애·다중 프로세스·외부 exactly-once 보장은 미검증으로 명시 |
