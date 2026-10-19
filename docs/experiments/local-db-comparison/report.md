# 로컬 DB와 실제 Python 큐: 실험 결과

## 요약

ToteDB는 이번 정상 처리 비교에서 SQLite WAL/FULL·LMDB·RocksDB보다 기록 처리량이 낮았고, 실제 SQLite ACK 큐 두 종보다도 느렸다. 작업 1,024개·본문 256B에서 ToteDB는 599jobs/s, persist-queue는 1,649jobs/s, Litequeue는 1,461jobs/s였다. 같은 반복끼리 계산한 ToteDB/persist-queue 처리량 비의 중앙값은 0.3628, 명목 95% bootstrap 구간은 [0.3458, 0.3639]였다(n=5쌍). 따라서 범용 DB 대체나 큐 속도를 이유로 ToteDB를 선택할 근거는 없다. 추가 검증할 사용 사례 중 하나는 SQL 업무 기록과 작업 등록·ACK를 같은 트랜잭션으로 묶고 C와 Python에서 쓰는 통합 비용의 절감이다. 이 결합은 선택 기능이며 이번 실험은 그 기능의 처리량 우위나 개발 시간 절감을 입증하지 않는다. ([summary.json](results/summary.json), [catalog.md](catalog.md))

## 실험 환경

| 항목 | 값 |
|---|---|
| 계획·소스 | [design.md](design.md), 계획 커밋 `c46b7005`·`d971a992`, 수집 소스 `5beed7f7`, 제품 기준 `a86cf241` |
| 실행 | [Linux CI 39244451196](https://github.com/woonyong-choi/totedb/actions/runs/39244451196), DB 실행 id `20261019T112642Z-5beed7f`, 2026-10-19 |
| 호스트 | Ubuntu 24.04 VM, Linux 6.17.0-1022-azure, x86_64, AMD EPYC 7763, vCPU 4개, MemTotal 16,373,452kB |
| 런타임 | Python 3.12.15, Python에 링크한 SQLite 3.45.1 |
| 실제 설치 | DuckDB 1.5.6, lmdb 3.0.0, rocksdict 0.3.29, persist-queue 1.1.0, litequeue 0.14, diskcache 5.6.3, Huey 3.4.0 |
| ToteDB | 공유 라이브러리 `-O2 -g`, `BENCHMARK=0`, sanitizer 없음, 수집 전후 제품 변경 없음 |
| 동기화 | SQLite FULL, LMDB sync·metasync 활성, RocksDB sync 활성·WAL 유지, DuckDB 기본 durable commit, ToteDB native COW fsync |
| 순서·표본 | 단일 client, 새 프로세스·새 DB, DB 다음 큐 직렬 실행, 반복별 엔진 순환, 조건별 예열 1회·정식 5회 |
| 근거 | [env.json](env.json), [requirements.txt](requirements.txt), [provenance.json](results/provenance.json), 원본 manifest·worker 결과 |

패키지·설정은 2026-10-19 실제 설치와 조회값으로 확인했다. 바인딩 버전을 native 엔진 버전으로 바꾸어 쓰지 않았다. VM의 디스크·host 간 변동을 제거한 전용 물리 장치 실험은 아니었다.

## 계획과 다른 점

측정 조건·가설·정식 반복 수의 변경은 없었다. 공개 시에는 큐 원본 요약의 반복 payload와 패키지 파일 해시를 artifact에 남기고 공개 요약에서 덜어 냈다. 이 처리는 수집 후에 했으며 처리량·지연·대응 비·실패 판정은 바꾸지 않았다. 원본 요약 SHA-256은 공개 요약에 보존했다. ([summary.json](results/summary.json), [publish.py](publish.py))

## 데이터 처리

| 단계 | DB | 큐 |
|---|---:|---:|
| 계획·수집한 matrix 실행 | 216 | 120 |
| 정식 통계에서 제외한 예열 | 36 | 20 |
| 실패·timeout·누락으로 제외 | 0 | 0 |
| 분석한 정식 실행 | 180 | 100 |
| matrix 밖 DB API 사전 검사 | 6 | 해당 없음 |

DB 검증은 matrix 216회와 별도 API 사전 검사 6회에서 실패가 없었다. 큐 검증도 120회 모두 통과했다. 정식 성능 표본은 합계 280회이며, 예열 56회와 API 검사 6회는 여기에 포함하지 않았다. 느린 반복이나 이상치를 제거하지 않았다. 입력은 결정적인 합성 데이터였다. ID·본문·완료량과 정상 종료 후 재개방 상태를 대조했다. ([summary.json](results/summary.json), [data/README.md](data/README.md))

처리량은 조건별 정식 5회의 중앙값으로 요약했다. 대응 비는 같은 조건·같은 반복의 ToteDB/기준선 처리량 비를 먼저 계산한 뒤 중앙값을 구했다. 중앙값끼리 나눈 값과는 다르다. 구간은 시드 244, 10,000회 percentile bootstrap의 명목 95% 구간이었다. 다중 비교 보정 없는 탐색적 추정이며 모집단 오류율이나 인과 효과로 일반화하지 않았다. ([design.md](design.md), [summary.json](results/summary.json))

## 가설 검증

| 가설 | 확인한 범위와 값 | 신뢰구간 | 판정 |
|---|---|---|---|
| H1 | 대표 DB의 ID·payload·두 레코드 결합·정상 재개방 검사, matrix 216회와 API 검사 6회 불일치 0회 | 전수 audit이므로 해당 없음 | PASS, 실행한 조건에 한정 |
| H2 | 실제 큐의 성공 경로·본문 소비·정상 재개방 후 미완료 0건, 120회 실패 0회 | 전수 audit이므로 해당 없음 | PASS, 성공 경로에 한정 |
| H3 | 실제 sync 조회값과 설정 일치, ToteDB 비계측 빌드·수집 전후 동일성 확인 | 설정·해시 대조이므로 해당 없음 | PASS, 구성 확인에 한정 |

PASS는 속도 우위나 모든 장애에서의 내구성 동등 판정이 아니다. H1의 두 레코드 기록은 완성 ACK 큐 실행이 아니었다. H2의 공식 성능 경로에는 worker 강제 종료나 자동 lease 만료 대기가 없었다. ([design.md](design.md), [summary.json](results/summary.json))

## 추가 분석

### DB 기록 36조건의 처리량

단위는 논리 작업/s이며 반올림한 중앙값이다. 각 실행은 512작업이었다. `write`는 한 레코드마다 commit했고, `atomic-outbox`는 업무·outbox 두 레코드마다 commit했다. `batch32`는 두 레코드 쌍 32개마다 commit했다. SQL은 두 행, KV는 두 key를 기록했으며 SQL 질의·큐 상태기계의 기능까지 같지는 않았다. 전체 반복의 최소·최대와 트랜잭션 지연은 [summary.json](results/summary.json)에 있다.

| 작업 | 본문 B | ToteDB | SQLite DELETE/FULL | SQLite WAL/FULL | DuckDB | LMDB | RocksDB |
|---|---:|---:|---:|---:|---:|---:|---:|
| write | 256 | 2,206 | 1,279 | 2,546 | 736 | 3,752 | 3,720 |
| write | 4,096 | 1,745 | 954 | 3,100 | 619 | 2,059 | 2,567 |
| atomic-outbox | 256 | 1,854 | 1,246 | 2,820 | 467 | 3,850 | 3,593 |
| atomic-outbox | 4,096 | 1,409 | 903 | 3,266 | 413 | 1,951 | 2,566 |
| batch32 | 256 | 8,128 | 25,616 | 49,044 | 762 | 59,267 | 69,851 |
| batch32 | 4,096 | 5,905 | 17,672 | 18,070 | 757 | 30,479 | 31,968 |

ToteDB는 작업마다 commit하는 조건에서 SQLite DELETE/FULL보다 높았지만, WAL/FULL·LMDB·RocksDB보다 낮았다. batch32에서는 DELETE/FULL보다도 낮았다. 이 결과는 작은 기록을 공개 Python API로 호출한 비용이다. DuckDB의 분석형 scan·join 성능이나 SQL·KV 전체의 우열을 판정하지 않았다. batch 지연의 측정 단위는 32쌍의 트랜잭션이며 개별 작업 지연으로 읽지 않는다. ([summary.json](results/summary.json), [catalog.md](catalog.md))

### ACK 큐 네 조건의 정상 처리량

단위는 jobs/s이며 각 칸은 중앙값 [최소, 최대]다. 전부 put한 뒤 get과 ack/done을 끝낸 시간을 쟀다. setup·기능 검사·audit·close/reopen·purge는 측정 밖이었다. 개별 API 시간 측정의 오버헤드는 포함했다. 본문 크기는 JSON ID·필드명을 포함한 전체 직렬화 크기와 다르다. ([summary.json](results/summary.json), [python_queue_compare.py](../../../bench/python_queue_compare.py))

| 작업 수 | 본문 B | ToteDB native | persist-queue SQLiteAckQueue | Litequeue |
|---:|---:|---:|---:|---:|
| 128 | 256 | 702 [661, 732] | 898 [822, 928] | 772 [749, 844] |
| 128 | 4,096 | 596 [541, 626] | 833 [769, 986] | 924 [836, 997] |
| 1,024 | 256 | 599 [583, 604] | 1,649 [1,633, 1,685] | 1,461 [1,400, 1,490] |
| 1,024 | 4,096 | 522 [494, 533] | 855 [835, 867] | 1,327 [1,288, 1,334] |

| 작업 수 | 본문 B | ToteDB/persist-queue 비 [95% 구간] | ToteDB/Litequeue 비 [95% 구간] |
|---:|---:|---:|---:|
| 128 | 256 | 0.7570 [0.7164, 0.8538] | 0.8874 [0.7834, 0.9372] |
| 128 | 4,096 | 0.7081 [0.6046, 0.7544] | 0.6309 [0.5886, 0.7128] |
| 1,024 | 256 | 0.3628 [0.3458, 0.3639] | 0.4023 [0.3991, 0.4313] |
| 1,024 | 4,096 | 0.6107 [0.5837, 0.6177] | 0.3962 [0.3701, 0.4053] |

각 비는 5쌍의 중앙값이다. 모든 조건에서 ToteDB가 두 실제 ACK 큐보다 느렸다. 다만 persist-queue의 id-only ACK·재개방 시 auto_resume, Litequeue의 `claim_id`·`retry_expired`, ToteDB의 lease·fence는 같은 실패 복구 계약이 아니다. Litequeue 0.14에도 claim과 retry가 있으므로 ToteDB만 복구 가능한 큐라고 설명할 수 없다. Litequeue의 `queue.transaction`은 존재하지만 관련 없는 업무 테이블과의 결합을 지원하는 것은 아니다. persist-queue 공개 API도 내부 자동 commit을 사용했다. ([summary.json](results/summary.json), [catalog.md](catalog.md))

### ACK 없는 FIFO는 별도 비교다

| 작업 수 | 본문 B | DiskCache Deque jobs/s | SqliteHuey storage jobs/s |
|---:|---:|---:|---:|
| 128 | 256 | 1,300 [1,268, 1,374] | 1,203 [1,101, 1,283] |
| 128 | 4,096 | 1,287 [1,052, 1,397] | 1,254 [1,143, 1,394] |
| 1,024 | 256 | 2,145 [2,043, 2,161] | 2,330 [2,197, 2,356] |
| 1,024 | 4,096 | 1,733 [1,689, 1,771] | 2,018 [2,007, 2,143] |

값은 중앙값 [최소, 최대]다. 두 API는 꺼낼 때 삭제하며 ACK를 기다리지 않았다. Huey는 task worker·결과 실행이 아닌 storage만 썼다. 이 수치로 ACK 큐와 같은 보장의 비용 순위를 만들지 않았다. 모든 SQLite 큐 writer는 WAL/FULL을 확인했지만 cache·mmap·statement cache·직렬화는 라이브러리별 정책을 유지했다. ACK 행은 재개방 audit까지 보존한 뒤 공개 purge API로 정리했다. ([summary.json](results/summary.json))

### 개별 API 지연은 엔진 CPU 시간이 아니다

작업 1,024개에서 각 실행의 개별 API p99를 구한 뒤 정식 5회의 중앙값을 냈다. 단위는 ms이며, 서로 다른 호출의 p99를 더해 한 작업의 p99로 만들지 않았다. ([summary.json](results/summary.json))

| 본문 B | 엔진 | put p99 | get/pop p99 | ack/done p99 |
|---:|---|---:|---:|---:|
| 256 | ToteDB | 1.141 | 1.458 | 1.475 |
| 256 | persist-queue | 0.790 | 0.330 | 0.278 |
| 256 | Litequeue | 0.671 | 0.449 | 0.408 |
| 4,096 | ToteDB | 1.439 | 1.447 | 1.298 |
| 4,096 | persist-queue | 0.719 | 1.707 | 0.303 |
| 4,096 | Litequeue | 0.719 | 0.505 | 0.844 |

벽시계 지연에는 wrapper·직렬화·SQL·동기화 대기가 섞였다. 큐 원자료의 `cpu_ns`는 성공 경로의 worker 프로세스 CPU 시간이며 엔진 코어만의 시간은 아니었다. 공개 요약에는 이 값을 옮기지 않았다. RSS는 부모 프로세스의 고수위 상속을 구분할 수 없어 측정하지 않았으며 CPU·메모리 우위를 주장하지 않았다.

## 선택형 API와 범용성

ToteDB는 큐 전용 엔진이 아니다. 업무와 작업을 같은 트랜잭션에 묶어야 하는 제약도 없다. 사용 사례를 하나 고르자는 제안은 이점을 검증할 실험 범위를 좁히는 것이지 제품의 사용 가능 범위를 줄이는 결정이 아니다.

| 사용 방식 | 호출 모양 | 동작과 이번 근거 |
|---|---|---|
| SQL DB만 사용 | Database.execute·begin·commit | Queue 생성 없이 CRUD·트랜잭션 가능, 이번 DB primitive가 이 경로 |
| ACK 큐만 사용 | Queue.put/get/ack, txn 생략 | 업무 테이블 불필요, 큐 연산이 자기 트랜잭션으로 확정, 이번 큐 실측이 이 경로 |
| 업무와 큐를 함께 사용 | 같은 DB의 txn을 Queue에 전달 | 업무 변경과 put 또는 로컬 효과와 ACK를 같은 commit으로 묶는 선택 기능 |

[아키텍처](../../architecture.md)의 DB 코어는 큐 모듈을 참조하지 않고, Queue를 설치하지 않으면 큐 객체·테이블이 자동 생성되지 않는다. [Python Queue](../../../python/totedb/queue.py)의 txn 기본값은 None이고, [큐 scope](../../../src/queue/ack_queue_sql.c)는 활성 트랜잭션이 없으면 자기 트랜잭션을 연다. 같은 공유 라이브러리에 큐 기능 코드가 포함되는 것과 큐 사용을 강제하는 것은 다르다.

DB primitive의 atomic-outbox도 일반 테이블 두 개를 기록한 것이며 ACK 큐 설치를 강제한 시험이 아니었다. 따라서 업무 결합이 강제돼 이번 결과가 느리다는 설명은 맞지 않는다. 선택형 API라는 설명이 지원 SQL 범위·단일 writer·플랫폼 등 현재 제약까지 없다는 뜻은 아니다.

## 성능 우위 기대가 충족되지 않은 이유

정확성·재개방·설정의 사전 가설 H1~H3는 통과했다. 충족되지 않은 것은 빠른 범용 DB나 기존 Python 큐보다 빠른 선택지가 될 것이라는 성능 기대다. 아래 설명은 수집 후의 원인 분석이며 새 사전 가설 검증으로 바꾸어 기록하지 않는다. 수치는 요약의 additional_analysis에서 다시 만든다.

### commit 횟수만으로는 격차를 설명할 수 없다

같은 업무·outbox 레코드를 쓰되 작업 32개를 한 commit으로 묶으면 요청한 commit 수는 512회에서 16회로 줄었다. 256B 조건에서 두 작업 조건의 처리량 중앙값 비는 다음과 같았다.

| 엔진 | 작업마다 commit | batch32 | batch/개별의 중앙값 비 |
|---|---:|---:|---:|
| ToteDB | 1,854 | 8,128 | 4.3833 |
| SQLite DELETE/FULL | 1,246 | 25,616 | 20.5635 |
| SQLite WAL/FULL | 2,820 | 49,044 | 17.3885 |
| LMDB | 3,850 | 59,267 | 15.3921 |
| RocksDB | 3,593 | 69,851 | 19.4389 |

이는 같은 반복의 대응 비가 아니라 서로 다른 작업 조건의 중앙값 비다. 작업 조건은 다른 시간 블록에서 실행했으므로 순수 fsync 절감의 인과 효과나 서로 같은 물리 sync 횟수라고 주장하지 않는다. 다만 commit 묶음이 적용돼도 ToteDB의 격차가 남았다. commit 횟수를 줄이는 것만으로 격차가 모두 사라진다는 기대는 충족되지 않았다. 남은 sync의 호출당 비용과 문장·페이지 처리의 기여도는 추가 분리가 필요하다.

코드에서 확인한 구조는 더 구체적이다. [INSERT 실행](../../../src/sql/database_insert.c)의 work_clone과 [문장 실행](../../../src/sql/database.c)의 lexer·parser 처리는 명시적 트랜잭션 안에서도 문장마다 수행된다. batch32의 논리 작업은 레코드 쌍 32개이므로 한 commit 안에 INSERT 호출 64개가 있다. SQLite 원시 DB 기준군은 cached_statements=128로 같은 INSERT 문장 템플릿을 재사용했다. KV 기준군은 SQL 해석 없이 transaction·WriteBatch에 key/value를 넣었다.

[publish_inner](../../../src/sql/database.c)는 heap_batch_finish를 호출한 다음 명시적 트랜잭션이면 stage_adopt로 넘어간다. [heap_batch_finish](../../../src/storage/heap_store.c)는 dirty record·directory·index 페이지를 처리하며 page_file_write를 호출한다. 따라서 commit을 한 번으로 묶는 기능과 여러 SQL 문장의 페이지 변경을 끝까지 한 번에 모으는 기능은 같지 않다. 트랜잭션 안에서도 남는 문장별 복제·힙 batch 완료·페이지 쓰기가 다음 계측 대상이다.

이 경로의 존재는 코드로 확인했다. 그러나 파싱·복제·페이지 쓰기 각각이 이번 시간 차이의 몇 %를 차지하는지는 계측하지 않았다. 트랜잭션 안의 모든 문장이 fsync한다거나, 확인한 함수 하나가 전체 격차의 원인이라는 설명도 하지 않는다.

### 큐의 느림이 claim 한 단계에만 있지는 않다

작업 1,024개·256B에서 각 실행의 API별 평균 wall time을 구한 뒤 다섯 실행의 중앙값을 계산했다. 단위는 μs/API 호출이다. 이 값은 p99나 엔진 CPU 시간이 아니다.

| 엔진 | put | get | ack |
|---|---:|---:|---:|
| ToteDB | 510.910 | 610.503 | 556.574 |
| persist-queue | 272.590 | 189.780 | 140.031 |
| Litequeue | 259.033 | 238.179 | 181.060 |

ToteDB는 put·get·ack 모두에서 더 긴 wall time을 보였다. 따라서 claim 조회 하나만 고치면 전체 격차가 없어질 것이라고 단정할 수 없다. 각 칸은 서로 다른 반복 분포의 중앙값이므로 이를 합산해 정확한 전체 시간을 재구성하지 않는다.

소스의 정상 경로도 확인했다. idempotency key를 쓰지 않고 다음 작업 하나를 가져온 뒤 현재 fence로 처음 ACK하는 경우, [enqueue_in](../../../src/queue/ack_queue.c)은 INSERT RETURNING, claim_in은 READY 조회·만료 LEASED 조회·UPDATE RETURNING, finish_claim은 메타데이터 조회·UPDATE를 실행한다. 이 경로는 put 1개·get 3개·ack 2개의 SQL 문장과 세 개의 자기 트랜잭션으로 구성된다. 큐 호출은 [session_exec](../../../src/queue/ack_queue_sql.c)를 통해 SQL 계층으로 들어간다.

이 개수는 해당 소스 분기의 정적 대조이며 동적 SQL·syscall 카운터의 실측값은 아니다. 반환 객체 복사·Python ctypes 경계·SQL 실행·COW 게시·sync의 상대 기여도도 아직 분리하지 않았다. 큐 API가 C로 구현됐다는 사실만으로 이 여러 경로가 SQLite 위 Python 큐보다 싸지는 않는다. 비교 라이브러리도 저장 엔진은 native SQLite이므로 C 대 Python이라는 단순한 언어 대결이 아니다.

### 충분하지 않은 원인 설명

| 설명 후보 | 이번 근거로 판단할 수 있는 것 | 남은 검증 |
|---|---|---|
| 업무와 큐의 결합 강제 때문 | DB 단독·업무 없는 큐 경로에서도 성능 저하 관측 | 결합 사용 시 이점은 별도 업무·장애 시나리오로 평가 |
| 큰 payload만 문제 | 256B에서도 실제 ACK 큐에 뒤처져 단독 설명 불가 | overflow·페이지 단위·인코딩 비용 계측 |
| 단일 writer 경합 때문 | 이번 시험은 단일 client라 동시 writer 경합으로 격차를 설명할 수 없음 | 동시성 시험은 별도 모집단·재시도·공정성으로 평가 |
| lease/fence 기능만의 비용 | Litequeue에도 claim_id 보호가 있고 ToteDB put도 느려 단독 설명 미입증 | 동일 계약의 정상·stale 경로별 SQL·IO 비교 |
| 이전 복제·버퍼 후보를 다시 넣으면 해결 | 이전 H2/H3는 추가·누적 성능 채택 기준 미충족으로 철회 | 후보별 전후 비교 없이는 재채택 금지 |
| COW 자체의 본질적 한계 | 구현·API·자료 구조가 다른 엔진 비교만으로 아키텍처 전체의 한계를 판정할 수 없음 | 같은 구현에서 쓰기 증폭·복제·게시 비용을 분리 |

이전 [비용 가설 평가](../queue-contention/report.md#단일-writer-후속-비용-평가)에서 내부 변환·재할당을 줄이고도 최종 처리량 개선을 입증하지 못했다. 새 비교는 문장·업무·저장 설정에 따라 여러 비용이 남는다는 해석과 부합한다. 다른 환경·다른 입력의 실험이므로 이번 자료로 과거 실패 원인을 확정하거나 서로의 효과를 곱하지 않는다.

## 해석

ToteDB가 겨냥할 문제는 주문은 저장됐지만 후속 작업은 빠지는 분리 commit이다. 현재 native API는 업무 INSERT와 `Queue.put(txn=...)`, 로컬 업무 효과와 fence ACK를 각각 한 트랜잭션에 넣는다. Python producer와 C worker 예제도 있다. SQLite로도 구현 가능한 기능이므로 차이는 가능 여부가 아니라 앱이 직접 소유할 상태기계와 검증 비용이다. 업무 트랜잭션을 결합하지 않은 이번 큐 정상 경로는 그 비용 절감의 실증이 아니다. 외부 결제·메일·전송의 exactly-once도 보장하지 않는다. ([catalog.md](catalog.md), [producer](../../../examples/outbox/run.py), [worker](../../../examples/outbox/consumer.c))

| 관측 | 가능한 원인, 아직 확정하지 않은 것 | 반증할 다음 검사 |
|---|---|---|
| batch32에서도 ToteDB가 WAL·KV보다 낮았다 | commit 이외의 SQL 해석·상태 복제·호출 비용이 남는다는 가설, 내부 구간 미계측 | 같은 결과·sync를 유지하고 parsing·복제·commit 시간을 분리해 병목 가설 대조 |
| 1,024작업 ACK에서 ToteDB 처리량이 낮았다 | 큐 상태 변경·저장 경로·라이브러리 정책 차이의 기여도 미확인 | 호출별 SQL·sync 횟수, 큐 길이, 보존 정책을 통제한 분해 측정 |
| 업무·큐의 같은 txn API가 존재한다 | 통합 코드 감소와 사용자 수요는 미확인 | 동등 보장의 SQLite outbox와 구현·검증 시간, 장애 후 누락·중복을 비교 |

작은 유리한 지연 구간만 골라 위 결과를 뒤집지 않는다. commit·batch 성능 차이를 ToteDB 설계의 본질적 한계로 확정하지도 않는다. 속도가 우선이면 해당 SQLite·KV·실제 큐 기준선을 먼저 검토한다. 업무 SQL·ACK·C/Python 결합이 실제 요구일 때만 ToteDB의 총 통합 비용을 검증한다.

## 위험 요소 점검

| 위험 요소 | 이 실험에서 |
|---|---|
| 전수 DB 비교라는 오해 | SQL 9종·KV 10종·객체 및 플랫폼 6종 조사와 대표 실측을 구분했다. 전수 실측은 아니었다 |
| 서로 다른 보장을 한 순위에 배치 | DB 기록·ACK·FIFO를 나눴으며 종합 점수를 만들지 않았다 |
| 약한 기본 sync가 빠른 결과를 만듦 | 실제 설정과 재개방 후 조회값을 보존했다. 전원 장애 동등성까지 확인하지 않았다 |
| 바인딩·직렬화·보존 비용 | 공개 Python API 경계와 측정 밖 audit·purge를 명시했다. 파일 정리 비용까지 같다고 보지 않았다 |
| 공유 host·짧은 반복 변동 | 같은 VM에서 직렬·순환 실행했고 큐의 전체 반복 범위를 공개했다. 정식 반복은 조건별 5회였다 |
| 재개방 성공을 전원 장애 보존으로 해석 | 공식 경로는 정상 close/reopen이었다. 별도 기능 검사의 명시적 복구와 장애 실험을 구분했다 |

## 한계

조사한 나머지 후보는 설치 실패나 느린 결과로 제외한 것이 아니다. libSQL·Turso·Firebird는 확장 설치·계약 검증, JVM·Go·JS·브라우저 계열은 별도 runner, 객체 계열은 schema·API 매핑이 필요해 이번 대표 matrix에 넣지 않았다. 제외 사유와 공식 근거는 [catalog.md](catalog.md)에 있다. 모든 로컬 DB를 찾아 실측했다는 결론은 내릴 수 없다.

기능 검사의 claim 후 재개방 복구는 ToteDB의 명시적 `release_leases()`, Litequeue의 `retry_expired(threshold_seconds=0)`, persist-queue의 auto_resume를 사용했다. 자동 만료·worker SIGKILL·OS crash·전원 차단 검증이 아니다. 단일 client 성공 경로는 다중 consumer 경합과 장기 운영·디스크 증가도 입증하지 않는다. 이전 420표본 보고서는 환경·부하가 달라 이번 통계와 합치거나 환경 간 배수를 만들지 않았다. ([python_queue_compare.py](../../../bench/python_queue_compare.py), [기존 결과](../release-cost/report.md))

## 재현 방법

저장소 루트에서 원본 artifact를 내려받고 오프라인 검증·공개 요약 재생성을 실행한다. `verify`는 원자료·해시를 검사하며 Linux 공유 라이브러리를 다른 OS에서 로드해 재측정하는 명령이 아니다. artifact 보존 기한과 식별자는 [provenance.json](results/provenance.json), 원본 요약 해시는 [summary.json](results/summary.json)에 있다.

```sh
gh run download 39244451196 --repo woonyong-choi/totedb --name local-db-queue-evidence --dir build/local-db-queue-evidence
python3 bench/local_db_compare.py verify --output build/local-db-queue-evidence/local-db-comparison
python3 bench/python_queue_compare.py verify --output build/local-db-queue-evidence/python-queue-comparison
python3 docs/experiments/local-db-comparison/publish.py --artifact build/local-db-queue-evidence --output build/local-db-queue-public-check
cmp docs/experiments/local-db-comparison/results/summary.json build/local-db-queue-public-check/results/summary.json
cmp docs/experiments/local-db-comparison/env.json build/local-db-queue-public-check/env.json
```

새 수집은 [design.md](design.md)의 Linux 환경·고정 버전·비계측 빌드를 따른다. DB와 큐 수집 CLI의 실행 id 인자가 다르므로 원본 manifest와 [CI workflow](../../../.github/workflows/ci.yml)의 실제 명령을 기준으로 한다.

## 결론

H1·H2·H3는 실행한 정확성·성공 경로·설정 범위에서 PASS다. 성능 우위를 전제로 제품 설계를 바꾼 문서는 없다. 이 결과만으로 ToteDB 도입을 권하지 않는다. 범용 SQL DB·독립 ACK 큐·업무와 큐의 결합 중 필요한 방식을 선택해 보장·성능·개발·검증·운영 비용을 비교해야 한다. 원자적 작업 등록과 로컬 효과·ACK 결합은 추가 검증할 사용 사례 중 하나이며 필수 사용 방식이나 제품 범위의 제한이 아니다.
