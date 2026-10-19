# 로컬 DB 비교 후보와 ToteDB 사용 조건

ToteDB의 검증할 이점은 SQLite 자체의 대체보다 로컬 SQL 업무 저장과 ACK 큐를 같은 트랜잭션으로 묶는 통합 비용에 있다. 예를 들어 주문 저장과 작업 등록을 함께 확정하고, C worker가 로컬 처리 기록과 완료 확인(ACK)을 함께 확정하는 경우다. SQLite도 같은 기능을 구현할 수 있다. 어느 쪽이 실제로 빠르거나 도입 비용이 낮은지는 별도 실측과 사용자 과제로 판정한다. 이 문서는 SQL 9종, KV 10종, 객체·문서·플랫폼 계열 6종의 대표 후보를 정리하며 모든 로컬 DB를 망라하지 않는다.

공식 자료 확인일은 2026-10-19다. 버전은 공식 배포·문서의 확인값이며 이 환경에 설치된 버전이 아니다. 설치 성공, linked engine 버전과 실행 설정은 실측 기록에서 확인한다.

## SQL 9종의 실행 조건

| 후보 | API·저장·트랜잭션 | 비교 역할과 제약 | 공식 근거 |
|---|---|---|---|
| SQLite | C, Python 표준 `sqlite3`; 주 DB와 journal 또는 WAL·SHM; 명시적 트랜잭션 | OLTP 기준선. 여러 프로세스 접근 가능, writer는 한 번에 하나. Public domain | [개요](https://sqlite.org/about.html), [WAL](https://sqlite.org/wal.html), [Python](https://docs.python.org/3/library/sqlite3.html) |
| libSQL | SQLite fork; C·Rust 구성, Python SDK; 로컬 파일·트랜잭션 | 확장군. 로컬 쓰기와 cloud-primary replica 쓰기를 구분. MIT 및 기존 SQLite public domain | [엔진 구분](https://docs.turso.tech/libsql), [Python](https://docs.turso.tech/sdk/python/reference) |
| Turso Database | Rust 재작성; Python `pyturso`의 `turso.connect`; 로컬 파일·트랜잭션 | libSQL과 별도 엔진. 릴리스별 MVCC·다중 프로세스·sync 보장은 추가 확인. MIT | [Python](https://docs.turso.tech/sdk/python/reference), [기능 경고](https://docs.turso.tech/tursodb/quickstart) |
| DuckDB | C++, 공식 Python·C API; 주 DB·WAL; MVCC·명시적 트랜잭션 | OLAP 대조군. native 파일 쓰기는 한 프로세스 내 여러 연결·스레드 대상. 다중 프로세스 C/Python 큐의 동급 대체재가 아님. MIT | [설치](https://duckdb.org/install/), [동시성](https://duckdb.org/docs/current/connect/concurrency.html) |
| H2 | Java/JDBC; `.mv.db`와 보조 파일; 트랜잭션 | JVM 별도군. embedded는 같은 JVM, 프로세스 공유는 server/mixed 경로. MPL 2.0 또는 EPL 1.0 | [기능](https://raw.githubusercontent.com/h2database/h2database/master/h2/src/docsrc/html/features.html) |
| HSQLDB | Java/JDBC; 파일 묶음; 2PL·MVCC | JVM 별도군. Python bridge·JVM 시작·GC 비용을 별도 기록. BSD 기반 | [홈](https://hsqldb.org/), [배포](https://hsqldb.org/doc/2.0/guide/deployment-chapt.html) |
| Derby | Java embedded JDBC 또는 Network Server | 유지보수 종료로 신규 도입 권고에서 제외. 2025-10-10 이후 개발·수정·후속 릴리스 종료. Apache 2.0 | [종료 공지](https://db.apache.org/derby/) |
| PGlite | JS/TS·WASM Postgres; Node FS 또는 IndexedDB; transaction callback | JS runner 필요. 단일 DB 파일이 아니며 Python direct API·프로세스 공유 쓰기는 미확인. Apache 2.0 및 PostgreSQL license | [개요](https://pglite.dev/docs/about), [파일](https://pglite.dev/docs/filesystems), [API](https://pglite.dev/docs/api) |
| Firebird embedded | native·fbclient, Python `firebird-driver`; DB 파일 직접 개방·트랜잭션 | 확장군. shared 구성의 여러 embedded 프로세스 접근과 loopback fallback을 구분. IPL/IDPL | [접속·구성](https://firebirdsql.org/file/documentation/html/en/firebirddocs/qsg5/firebird-5-quickstartguide.html), [Python](https://firebirdsql.org/en/python-driver/) |

확인된 배포 표시는 SQLite 3.54.1, DuckDB current 1.5.6·LTS 1.4.5, H2 2.5.252, HSQLDB 2.7.4, Firebird 5.0.4다. DuckDB preview를 정식 2.0 출시로 읽지 않는다. Python `sqlite3.sqlite_version`은 SQLite 다운로드 버전과 별개다. ([SQLite](https://sqlite.org/download.html), [DuckDB](https://duckdb.org/install/), [H2](https://github.com/h2database/h2database/releases/tag/version-2.5.252), [HSQLDB](https://hsqldb.org/), [Firebird](https://firebirdsql.org/en/firebird-5-0/))

Turso v0.8.3과 libSQL 서버 태그 `libsql-server-v0.24.32`는 서로 다른 배포다. 서버 태그를 Python embedded 버전으로 쓰지 않는다. Turso 문서의 production-ready 표현과 libSQL README의 새 Turso beta 표현이 상충한다. 일부 기능의 experimental 경고도 있어 API 이름만으로 성숙도·보장을 확정하지 않는다. PGlite core manifest의 0.5.10 역시 최신 npm 설치 성공의 증거가 아니다. ([Turso 릴리스](https://github.com/tursodatabase/turso/releases/tag/v0.8.3), [libSQL 릴리스](https://github.com/tursodatabase/libsql/releases/tag/libsql-server-v0.24.32), [README](https://github.com/tursodatabase/libsql), [PGlite manifest](https://raw.githubusercontent.com/electric-sql/pglite/main/packages/pglite/package.json))

## KV 10종의 원자성 범위

| 후보 | 저장·원자성 | Python 경로와 비교 판단 | 공식 근거 |
|---|---|---|---|
| LMDB | C, B+tree·MVCC; 데이터·lock 파일; 단일 writer, 한 환경의 여러 key·named DB 트랜잭션 | `lmdb`; 1차 대표. `subdir=False`도 lock 파일을 유지 | [API](https://lmdb.readthedocs.io/en/release/) |
| RocksDB | C++, LSM; WAL·SST·manifest 디렉터리; atomic WriteBatch | `rocksdict`; 1차 대표. Rdict·WriteBatch와 별도 TransactionDB의 계약 구분 | [트랜잭션](https://github.com/facebook/rocksdb/wiki/Transactions), [바인딩](https://rocksdict.github.io/RocksDict/rocksdict.html) |
| LevelDB | C++, 디렉터리; atomic WriteBatch·snapshot | `plyvel`; 매우 제한된 유지보수. archived라는 뜻은 아님 | [공식](https://github.com/google/leveldb) |
| redb | Rust, 단일 데이터 파일; COW B+tree·ACID·MVCC | 현행 Rust와 Python `redb` 배포의 세대 차이 확인 필요 | [공식](https://github.com/cberner/redb), [Python](https://pypi.org/project/redb/) |
| sled | Rust, 경로 기반; optimistic multi-key/tree 트랜잭션·CAS | beta/unstable·rewrite 경고. 현행 Python binding과 엄밀한 파일 구성 미확인 | [공식](https://github.com/spacejam/sled) |
| bbolt | Go, 단일 파일; 단일 writer·snapshot 트랜잭션 | Go 별도 runner 후보. 현행 공식 Python binding 미확인 | [공식](https://github.com/etcd-io/bbolt) |
| Badger | Go, LSM·value log 디렉터리; concurrent ACID/SSI 트랜잭션 | Go 별도 runner 후보. sync 설정 명시 필요, Python 경로 미확인 | [공식](https://github.com/dgraph-io/badger), [설정](https://github.com/dgraph-io/badger/blob/main/options.go) |
| Pebble | Go, 디렉터리; atomic batch·indexed batch·snapshot | Go 별도군. 상위 CockroachDB의 SQL·트랜잭션과 엔진 기능 구분 | [공식](https://github.com/cockroachdb/pebble) |
| Berkeley DB | C 계열, 데이터·환경·log 파일; locking·WAL ACID 트랜잭션 | `berkeleydb`, native 의존성 확인 필요. KV API와 별도 SQL interface를 구분 | [개요](https://docs.oracle.com/database/bdb181/html/programmer_reference/intro_dbis.html), [SQL 빌드](https://docs.oracle.com/database/bdb181/html/installation/build_unix_sql.html) |
| UnQLite | C, portable 주 DB 파일; ACID begin/commit/rollback | Python `unqlite`; Jx9 문서 스크립트는 SQL이 아님. 트랜잭션 보조 파일과 주 DB를 구분 | [공식](https://github.com/Symisc/unqlite), [commit](https://unqlite.symisc.net/c_api/unqlite_commit.html) |

KV 표는 Berkeley DB의 별도 SQL interface를 제외하면 SQL API 비교가 아니다. 두 key를 한 번에 쓰는 기능은 업무 기록과 outbox(후속 작업 등록)를 함께 저장하는 재료다. claim·lease(처리권 만료)·fence(옛 처리권의 변경 차단)·ACK 상태기계까지 자동 제공한다는 뜻은 아니다. 특히 RocksDB WriteBatch는 앞선 `get`과 판단 사이의 경합을 검증하지 않는다. ([RocksDB](https://github.com/facebook/rocksdb/wiki/Transactions))

공식 배포 확인값은 Python `lmdb` 3.0.0, `rocksdict` 0.3.29다. 바인딩 버전과 내부 native 엔진 버전은 별도로 기록한다. 나머지 KV의 라이선스 세부, 플랫폼별 wheel·native 의존성, 지원 수명은 도입 전 추가 확인 대상이다. ([lmdb 배포](https://pypi.org/project/lmdb/), [rocksdict 배포](https://pypi.org/project/rocksdict/))

## 객체·문서·플랫폼 계열 6종

| 후보 | 해결하는 요구 | 같은 C/Python SQL 측정에 바로 넣지 않는 이유 | 공식 근거 |
|---|---|---|---|
| LiteDB | .NET embedded BSON 문서·collection, 단일 주 데이터 파일 | .NET runner와 문서 모델 매핑 필요. 일부 SQL 문법과 관계형 SQL 전체 계약 구분 | [문서](https://www.litedb.org/docs/) |
| Realm | 모바일 객체 DB·언어별 SDK | 지원 SDK·버전·schema·트랜잭션을 먼저 고름. core 자체는 안정된 end-user API가 아니며 sync 서비스 수명은 별도 확인 | [core](https://github.com/realm/realm-core) |
| ObjectBox | 객체·vector 저장, Python 및 Linux 경로 | 모바일 전용이 아님. 객체 schema·binding·ABI·프로세스 공유 계약을 확인한 별도 runner 후보 | [문서](https://docs.objectbox.io/) |
| IndexedDB | 브라우저의 비동기 object store 트랜잭션 | browser runner·same-origin·quota·eviction·durability 통제 필요. 여러 store의 원자 변경 자체는 가능 | [표준](https://w3c.github.io/IndexedDB/) |
| RxDB | JS reactive/local-first 데이터 계층 | 독립 엔진이 아닌 RxStorage·backend 조합. backend·유료 옵션 고정 필요 | [storage](https://rxdb.info/rx-storage.html) |
| PouchDB | JS 문서 저장·replication | `bulkDocs`는 일부 성공 가능. 업무·outbox 두 문서의 원자 변경은 같은 계약이 아님 | [bulk operations](https://pouchdb.com/guides/bulk-operations.html) |

로컬 PostgreSQL·MySQL 서버, H2 AUTO_SERVER와 DuckDB의 서버 경유 쓰기는 서버 없는 in-process 주 비교에서 제외한다. 제외는 느리다는 판정이 아니다. 브라우저 수명, replication, 객체·vector 검색이 실제 요구라면 별도군이 오히려 먼저 검토할 대상이다.

## 실측 대상과 실제 Python 큐 패키지

1차 DB 비교 대상은 ToteDB와 SQLite DELETE, SQLite WAL, DuckDB, LMDB, RocksDB의 6개 설정이다. SQLite를 한 엔진으로 세면 외부 엔진은 4종이다. SQL counter 갱신은 SQL 지원 대상끼리, 업무 기록·outbox의 한 commit 저장은 SQL의 두 행과 KV의 두 key를 구분해 비교한다. LMDB와 RocksDB에 새 큐 adapter를 붙여 완성 큐 성능인 것처럼 보고하지 않는다.

| 실제 패키지·비교 층 | 공식 API에서 확인한 동작 | 동등 계약 판정에 남은 확인 | 공식 근거 |
|---|---|---|---|
| `persist-queue`의 `SQLiteAckQueue` | `get` 뒤 ACK 상태 변경. v1.1.0 소스의 내부 connection·commit 관리, `auto_commit=True` 강제 | 설치 pin·sync·복구 정책. caller-owned 업무 트랜잭션과 별도 경계 | [고정 소스](https://raw.githubusercontent.com/peter-wangxu/persist-queue/v1.1.0/persistqueue/sqlackqueue.py), [connection](https://raw.githubusercontent.com/peter-wangxu/persist-queue/v1.1.0/persistqueue/sqlbase.py) |
| Litequeue | 현재 main README는 `pop`·`done(message_id, claim_id)`·`retry_expired`·멱등 등록을 설명 | 설치 릴리스의 ACK 인자·claim 계약·durability 확인. main과 배포판 구분 | [공식 README](https://github.com/litements/litequeue) |
| DiskCache `Deque` | SQLite·파일 기반 `append`·`popleft`; 프로세스 공유·영속 deque, transaction 제공 | ACK 큐가 아닌 제거형 기준선. 기본 WAL/NORMAL과 FULL을 구분하고 큰 값 외부 파일 정책 확인 | [Deque·transaction·설정](https://grantjenks.com/docs/diskcache/tutorial.html) |
| `SqliteHuey` storage | SQLite 큐·결과 저장, `fsync` 옵션 | storage enqueue/dequeue와 전체 task runner를 분리. 설치 버전의 dequeue·ACK·재시도·sync 경계 확인 | [공식 API](https://huey.readthedocs.io/en/latest/api.html#SqliteHuey) |
| ToteDB native `Database`·`Queue` | 업무 SQL과 `put` 또는 fence ACK에 같은 `txn` 전달 | durable ON·현재 빌드·실행 audit 기록. compat API와 native outbox 계약 구분 | [Queue](../../../python/totedb/queue.py), [C API](../../../include/totedb.h) |

이 네 SQLite 패키지는 자체 제작한 SQLite 큐 대조군과 별개의 실측 계획 대상이다. 설치 버전, 기본값 변경, 성공 처리의 정의를 고정하기 전에는 성능 순위를 만들지 않는다. Litequeue main은 claim ID로 stale 변경을 차단한다고 명시하지만, 같은 README는 업무 테이블과 공유하는 DB를 지원하지 않는다고 명시한다. fence 유무와 caller-owned 업무 트랜잭션 결합은 다른 조건이다. ([Litequeue](https://github.com/litements/litequeue))

| 기능 계약 | Tote native | Tote compat | SQLite 직접 구현 | 실제 패키지·KV 기준선 |
|---|---|---|---|---|
| 업무 SQL과 enqueue의 한 commit | 같은 `txn` API 제공 | `put`에 txn 미노출 | 같은 connection의 트랜잭션으로 구현 가능 | persist-queue v1.1.0 공개 API 그대로는 불가. 나머지는 별도 확인·모델 구분 |
| 로컬 업무효과와 fence ACK의 한 commit | 같은 `txn` API·예제 제공 | native 결합 근거 아님 | 상태·조건부 갱신을 직접 구현 | KV atomic batch만으로 완성 큐 지원 판정 불가 |
| lease·stale fence 차단 | native API 제공 | 원본 persist-queue와 의미 차이 | adapter 필요 | persist-queue는 동일 계약 아님. Litequeue main claim ID 제공, 설치판 미확인 |
| C worker와 Python producer | 공개 C API·Python wrapper·예제 제공 | Python 호환층 | C·Python SQLite와 자체 상태기계로 구현 가능 | Python 패키지 내부 schema에 C로 접근할 때 공개 ABI 계약 별도 확인 |

Tote native·compat의 근거는 [Queue](../../../python/totedb/queue.py), [compat](../../../python/totedb/persistqueue_compat.py), [Python producer](../../../examples/outbox/run.py), [C worker](../../../examples/outbox/consumer.c)다. SQLite의 원자성 근거는 [트랜잭션 문서](https://sqlite.org/lang_transaction.html)다. 위 표는 API·소스 계약의 분류이며 이번 실행 성공이나 모든 장애 조건의 검증 결과가 아니다.

## ToteDB의 채택 가설과 반증 조건

| 사용자 과제 | 검증할 이익 | 반증 조건 |
|---|---|---|
| 로컬 주문 접수와 후속 작업 등록 | 업무-only·outbox-only 상태 없이 한 commit 처리. SQLite 동등 계약 대비 앱 소유 큐 코드·통합 시간·운영 절차 감소 | SQLite outbox가 같은 보장을 더 낮은 총비용에 충족하거나 원자성 문제가 실제로 없음 |
| 한 호스트의 Python producer와 C worker | worker 종료 후 lease 복구, stale ACK 거절, 로컬 처리 기록·ACK 결합을 공개 API로 사용 | C/Python 혼용 수요가 없거나 POSIX single-writer 경합·버전 변경 비용이 통합 이익보다 큼 |
| 간헐 연결 Linux edge 장치의 업로드 대기 큐 | 업무 데이터와 전송 의도 동시 저장, 재시도 후 잔량·디스크 증가·중복 수신을 추적 | 장치 간 sync·충돌 해결 요구 또는 수신 측 멱등 처리 불가 |

이 과제들은 사용 수요가 입증된 사례가 아니라 현재 API·예제에 맞춘 검증 가설이다. C worker 예제의 원자적 효과는 DB 안의 출고 기록이다. 택배 API·메일·결제 같은 외부 효과까지 exactly-once로 만들지는 않는다. ToteDB의 안전성 문서는 POSIX·파일 잠금·single-writer 전제를 두며 전원 차단·네트워크 FS·Linux 외 OS는 검증 범위 밖이다. ([worker](../../../examples/outbox/consumer.c), [안전성](../../safety.md))

## 판정 경계와 후속 문서

- 이 파일 `catalog.md`는 후보와 계약을 정리한 수동 작성 정본이며 실측 결과 파일이 아니다.
- 후속 [실험 계획](design.md)은 대상 버전·입력·동일 결과·내구성·반복·실패 처리 기준을 고정하는 문서다. 계획과 결과 작성은 별도 범위다.
- 후속 [실험 결과](report.md)는 실제 설치 버전·원자료·audit와 성능 판정을 담는 문서다. 이 목록에는 측정값을 전재하지 않는다.
- [기존 배포 비용 결과](../release-cost/report.md)는 질문을 정한 배경이다. 다른 호스트·버전·실행의 수치를 합산하거나 환경 간 배수를 만들지 않는다.
- commit 반환·정상 close/reopen·프로세스 중단·OS crash·전원 상실은 다른 검사다. 재개방 성공만으로 전원 장애 보존을 주장하지 않는다.
- SQLite FULL·LMDB sync·RocksDB sync와 WAL 설정을 기록한다. 기본 NORMAL·비동기·주기 flush를 durable commit과 같은 순위에 섞지 않는다. ([SQLite](https://sqlite.org/pragma.html#pragma_synchronous), [LMDB](https://lmdb.readthedocs.io/en/release/), [RocksDB](https://github.com/facebook/rocksdb/blob/main/include/rocksdb/options.h))
- 작은 point read·counter·두 기록 commit은 OLAP scan/join, 다중 consumer 안전성, 긴 운영 기간, 모든 SQL·KV 성능을 증명하지 않는다. SQL·binding·직렬화 비용을 엔진 코어 성능으로 일반화하지 않는다.
- 공식 URL·API·배포 상태는 확인일의 근거다. 패키지 설치·crash 실험·사용자 수요·개발 시간 절감은 이 문서에서 확인하지 않았다. 삭제하면 후보 선정 근거가 사라지며 결과 재생성에는 영향을 주지 않는다.
