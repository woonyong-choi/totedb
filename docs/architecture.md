# 아키텍처

ToteDB는 애플리케이션 프로세스 안에서 SQL을 실행하고 테이블을 DB 파일에 저장하는 C11 데이터베이스다. 주문 상태를 바꾸는 `UPDATE`는 Python의 `Database.execute`에서 C API, 세션, SQL 실행기를 거쳐 행 저장소에 도달한다. 선택 기능인 ACK 큐도 같은 세션의 SQL과 트랜잭션을 사용하므로 업무 행과 큐 작업을 한 DB 거래에서 함께 바꿀 수 있다.

DB 파일을 다시 열면 제어 페이지가 가리키는 카탈로그에서 테이블의 행 저장소와 색인 루트를 찾는다. 행 저장소의 페이지 디렉터리는 실제 행 페이지(record page)를 가리키고, 행 페이지 안의 slot이 행을 식별한다. 색인은 물리 행 페이지 번호를 직접 저장하지 않고 디렉터리 순번과 slot을 저장한다. 큰 TEXT와 BLOB은 행에서 참조하는 별도 overflow 체인에 둔다.

변경 문장은 게시된 페이지를 덮어쓰지 않고 새 페이지와 작업 카탈로그에 변경을 모은다. 최종 commit은 새 카탈로그와 회수 목록을 준비하고 제어 페이지로 새 상태를 게시한다. 쓰기 거래 전체를 직렬화하는 잠금 파일과 게시 순간에 사용하는 DB 파일 잠금은 대상이 다르다. 다른 핸들의 읽는 문장은 쓰기 준비 중에는 이전 게시 상태를 읽을 수 있지만, 제어 페이지 게시와는 서로 기다린다.

이 문서는 `1ae11543`의 구조를 설명한다. 첫 실행은 [README](../README.md#빠른-시작), 큐 기능의 상태와 사용 경계는 [주문 저장과 작업 등록을 한 트랜잭션으로 처리하기](design/transaction-queue.md)가 맡는다. SQL만 쓰는 독자는 큐 예제를 실행할 필요가 없다.

## 외부 연결

| 외부 요소 | 종류 | 주고받는 것 |
|---|---|---|
| C 애플리케이션 | 외부 프로그램 | `tote_*` 호출, SQL 바인딩, 소유권을 넘겨받는 결과 버퍼 |
| Python 애플리케이션 | 외부 프로그램 | `Database`·`Transaction`·`Queue` 호출과 Python 값, ctypes를 통한 공유 라이브러리 호출 |
| DB 파일 | 파일 | 8192바이트 페이지, 제어 슬롯 두 장, 카탈로그와 행·색인·회수 목록 |
| writer 잠금 파일 | 파일 | DB 옆 `<db>-lock`의 writer `flock`과 게시 대기 표시 |
| 운영체제 | 운영체제 | `pread`·`pwrite`·`fsync`, 파일 잠금, pthread 동기화 |

호출은 모두 같은 프로세스 안에서 끝난다. 큐를 열어도 별도 서버나 워커 프로세스를 자동으로 시작하지 않으며, 외부 HTTP나 파일 쓰기는 DB 거래에 포함하지 않는다.

## 구성 요소

<picture>
<source media="(max-width: 600px)" srcset="assets/architecture-narrow.svg" width="210" height="675">
<img src="assets/architecture.svg" alt="설계: 선택 큐와 SQL 호출은 세션에서 합류하고 database가 행과 색인의 루트를 함께 게시한다" width="594">
</picture>

그림의 선은 호출 방향이다. DB 실행에는 세션·연결·SQL 실행기와 `database` 작업 상태를, 파일 게시에는 `catalog_store`와 `page_file`을 합쳤다. 반환 버퍼와 batch의 새 루트를 호출자에게 돌려주는 선, 실행기 내부의 구문·식 평가 단계는 생략한다. 루트와 페이지 집합의 소유권 인계는 아래 처리 흐름에서 구분한다.

| 구성 요소 | 하는 일과 입출력 | 기술 | 위치 |
|---|---|---|---|
| `totedb.Database`·`Transaction`·`Queue` | Python 값을 C 바인딩으로 포장하고 결과를 Python 값으로 복사한 뒤 native 버퍼 해제 | Python, ctypes | [database.py](../python/totedb/database.py), [queue.py](../python/totedb/queue.py) |
| `tote_db_t`·`tote_exec` | DB mutex와 기본 세션 소유, SQL 결과를 독립 소유 버퍼로 인코딩 | C11, pthread | [totedb.c](../src/capi/totedb.c), [totedb.h](../include/totedb.h) |
| `ack_queue` | 큐 연산을 SQL로 실행, 호출자 거래 또는 자체 거래 선택 | C11 | [ack_queue.c](../src/queue/ack_queue.c), [ack_queue_sql.c](../src/queue/ack_queue_sql.c) |
| `session`·`database_conn` | 연결별 거래 관리, 실행 호출 직렬화, 예산·취소와 결과 수명 관리 | C11, pthread | [session.c](../src/sql/session.c), [database_txn.c](../src/sql/database_txn.c) |
| `lexer`·`sql_statement`·`sql_eval`·`database_*` | SQL과 바인딩을 해석하고 조건을 평가해 행·색인 연산 및 내부 `result_t` 생성 | C11 | [lexer.c](../src/sql/lexer.c), [sql_statement.c](../src/sql/sql_statement.c), [sql_eval.c](../src/sql/sql_eval.c), [database_select.c](../src/sql/database_select.c), [database_update.c](../src/sql/database_update.c), [result.c](../src/sql/result.c) |
| `heap_store`·`pk_tree`·`text_tree` | 행·큰 값·색인의 COW 변경, 새 루트와 fresh·old 페이지 집합 반환 | C11 | [heap_store.c](../src/storage/heap_store.c), [pk_tree.c](../src/storage/pk_tree.c), [text_tree.c](../src/storage/text_tree.c) |
| `database`·`transaction` | 게시·작업 카탈로그와 페이지 집합 소유, writer 자리와 commit 결과 관리 | C11 | [database.c](../src/sql/database.c), [database_txn.c](../src/sql/database_txn.c), [transaction.c](../src/storage/transaction.c) |
| `catalog`·`catalog_store` | 테이블·색인 루트 묶음을 직렬화해 overflow에 쓰고 제어 참조 게시 | C11 | [catalog.c](../src/storage/catalog.c), [catalog_store.c](../src/storage/catalog_store.c) |
| `page_file`·`page_cache` | 물리 페이지 I/O와 CRC32C 검사, 잠금·제어 슬롯·회수 상태 및 읽기 캐시 관리 | C11, POSIX | [page_file.c](../src/storage/page_file.c), [page_cache.c](../src/storage/page_cache.c) |

### 저장 참조

<picture>
<source media="(max-width: 600px)" srcset="assets/storage-layout-narrow.svg" width="208" height="699">
<img src="assets/storage-layout.svg" alt="설계: 제어 페이지의 카탈로그 참조에서 디렉터리와 행 페이지를 거쳐 큰 값의 overflow까지 읽는다" width="689">
</picture>

파일의 제어 슬롯 두 장 중 선택된 `page_file_control_t`가 게시 상태의 입구다. `catalog_head`와 `catalog_size`는 table ID 0인 카탈로그 overflow 체인을 가리킨다. 카탈로그를 읽으면 테이블 schema와 `heap_root`, 기본 키 루트, 보조 색인 루트를 얻는다. 제어 상태가 보관하는 reusable·aging 목록은 지금 재사용할 페이지와 아직 이전 세대를 위해 남겨 둘 페이지를 구분한다.

`heap_root`는 행 페이지가 아니라 페이지 디렉터리의 루트다. 작은 디렉터리는 `LHD3` 잎 한 장이고, 커지면 `LHD2` 루트 아래 `LHD3` 잎들을 둔다. 잎 항목은 물리 행 페이지 ID와 빈 공간 힌트를 담으며, 항목의 순서가 `directory_index`다. 빈 공간은 삽입 후보를 고르는 힌트일 뿐이며 실제 삽입 전 행 페이지에서 다시 확인한다.

색인 잎의 값인 `heap_locator_t`는 `directory_index`와 `slot`이다. 조회는 디렉터리 순번으로 물리 페이지를 고른 뒤 그 페이지의 slot에서 행을 읽는다. 행 페이지를 COW로 교체해도 디렉터리 순번과 slot이 같으면 색인 locator는 유지할 수 있다. 반대로 `UPDATE`가 행을 다른 위치로 옮기면 새 locator를 색인에도 넣는다. locator는 연 heap이나 batch의 수명 안에서만 쓰며 commit 뒤에는 다시 연 heap에서 얻는다.

<picture>
<source media="(max-width: 600px)" srcset="assets/storage-index-locator-narrow.svg" width="208" height="425">
<img src="assets/storage-index-locator.svg" alt="설계: 색인은 물리 행 페이지를 직접 가리키지 않고 디렉터리 순번과 slot으로 행을 찾는다" width="670">
</picture>

행에 다 담지 않는 큰 TEXT·BLOB은 `head`와 `size`로 별도 overflow 체인을 참조한다. 카탈로그와 큰 값은 같은 overflow 모듈을 쓰지만 table ID와 참조 주체가 다르다. 형식의 필드와 검사 조건은 [heap_store.h](../include/storage/heap_store.h), [pk_tree.h](../include/storage/pk_tree.h), [page_file.h](../include/storage/page_file.h)에 있다.

세대를 뜻하는 값과 참조 검사용 값을 합치지 않는다.

| 값 | 의미 |
|---|---|
| `control.generation` | 실제 `page_file_commit`마다 증가하는 물리 게시 세대, 나머지 연산 `% 2`로 제어 슬롯 선택 |
| `catalog_generation` | 테이블·색인 루트 등 카탈로그 변경의 식별값, 제어 상태와 decode한 카탈로그 사이 일치 검사 |
| `commit_seq`·`epoch` | 일반 transaction manager의 거래 식별 영역, 제품 backend가 파일에 저장하는 게시 세대가 아님 |
| 색인 root stamp | 대응하는 `heap_root`의 물리 페이지 ID, 세대 카운터가 아님 |

기본 키 색인의 stamp만 어긋나면 heap은 그 색인을 사용하지 않고 스캔한다. 기본 키 루트 자체를 읽지 못하는 오류까지 무시하지는 않는다. 보조 색인의 stamp 불일치는 `HEAP_CORRUPT`다. 두 경우를 모두 자동 재구축으로 설명하면 안 된다. 해당 분기는 [heap_store.c](../src/storage/heap_store.c)의 `setup_index`와 보조 색인 검사에 있다.

## 처리 흐름

### 읽기와 다음 문장의 재적재

1. `database_txn`의 자동 실행은 `SELECT`에도 먼저 writer 자리를 시도한다. writer가 바쁘거나 timeout이면 읽기 경로로 전환하고, 변경 문장은 그 경로에서 거절한다.
2. 읽기 경로의 `database`는 문장 동안 DB 파일의 SHARED 잠금을 보유한다. `page_file_lock`은 디스크의 제어 세대를 확인하고 바뀌었으면 빈 목록과 페이지 캐시를 재설정한다.
3. `database`는 세대가 바뀌면 카탈로그를 다시 적재하고 SQL 실행기는 사용할 색인이나 heap 스캔으로 행을 찾는다. 색인으로 찾은 행에도 SQL 조건을 평가한다.
4. `database`는 문장 종료 때 SHARED 잠금을 놓는다. 다음 문장은 다른 핸들이 게시한 새 세대를 읽을 수 있으므로 여러 문장에 걸친 고정 read-only snapshot으로 해석하지 않는다.

<picture>
<source media="(max-width: 600px)" srcset="assets/read-publish-locks-narrow.svg" width="230" height="942">
<img src="assets/read-publish-locks.svg" alt="설계: writer 잠금과 DB 파일 잠금은 대상이 달라 쓰기 준비 중 읽을 수 있지만 게시 순간에는 서로 기다린다" width="624">
</picture>

[잠금 보유와 대기 재생](assets/read-publish-locks.html)은 독립 핸들의 writer 작업, 게시 대기, 다음 reader의 재적재를 같은 잠금 객체 위에서 보여 준다. writer는 거래 내내 `<db>-lock`을 쥐지만 DB 파일 EXCLUSIVE는 제어 write와 최종 fsync 구간에만 쥔다. 게시 전 intent 바이트는 새 reader의 양보를 돕는 표시이고, 정확성을 지키는 잠금은 `flock`이다. 표시만으로 기아 방지를 보장하지 않는다.

같은 핸들의 호출은 별도 문제다. C API는 DB mutex로 실행과 결과 인코딩을 직렬화하고, 같은 core database를 공유하는 세션은 run mutex로 문장·commit·rollback 호출을 직렬화한다. run mutex를 열린 거래 전체 동안 보유하지는 않는다. 직접 core 핸들을 호출하는 코드와 최종 close를 수행하는 코드는 호출·수명을 직렬화할 책임이 있다. 내부 mutex가 이미 해제된 포인터의 새 접근까지 막지는 않는다.

### 문장 batch와 거래 작업 상태

1. `database_work_begin`은 거래가 끝날 때까지 writer 잠금을 보유한다. `database`는 각 문장의 기준 카탈로그를 복제하며, 거래 안에서는 자기 작업 카탈로그를 기준으로 삼는다.
2. `heap_batch_t`는 변경한 행 페이지와 큰 값·색인 페이지를 새 물리 페이지에 쓴다. COW(copy-on-write)는 게시 상태가 읽는 페이지를 그대로 두고 바뀐 부분을 새 페이지에 쓰는 방식이다. 색인도 이 batch가 새로 만든 노드만 제자리에서 고친다.
3. `heap_batch_finish`는 바뀐 디렉터리를 쓰고 새 `heap_root`에 색인 stamp를 맞춘다. `LHD2` 트리의 안 바뀐 잎은 공유한다. 반환값은 새 heap·기본 키·보조 색인 루트와 fresh·old 페이지 집합이며, heap과 tree는 최종 commit을 호출하지 않는다.
4. `database`의 `publish_inner`와 `stage_adopt`는 루트 묶음을 작업 카탈로그로 채택한다. 앞 문장이 만든 미게시 fresh 페이지 중 더는 쓰지 않는 것은 즉시 돌려주고, 게시 세대의 퇴역 페이지는 `old_committed`에 모은다. 이 시점의 문장 성공은 아직 파일 게시가 아니다.

명시적 거래에서 다음 문장은 자기 변경을 읽지만 다른 세션은 게시 상태를 읽는다. 문장 오류가 나면 작업 상태에 `failed`를 세워 후속 문장과 commit을 거절하므로 호출자는 rollback으로 거래를 끝낸다. `transaction` manager에 일반적인 snapshot·reader horizon 기능이 있어도 제품 backend의 `reclaim`은 `NULL`이고 `READ_ONLY` 거래는 거절한다. SQL 페이지 회수를 그 horizon에 연결하지 않는다.

### 최종 게시와 결과 전달

<picture>
<source media="(max-width: 600px)" srcset="assets/storage-publication-narrow.svg" width="208" height="583">
<img src="assets/storage-publication.svg" alt="설계: 작업 카탈로그의 새 루트는 제어 페이지 게시가 성공한 뒤에만 게시 상태로 채택한다" width="692">
</picture>

[참조 교체 재생](assets/storage-publication.html)은 같은 구조에서 manifest 경로와 data barrier 경로를 비교한다. 그림의 old·new와 재생 시간은 설명용이며 실행 관측값이 아니다.

1. `database_work_commit`은 `old_committed`를 먼저 release해 pending으로 등록한다. 작업 카탈로그가 없으면 물리 commit 없이 `APPLIED`를 반환한다.
2. `catalog_store_save`는 새 카탈로그 overflow를 쓰고 옛 카탈로그 체인의 회수를 등록한 뒤 새 카탈로그 참조를 stage한다. `page_file_commit`은 이 참조와 다음 빈 목록을 담을 제어 페이지를 준비한다. 옛 페이지의 release는 게시 뒤가 아니라 이 준비 단계의 pending 등록이다.
3. `page_file_commit`은 아래 조건에 따라 데이터 장벽을 선택한 뒤 DB 파일 EXCLUSIVE를 얻는다. 이미 읽고 있는 문장이 있으면 그 문장의 SHARED 해제를 기다린다.
4. `page_file_commit`은 제어 쓰기 시도 직전에 결과를 `UNKNOWN`으로 바꾸고 다음 슬롯에 제어 페이지를 쓴다. 최종 `fsync` 성공 뒤에만 `APPLIED`로 바꾸고 메모리 제어 상태와 회수 목록을 승격한다.
5. `database_work_end`는 성공한 작업 카탈로그를 게시 카탈로그로 채택하고 거래 작업 메모리를 정리한 뒤 writer 잠금을 놓는다. C API는 그 뒤 내부 결과를 바이트 버퍼로 인코딩해 호출자에게 넘긴다.

| 게시 경로 | 조건 | 파일 동기화 |
|---|---|---|
| manifest | 이번 기록이 모두 fresh이고 manifest 용량 256개 이내, 게시 페이지 overwrite 없음 | 제어 쓰기 뒤 데이터와 제어를 함께 `fsync` 1회 |
| data barrier | manifest 용량 초과 또는 게시 페이지 overwrite로 `needs_barrier` 설정 | 데이터 `fsync` 뒤 제어 쓰기와 최종 `fsync`, 총 2회 |

빈 목록 체인의 쓰기도 이 판정에 포함한다. 1회 경로는 측정한 속도 우위가 아니라 [page_file.c](../src/storage/page_file.c)의 `note_write`와 `page_file_commit` 분기다. 제품 저장 계층의 COW가 전제이며 raw `page_file_write`의 임의 다중 페이지 overwrite까지 원자적 거래로 보장하지 않는다.

제어 쓰기 시도 전 실패는 `NOT_APPLIED`지만 data fsync 실패처럼 핸들이 poison일 수 있다. `NOT_APPLIED`는 게시 결과이고 poison은 그 핸들을 계속 쓸 수 있는지의 상태다. 제어 write나 최종 fsync가 실패하면 결과는 `UNKNOWN`이고 새·옛 페이지 모두 보존한다. 호출자는 쓰기를 중단하고 자원을 정리해 DB를 닫은 뒤 다시 열어 업무 키로 실제 상태를 확인한다. 오류별 외부 표현은 [totedb.h](../include/totedb.h), SQL 경로는 [database_txn.c](../src/sql/database_txn.c)에 있다.

`catalog_store_save`는 미반영이면 새 체인을 놓고 staged 참조와 pending을 복원하지만 불확정이면 어느 체인도 회수하지 않는다. 옛 카탈로그 공간 회수만 실패한 `UNRECLAIMED`는 게시 성공으로 취급한다. 이는 게시 실패와 다르다.

결과 전달도 별도 성공 경계다. `tote_exec`의 결과 인코딩에서 크기 제한이나 메모리 부족이 나면 변경을 이미 commit했어도 버퍼를 받지 못할 수 있다. 따라서 버퍼 부재만으로 미반영을 판단하거나 쓰기를 무조건 재실행하지 않는다. 성공 버퍼는 호출자 소유이며 `tote_free`로 해제한다. Python은 내용을 복사하고 native 버퍼를 해제한다. 입력 바인딩은 동기 호출 동안 빌리고 parser가 필요한 값을 복제한다.

### 재개방과 조건부 fallback

1. `page_file_open`은 두 제어 슬롯을 decode하고 각 세대와 슬롯의 parity를 검사한다. 두 슬롯이 모두 유효하면 세대 차이가 1인지 확인하고 최신 슬롯을 고른다. 한 슬롯이라도 지원하지 않는 형식이면 다른 슬롯으로 우회하지 않는다.
2. `commit_complete`는 선택한 제어 상태의 파일 길이와 manifest에 든 페이지의 CRC·envelope를 검사한다. 이 검사가 `CORRUPT`일 때만 이전 슬롯을 다시 선택하고 같은 검사를 한다. I/O 실패는 fallback 조건이 아니다.
3. `page_file`은 선택 상태의 reusable·aging 목록과 빈 목록 체인을 복원하고 `database`는 카탈로그를 적재한다. 카탈로그 세대 불일치나 이 후속 적재 실패 뒤에는 다른 제어 슬롯으로 재선택하지 않는다.

manifest는 이번 기록 집합이지 전체 도달 페이지의 무결성 증명이 아니다. 최신 제어 쓰기 자체가 장치에서 사라져 이전의 자기 일관된 값만 남으면 손상을 탐지하지 못할 수도 있다. 프로세스 종료와 I/O 주입 시험의 범위는 [안전성 시험](safety.md)에 있으며, 전원 차단·장치 lost write·네트워크 파일시스템 검증으로 확대하지 않는다.

## 지켜야 할 규칙

- 파일의 writer는 한 번에 하나다. 두 writer가 같은 빈 페이지를 할당하거나 제어 세대를 따로 게시하는 일을 막기 위해서다.
- `heap_store`·색인·`catalog_store`는 게시 상태에서 도달하는 페이지를 COW로 바꾼다. 제어 게시 전까지 이전 참조가 읽을 내용을 남기기 위해서다.
- 읽기는 파일 잠금 안에서 하고 세대 변화 시 캐시·빈 목록·카탈로그를 다시 적재한다. 다른 핸들이 게시한 뒤 낡은 참조와 할당 목록을 사용하지 않기 위해서다.
- 같은 세션의 거래 호출과 최종 핸들 종료는 호출자가 직렬화한다. 다른 스레드의 문장이 열린 거래에 섞이거나 해제한 핸들을 다시 읽는 일을 막기 위해서다.
- `UNKNOWN`이나 poison 뒤에는 핸들을 닫고 재개방한다. 게시 여부를 모르는 상태에서 양쪽 페이지를 재사용하거나 후속 변경을 쌓지 않기 위해서다.
- DB 코어는 큐 모듈에 의존하지 않고 큐는 세션 SQL과 거래 API를 사용한다. 큐 없는 SQL 사용과 업무 변경·큐 변경의 공동 commit을 함께 유지하기 위해서다.
- fork 자식은 부모 핸들을 사용하거나 close하지 않고 독립적으로 연다. 부모의 잠금과 작업 상태를 상속 핸들로 이어 쓰지 않기 위해서다.

### 두 세대를 보호하는 회수

<picture>
<source media="(max-width: 600px)" srcset="assets/page-reclamation-narrow.svg" width="248" height="721">
<img src="assets/page-reclamation.svg" alt="설계: 게시됐던 페이지는 해제를 포함한 commit과 다음 commit이 성공한 뒤에만 재사용한다" width="634">
</picture>

`page_file_release`는 게시됐던 USED 페이지를 PENDING으로 옮긴다. 그 해제를 포함한 commit이 성공하면 AGING이며, 다음 commit까지 성공해야 REUSABLE이다. [페이지 회수 재생](assets/page-reclamation.html)의 `g`는 설명용 세대다. 점선은 게시 뒤 바뀌는 참조이고 카드 안의 현재·이전 참조가 각 시점의 보유 상태를 나타낸다. 첫 성공 뒤에는 fallback 제어 슬롯이 옛 페이지를 아직 가리킬 수 있어 즉시 재사용하지 않는다.

제어 슬롯이 한 번도 가리킨 적 없는 FRESH 페이지의 release는 즉시 재사용 후보가 된다. rollback과 미반영 정리는 이 fresh 페이지만 돌려주고 작업 카탈로그를 버린다. 미게시 상태로 잠금을 놓으면 다음 잠금 때 게시 상태를 복원하도록 stale 표시를 남긴다. 불확정에서는 어느 쪽 물리 자원도 돌려주지 않는다.

빈 목록 자체도 영속 상태다. 제어 페이지와 `FREE_LIST` 체인에 reusable·aging을 저장하므로 재개방 뒤에도 보호 기간이 이어지며, 교체된 옛 빈 목록 체인도 aging을 거친다. reader 종료나 시간 경과만으로 이 전이를 일으키지 않는다. 페이지 재사용은 파일 축소와 다르고, 행 삭제 뒤 slot 번호의 재사용과도 다르다.

## 배포

| 프로세스 | 시작 주체 | 수명 |
|---|---|---|
| 애플리케이션 | 사용자 | 라이브러리를 로드하고 DB·거래·큐 핸들을 열어 쓰는 동안, 별도 DB 서버 없음 |

| 경로 | 내용 | 쓰는 구성 요소 |
|---|---|---|
| 앱이 정한 DB 경로 | 제어·카탈로그·행·큰 값·색인·회수 상태 | `page_file` |
| `<db>-lock` | 거래 writer 잠금과 게시 대기 표시, 영속 보조 파일 | `page_file` |
| `<db>.partial` | 최초 생성 중 최종 이름 게시 전 파일 | `page_file_create`·`page_file_publish` |
| `build/lib/libtotedb.dylib` 또는 `.so` | Python이 로드하는 공유 라이브러리의 개발 빌드 | `totedb` |

최초 생성은 partial 파일에서 초기 commit을 만든 뒤 최종 이름으로 게시하며 디렉터리를 동기화한다. 이름이 보이더라도 정리나 디렉터리 fsync가 실패한 `PUBLISHED_DIRTY`는 이름 내구성까지 성공했다는 뜻이 아니다. 잠금 파일을 임의로 지우거나 사용 중인 DB 파일을 교체하지 않는다. 메모리 캐시와 열린 핸들은 프로세스 수명에 속하며 DB 파일에 저장하는 객체가 아니다. 라이브러리 탐색은 [_native.py](../python/totedb/_native.py), 자식 자원 종료 순서는 [totedb.h](../include/totedb.h)를 따른다.

### 그림 원본과 재생성

이 문서의 그림은 같은 이름의 `docs/assets/*.diagramplay`가 수정 원본이며 SVG와 HTML은 생성물이다. `storage-layout.svg`는 파일의 읽기 참조를, `storage-index-locator.svg`는 색인의 간접 참조를 맡는다. `-narrow.svg`는 같은 원본을 목표 폭 390px로 다시 배치한 생성물이다. 본문의 `<picture>`는 600px 이하에서 좁은 배치를 고르고 이미지 폭은 각 출력 폭에 맞춰 글자를 늘리거나 줄이지 않는다. SVG를 지우면 본문이 깨지고 HTML을 지우면 재생 링크가 깨진다. 기존 큐 그림과 trace는 별도 원본이므로 이 그림 생성에 덮어쓰지 않는다.

기존 의존성을 갖춘 DiagramPlay 소스 `0f35febbae5afa541b92ffcf80f954d07b421382`와 Node.js `26.9.0`을 사용한다. `DIAGRAMPLAY_DIR`은 그 소스 작업본 경로다. 저장소 루트에서 각 원본의 이름을 넣어 strict 검사 뒤 생성한다. 구성도와 저장 참조는 정지 SVG를 쓰고, 게시·회수·잠금은 움직이는 SVG와 HTML을 쓴다.

```sh
node "$DIAGRAMPLAY_DIR/src/cli.js" check docs/assets/architecture.diagramplay --strict
node "$DIAGRAMPLAY_DIR/src/cli.js" render docs/assets/architecture.diagramplay --strict --static
node "$DIAGRAMPLAY_DIR/src/cli.js" render docs/assets/storage-layout.diagramplay --strict --static
node "$DIAGRAMPLAY_DIR/src/cli.js" render docs/assets/storage-index-locator.diagramplay --strict --static
node "$DIAGRAMPLAY_DIR/src/cli.js" render docs/assets/storage-publication.diagramplay docs/assets/page-reclamation.diagramplay docs/assets/read-publish-locks.diagramplay --strict
node "$DIAGRAMPLAY_DIR/src/cli.js" render docs/assets/storage-publication.diagramplay docs/assets/page-reclamation.diagramplay docs/assets/read-publish-locks.diagramplay --strict --html --out docs/assets
```

좁은 배치는 같은 버전의 공개 JavaScript API로 생성한다. 원본·내용·시간표는 바꾸지 않고 `layoutWidth`만 지정한다. 원본을 고쳐 출력 크기가 달라지면 본문의 이미지 폭과 `<source>`의 폭·높이도 새 `viewBox`에 맞춘다.

```sh
export DIAGRAMPLAY_DIR
node --input-type=module <<'JS'
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
const api = pathToFileURL(path.join(process.env.DIAGRAMPLAY_DIR, 'src/index.js'));
const { buildFigure, toSvg } = await import(api);
const names = ['architecture', 'storage-layout', 'storage-index-locator',
	'storage-publication', 'page-reclamation', 'read-publish-locks'];
for (const [index, name] of names.entries()) {
	const source = fs.readFileSync(`docs/assets/${name}.diagramplay`, 'utf8');
	const figure = await buildFigure(source, {
		baseDir: path.resolve('docs/assets'), strict: true, layoutWidth: 390,
	});
	const svg = await toSvg(figure, { isStatic: index < 3 });
	fs.writeFileSync(`docs/assets/${name}-narrow.svg`, svg);
}
JS
```

같은 입력으로 두 번 생성해 바이트를 대조한 뒤 라이트·다크 본문과 좁은 화면에서 읽는다. 재생 시간은 설명용이고 저장 지연 측정값이 아니다. 도구의 원본·문법은 [DiagramPlay](https://github.com/woonyong-choi/diagramplay/tree/0f35febbae5afa541b92ffcf80f954d07b421382), 일반 개발 명령은 [빌드와 시험](build-and-test.md)에 있다.
