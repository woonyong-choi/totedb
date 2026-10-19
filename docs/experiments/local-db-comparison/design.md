# 로컬 DB와 실제 Python 큐: 실험 계획

| 항목 | 값 |
|---|---|
| 추적 | [#244](https://github.com/woonyong-choi/totedb/issues/244) |
| 대상 | 제품 기준 a86cf241, 제품 소스·공개 API·내구성 변경 없음 |
| 후보 조사 | [DB·큐 후보와 사용 과제](catalog.md), 2026-10-19 공식 자료 확인 |
| 사전 데이터 | 기존 ToteDB·SQLite 실험과 소규모 패키지 API 검사를 배경으로 사용, 새 정식 표본과 합산 금지 |

## 목적

실제 설치한 로컬 DB와 SQLite 기반 Python 큐 라이브러리를 공개 Python API 경계에서 비교한다. 지원 기능·내구성·완료량을 먼저 확인하고 성공 경로의 성능과 사용자 과제의 적합성을 구분한다. 모든 로컬 DB의 전수 비교나 종합 순위는 주장하지 않는다.

## 가설

| 가설 | 예측 | 적용 범위 |
|---|---|---|
| H1 | 실행 가능한 DB 조건에서 기록 ID·payload·두 레코드의 원자적 결합과 재개방 결과가 일치한다. | 설치·설정이 검증된 대표 DB |
| H2 | 실행 가능한 큐 조건에서 성공 경로의 중복·누락·payload 불일치 없이 완료하고 재개방 후 미완료 작업이 없다. | 설치·설정이 검증된 실제 큐 API |
| H3 | 명시한 동기화 설정과 실제 조회값이 일치하며 수집에 사용한 ToteDB는 비계측 빌드다. | 정상 성능 비교에 포함한 설정 |

성능의 우열은 탐색적으로 추정한다. FIFO가 ACK·재전달·fencing 큐보다 빠르다는 관측을 같은 보장의 비용 우위로 해석하지 않는다. 지원하지 않는 보장은 기능 차이로 기록하며 실패 속도나 0초로 처리하지 않는다.

## 비교 조건

| 축 | 대표 설정 | 같은 것으로 맞추는 것 | 같다고 주장하지 않는 것 |
|---|---|---|---|
| DB primitive | ToteDB·SQLite DELETE/FULL·SQLite WAL/FULL·DuckDB·LMDB·RocksDB | Python 단일 client, 실제 값·payload·논리 완료량·commit 경계 | SQL과 KV의 API·저장 모델, 전원 장애 내구성 동등성 |
| ACK 성공 경로 | ToteDB native Queue·persist-queue SQLiteAckQueue·Litequeue | put→get→ack/done, ID·본문 소비와 완료 확인 | lease·자동 재전달·stale ACK 거절·보존 정책의 동등성 |
| FIFO 성공 경로 | DiskCache Deque·SqliteHuey storage | put→꺼내기, ID·본문 소비와 큐 비움 확인 | ACK 없는 pop의 실패 복구와 ACK 큐의 보장 |

실제 패키지 버전은 설치 검사 후 별도 고정 파일로 커밋하고 수집한다. 사용할 수 없거나 동기화 설정을 확인하지 못한 패키지는 이유·원본 오류를 보존하고 해당 비교를 unavailable 또는 incomparable로 표시한다. 다른 구현으로 몰래 대체하지 않는다.

[고정 패키지](requirements.txt)는 실제 소규모 설치·API 검사에서 확인한 DuckDB 1.5.6, lmdb 3.0.0, rocksdict 0.3.29, persist-queue 1.1.0, litequeue 0.14, diskcache 5.6.3, Huey 3.4.0이다. 정식 Linux 설치의 wheel URL·digest·실제 버전은 pip 설치 report와 각 worker에 보존한다. 로컬 API 검사 시간은 정식 성능 표본이 아니다.

### DB 작업

논리 작업 수는 실행당 512개, payload는 256·4,096B다. 입력과 직렬화된 바이트는 측정 전에 준비한다.

| 작업 | 동작 | commit |
|---|---|---|
| write | 고유 ID·payload 레코드 하나 기록 | 작업마다 하나 |
| atomic-outbox | 업무 레코드와 대응 outbox 레코드 기록 | 레코드 쌍마다 하나 |
| atomic-outbox-batch | 같은 레코드 쌍을 묶어 기록 | 논리 작업 32개마다 하나 |

SQL은 명시적 트랜잭션을 사용하고 KV는 동일 환경의 transaction 또는 atomic WriteBatch를 사용한다. RocksDB의 batch를 경합을 검증하는 TransactionDB로 소개하지 않는다. 두 레코드의 정상 원자적 기록은 완성 ACK 큐·외부 exactly-once 처리의 구현을 뜻하지 않는다.

### 실제 큐 작업

작업 수 128·1,024개와 payload 본문 256·4,096B의 네 조건을 사용한다. 각 실행은 단일 client가 실제 라이브러리 API로 전부 put한 뒤 전부 꺼내고, ACK 계열은 ack/done까지 수행한다. JSON ID·본문을 소비하며 단순 호출 횟수만 완료로 세지 않는다.

put·get·ack 시간과 전체 성공 경로를 각각 기록한다. payload 본문 길이와 실제 직렬화된 전체 바이트를 구분한다. 라이브러리별 serializer·ACK 보존·purge·close·checkpoint 정책을 공개하고 측정 밖 작업을 무료라고 간주하지 않는다.

## 측정 지표

| 지표 | 정의 | 단위 |
|---|---|---|
| 처리량 | 정확성 검사를 통과한 논리 작업 수 / 측정 경계 elapsed | ops/s·jobs/s |
| 지연 | 실행별 API 또는 commit 지연 p50·p95·p99, 반복별 요약 | ms |
| 완료·audit | 실제 ID·값·반환 본문·중복·누락·재개방 검사 | 건 |
| 파일·정리 | 닫기·재개방·정리 단계와 파일 크기, 측정 밖 작업 별도 | byte·s |
| 기능 검사 | ACK·재전달·호출자 트랜잭션·lease/fence의 구현·미지원·미검증 | 상태 |

RSS 고수위 상속 문제를 배제하지 못하면 엔진 메모리 우위로 사용하지 않는다. 서로 다른 부하·보장 그룹의 처리량을 합친 종합 점수는 만들지 않는다.

## 실험 환경

| 항목 | 값 |
|---|---|
| 운영체제 | 전용 GitHub Actions Ubuntu 24.04 VM, 실제 kernel·runner·CPU·메모리·부하 기록 |
| 런타임 | Python 3.12와 고정 패키지, 실제 binding·엔진·SQLite 링크 버전 기록 |
| 빌드 | ToteDB -O2 -g 공유 라이브러리, BENCHMARK=0·sanitizer 없음 |
| 순서 | 설치·빌드·작은 정확성 검사 완료 후 DB 실측→큐 실측 직렬 수행 |
| 동기화 | SQLite FULL, LMDB sync/metasync 활성, RocksDB sync 활성·WAL 유지, DuckDB 기본 durable commit, 큐는 실제 설정 조회 |
| 파일 | 새 프로세스·새 DB·새 출력 경로, 원본·DB·stderr·설치 report·해시 보존 |
| 시드 | 입력은 결정적, 대응 bootstrap 244, 엔진 시작 순서 반복별 순환 |
| 모델 | 해당 없음 |

## 표본

| 축 | 조건 | 정식 | 사전 |
|---|---|---:|---:|
| DB | 6설정×3작업×2payload | 180 | 36 |
| 실제 큐 | 5종×2작업 수×2payload | 100 | 20 |
| 합계 | 설치 실패·미지원도 계획 분모에 보존 | 280 | 56 |

각 조건은 정식 5회와 별도 사전 1회다. 검정력 기반 표본 수가 아니라 수집 한도다. 실행당 제한은 180초·job 전체는 45분이다. 오류·timeout·불완전 결과를 보존하고 가능한 다음 조건을 계속한다. 느린 표본을 제거하거나 성공 표본으로 교체하지 않는다.

DB 수집기는 이 matrix 외에도 엔진당 N=4의 API 검사 여섯 회를 먼저 수행하고 별도 preflight에 보존한다. 큐의 retry·fail·재개방 capability probe도 시간 측정 밖이다. 이 자료를 정식 또는 사전 성능 반복의 수에 합산하지 않는다.

## 분석 방법

| 항목 | 규칙 |
|---|---|
| 가설 판정 | 계획한 실행의 완료·보장·audit 전수 확인, 누락은 보류·불일치는 기각, 모집단 오류율로 일반화 금지 |
| 성능 | 조건별 5회 중앙값·최소·최대, 같은 반복의 대응 비와 percentile bootstrap 10,000회·명목 95% 구간 |
| 비교 적격성 | 사전·정식 정확성·설정 검사 통과가 선행, 실패 조건의 비율은 incomparable |
| 다중 비교 | 모두 탐색적 추정, 보정 없이 선택한 구간으로 전체 우위·인과 효과 주장 금지 |
| 사전 실행 | 정식 성능 통계에서 제외하되 실패는 비교 적격성에 반영 |
| 재현 | 원자료·DB·설치 버전·설정·파일 집합·해시 검증, summary 재생성의 바이트 일치 |

## 원인 가설과 사용자 과제

관측→근거→가능한 원인→반증할 실험→판단을 연결한다. SQL 준비·여러 commit·serializer·라이브러리 상태기계·보존 정책과 저장 엔진 비용을 구분한다. 성공 경로에서 빠르다는 결과를 crash 복구나 원자적 outbox의 증명으로 바꾸지 않는다.

검증할 사용자 과제는 별도 broker 없이 SQL 업무 상태·작업 등록과 로컬 업무 효과·ACK를 각각 하나의 트랜잭션에 묶고 Python producer와 C worker가 공유하는 흐름이다. SQLite로도 구현 가능하므로 ToteDB만 가능한 기능이라고 주장하지 않는다. 기능 통합의 이점은 동일 보장의 구현·검증 비용 절감 가설이며 이번 처리량만으로 개발 시간 절감을 입증하지 않는다.

## 위험 요소와 대응

| 위험 요소 | 대응 |
|---|---|
| 전수 DB 비교라는 오해 | 조사 후보와 실제 설치·실측 후보를 나누고 선정·제외 사유 공개 |
| 서로 다른 보장을 한 순위에 배치 | SQL/KV primitive·ACK·FIFO 그룹과 capability를 분리 |
| 약한 기본 sync가 빠른 결과를 만듦 | 실제 설정 조회·기록, 확인 불가 조건 별도 표시 |
| 바인딩·직렬화·보존 비용 | 공개 API 경계·실제 바이트·측정 밖 작업을 명시 |
| 공유 host·짧은 반복 변동 | 같은 VM·직렬·순환 순서와 전체 반복 범위 보존 |
| 재개방 성공을 전원 장애 보존으로 해석 | 정상 재개방·프로세스 중단·OS crash·전원 장애를 구분 |
