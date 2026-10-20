# 데이터

ToteDB 0.2.0의 SQL 값, 파일 형식과 선택형 ACK 큐 테이블을 다룬다. 예를 들어 SQL의 `NULL`, 빈 문자열 `''`, 빈 BLOB `X''`는 서로 다른 값이며, 조회 결과가 0행인 경우와도 다르다. C 호출의 반환 바이트는 [프로토콜](protocol.md)에 있다.

## 저장 방식

| 항목 | 값 |
|---|---|
| 엔진 | ToteDB 0.2.0, 자체 페이지 파일 |
| 경로 | `tote_open` 또는 `Database`에 전달한 경로 |
| 쓰는 구성 요소 | SQL 데이터베이스, 같은 DB의 선택형 ACK 큐 |
| 동시성 | 파일 전체에 writer 하나, 문장 단위 읽기와 게시 잠금 |
| 형식 버전 위치 | 페이지 envelope의 버전, 제어 페이지 본문의 파일 형식 버전, 각 본문의 magic과 버전 |
| 앱 스키마 버전 | 제품이 지정한 위치 없음 |

게시·읽기·페이지 회수의 순서는 [아키텍처](architecture.md)에 있다. 아래 형식은 현재 구현의 저장 표현이며 외부 프로그램이 DB 파일을 직접 수정하는 인터페이스가 아니다.

### SQL 값과 바인딩

| 번호 | 값 | Python | 범위와 표현 |
|---|---|---|---|
| `0` | `NULL` | `None` | 값 없음 |
| `1` | `INTEGER` | `int` | 부호 있는 64비트, `-2^63`부터 `2^63-1` |
| `2` | `REAL` | `float` | 유한한 double, NaN과 무한대 거절, `-0.0` 보존 |
| `3` | `TEXT` | `str` | 유효한 UTF-8 바이트열 |
| `4` | `BLOB` | `bytes` | 임의 바이트열 |

TEXT와 BLOB 바인딩은 길이를 따로 전달하므로 내장 NUL을 보존한다. 경로, SQL 본문, 큐 이름, idempotency key와 reason은 flat C API의 NUL 종료 문자열이다. 이 입력에 내장 NUL을 넣으면 뒤쪽이 보존되지 않으며, Python이 항상 먼저 거절하는 것도 아니다.

Python의 `bool`은 INTEGER `1` 또는 `0`으로 바인딩한다. 64비트 범위 밖의 Python 정수는 실행 전에 거절한다. TEXT와 BLOB 값 표현의 길이 필드는 최대 `UINT32_MAX`바이트지만, 실제 쓰기와 읽기는 [설정의 한도](configuration.md#항목)도 받는다.

SQL 열 타입은 `INTEGER`, `REAL`, `TEXT`, `BLOB`, `VARCHAR(n)`이다. `VARCHAR(n)`은 TEXT의 별칭이며 문법상 `1 <= n <= UINT32_MAX`만 검사하고 저장 길이는 제한하지 않는다. 저장 시 타입 변환은 정확히 표현되는 값만 허용한다. 예를 들어 소수부가 있는 REAL을 INTEGER로 잘라 넣지 않는다. SQL `CAST`는 별도 규칙으로 숫자 문자열 양끝의 ASCII 공백과 INTEGER에서 REAL로의 반올림을 허용한다. 세부 변환은 [value.h](../include/storage/value.h)와 [sql_eval.h](../include/sql/sql_eval.h)에 있다.

| 바인딩 항목 | 형식 |
|---|---|
| `?` | 출현 순서대로 1부터 번호 부여 |
| `?N` | 고유 번호 집합이 1부터 최대 번호까지 연속, 반복과 역순 허용 |
| 개수 | 고유 번호 수와 매개변수 수 일치, 최대 65535개 |
| 거절 | `?`와 `?N` 혼용, 번호 누락, `?0`, `?01`, `?a` |
| 문장 수 | 한 호출에 한 문장, 마지막 `;` 선택 |

예를 들어 `SELECT ?2, ?1, ?2`에 `(10, 20)`을 바인딩하면 `(20, 10, 20)` 한 행이다. 이름 붙인 placeholder는 지원하지 않는다. C 입력 배열과 바이트는 동기 호출이 끝날 때까지 호출자가 유지한다.

### 파일 형식

다중 바이트 정수는 little-endian이다. 파일 버전과 envelope 버전, 카탈로그 버전은 서로 다른 필드다.

| 부분 | 식별값·버전·크기 |
|---|---|
| 물리 페이지 | 8192바이트, 앞 32바이트 envelope와 본문 8160바이트 |
| Envelope | `LRPG`, 버전 `2` |
| 제어 페이지 | 슬롯 `0`, `1`, 본문 magic `TOTEDB\0\0`, 파일 형식 `3`, byte-order 표시 `0x01020304` |
| 카탈로그 | `LRCT`, 버전 `1` |
| 행 디렉터리 | `LHD3` 버전 `3`, `LHD2` 버전 `2` |
| Record page | `LRP2` |
| Record codec | 형식 바이트 `0x02` |
| Heap 행 | 행 버전 `1`, REAL은 8바이트 |
| Overflow | `LRO2` |
| 정수 색인 | `LPK1` 버전 `1` |
| TEXT 색인 | `LTX1` 버전 `1` |
| 빈 페이지 사슬 | `FREE_LIST` 종류 `6` |

- 물리 페이지의 envelope와 본문 크기는 [page_file.h](../include/storage/page_file.h)에 있다.
- Envelope는 식별값과 버전 뒤에 종류, page ID, table ID, 본문 길이, 0인 예약 8바이트, CRC32C를 담는다. 구현은 [page_file.c](../src/storage/page_file.c)에 있다.
- 제어 페이지는 파일 형식 뒤에 페이지 크기와 byte-order 표시를 담는다. 구현은 [page_file.c](../src/storage/page_file.c)에 있다.
- 제어 참조는 generation, page_count, catalog head·size·generation, reusable·aging 목록과 사슬 참조, manifest를 담는다. 정의는 [page_file.h](../include/storage/page_file.h)에 있다.
- 카탈로그는 길이·generation·다음 table ID·테이블·열·기본값·색인, FNV-1a 체크섬을 담는다. 구현은 [catalog.c](../src/storage/catalog.c)에 있다.
- 행 디렉터리의 잎은 record page ID와 빈 공간 힌트를 담는다. 큰 디렉터리는 `LHD2` 루트 아래 `LHD3` 잎을 둔다. 정의는 [heap_store.h](../include/storage/heap_store.h)에 있다.
- Record page는 slot과 행 바이트를 담으며 삭제 slot 번호를 재사용하지 않는다. 정의는 [record_page.h](../include/storage/record_page.h)에 있다.
- Record codec은 형식 바이트 뒤에 LEB128 필드 수·코드, 최소 폭 little-endian 정수, TEXT·BLOB 바이트를 담는다. 구현은 [record.c](../src/storage/record.c)에 있다.
- Heap 행은 열별 저장 종류와 값을 담고 큰 값은 head·size로 참조한다. 구현은 [heap_store.c](../src/storage/heap_store.c)에 있다.
- Overflow는 체인 순번·다음 page·chunk 길이·전체 길이·head·CRC32C를 담는다. 구현은 [overflow.c](../src/storage/overflow.c)에 있다.
- 정수 색인은 leaf의 key와 locator, internal의 하한 key와 child를 담는다. 정의는 [pk_tree.h](../include/storage/pk_tree.h)에 있다.
- TEXT 색인은 가변 길이 키와 locator를 담는 COW B+Tree다. 정의는 [text_tree.h](../include/storage/text_tree.h)에 있다.
- 빈 페이지 사슬은 다음 page·번호 개수·page ID 배열을 담는다. 구현은 [page_file.c](../src/storage/page_file.c)에 있다.

Record codec의 코드 `0`은 NULL, `1`과 `2`는 정수 0과 1, `3..10`은 1~8바이트 정수다. 코드 `11+2n`은 n바이트 BLOB, `12+2n`은 n바이트 TEXT다. Heap은 이 codec으로 저장 종류와 값을 함께 표현하므로 SQL 열 하나가 codec 필드 하나라는 뜻은 아니다. 전체 offset과 트리 변경 알고리즘은 위 구현이 맡는다.

## 관계

제어 페이지는 카탈로그 overflow를 가리키고, 카탈로그는 각 테이블의 heap과 색인 루트를 담는다. Heap의 디렉터리가 record page를, record page의 slot이 행을 식별한다. 색인 locator는 물리 record page 번호가 아니라 `directory_index: u32`와 `slot: u16`이다. 색인 루트의 stamp는 대응 heap root의 page ID이며 commit generation이 아니다.

큐와 업무 행을 같은 트랜잭션에 넣는 호출 예는 [주문과 작업의 공동 commit](design/transaction-queue.md#주문과-작업의-공동-commit)에 있다. 업무 테이블의 schema와 큐 payload가 가리키는 업무 ID는 앱이 정한다. 큐의 `job`도 외래 키가 아니다. Purge 후에는 작업 행이 없어도 보존 키 행이 남는다.

## 테이블

### `tote_queue_<name>`

큐 설치가 만드는 작업 본체다. 모든 열은 `NOT NULL`이고 DDL에 `DEFAULT`가 없다. `put`이 넣는 초기 값은 열 기본값과 다르다.

| 열 | 타입 | 뜻 |
|---|---|---|
| `id` | `INTEGER` | `PRIMARY KEY AUTOINCREMENT` 작업 ID |
| `state` | `INTEGER` | `0 READY`, `1 LEASED`, `2 ACKED`, `3 FAILED` |
| `ready_at` | `INTEGER` | 다음 claim 가능 시각, 밀리초 |
| `lease_until` | `INTEGER` | lease 만료 절대 시각, 밀리초 |
| `fence` | `INTEGER` | 재claim마다 증가하는 claim 식별값 |
| `attempts` | `INTEGER` | claim 횟수 |
| `idem` | `TEXT` | 중복 제거 키, 미사용은 빈 TEXT |
| `payload` | `BLOB` | 작업 바이트, 빈 BLOB 허용 |
| `note` | `TEXT` | 마지막 이유, 없으면 빈 TEXT |
| `created_at` | `INTEGER` | 생성 절대 시각, 밀리초 |

| 키와 색인 | 열 |
|---|---|
| 기본 키 | `id` |
| 비고유 색인 `tote_queue_<name>_state` | `state` |
| 비고유 색인 `tote_queue_<name>_idem` | `idem` |
| 외래 키 | 없음 |

`put`은 `state=0`, `lease_until=0`, `fence=0`, `attempts=0`, `note=''`를 명시하고 `ready_at`과 `created_at`에 현재 시각을 넣는다. 상태 전이와 lease 만료의 의미는 [트랜잭션 큐 설계](design/transaction-queue.md)에 있다.

### `tote_queue_<name>_keys`

키가 있는 작업을 purge할 때 payload 요약과 만료 시각을 보관한다. 작업 본체가 살아 있는 동안 모든 키를 복제하는 테이블은 아니다.

모든 열은 `NOT NULL`이고 DDL에 `DEFAULT`가 없다.

| 열 | 타입 | 뜻 |
|---|---|---|
| `seq` | `INTEGER` | `PRIMARY KEY AUTOINCREMENT` 보관 순번 |
| `idem` | `TEXT` | 중복 제거 키 |
| `job` | `INTEGER` | 지운 작업 ID, 외래 키 아님 |
| `digest` | `BLOB` | 20바이트 payload 요약 |
| `expires_at` | `INTEGER` | 보존 만료 절대 시각, 밀리초 |

| 키와 색인 | 열 |
|---|---|
| 기본 키 | `seq` |
| 고유 색인 `tote_queue_<name>_keys_idem` | `idem` |
| 외래 키 | 없음 |

`digest`는 little-endian 길이 `u64`, CRC32C `u32`, FNV-1a `u64` 순서다. 작업 본체가 있으면 현재 payload 바이트를 비교하고, purge 뒤에는 이 요약을 비교한다. 따라서 보존 키가 수학적으로 충돌 없는 중복 제거를 보장하지 않는다. 실제 DDL과 보관 처리는 [ack_queue.c](../src/queue/ack_queue.c)에 있다.

### 업무 테이블 예시

아래 `orders`는 앱이 정의하는 예시이며 제품이 설치하는 테이블이 아니다. 큐 payload와의 외래 키도 만들지 않는다.

```sql
CREATE TABLE orders (id INTEGER PRIMARY KEY, state TEXT NOT NULL);
```

## 지켜야 할 규칙

| 조건 | 지키는 방법 |
|---|---|
| TEXT는 유효한 UTF-8이고 REAL은 유한값이다. | 값 복제와 SQL 바인딩에서 검사한다. |
| 페이지 참조는 범위·종류·table ID가 맞아야 한다. | Envelope와 본문 길이·체크섬을 읽을 때 검사한다. |
| 디렉터리에 중복되거나 범위를 벗어난 page ID를 넣지 않는다. | Heap을 열 때 디렉터리를 검사한다. |
| 색인은 대응 heap을 가리킨다. | 기본 키 stamp 불일치는 scan으로 처리하지만 malformed root는 오류다. 보조 색인 stamp 불일치는 `CORRUPT`다. |
| 큐 변경은 현재 상태와 fence에 맞아야 한다. | 큐 API가 검사한다. 큐 테이블 직접 SQL 수정은 이 검사를 대신하지 않는다. |

## 보관 기간

| 데이터 | 보관 기간 | 삭제 시점 |
|---|---|---|
| 업무 행 | 앱별 보관 정책 | 앱의 `DELETE` 또는 `DROP TABLE` |
| READY·LEASED 작업 | 자동 삭제 기간 없음 | 큐 상태 전이 뒤 purge 대상이 될 때 |
| ACKED 작업 | purge 전까지 | `purge` 호출 |
| FAILED 작업 | 포함하는 purge 전까지 | `purge(include_failed=True)` 호출 |
| 보존 키 | purge 시점부터 핸들의 보존 기간 | 만료 뒤 해당 키 조회 또는 purge의 제한된 만료 정리 |

ACK는 물리 삭제가 아니다. 보존 설정은 [설정](configuration.md)에 있다. 만료 정리는 백그라운드 타이머가 아니므로 만료된 키 행이 즉시 사라지지는 않는다. 삭제한 행 바이트와 안전하게 회수한 페이지는 재사용할 수 있지만, 삭제 slot 번호 재사용이나 파일 축소를 뜻하지 않는다.

## 스키마 변경

앱의 테이블 변경과 버전 관리는 앱이 맡는다. 공개 API에는 시작 시 앱 migration을 자동 실행하는 기능이 없다. ToteDB 0.2.0은 알 수 없는 파일 형식을 거절하며 SQLite 파일을 변환하지 않는다. 카탈로그와 레코드 버전도 공개 패키지 버전과 별개다. `1.0` 전에는 API와 파일 형식이 바뀔 수 있으며 자동 migration을 보장하지 않는다.

## 파일

| 경로 | 내용 | 형식 |
|---|---|---|
| 호출자가 지정한 DB 경로 | 제어 페이지·카탈로그·행·색인·빈 목록 | 위 페이지 형식 |
| `<db>-lock` | writer 잠금과 게시 대기 표시 | 내부 잠금 파일 |
| `<db>.partial` | 최초 생성 중 아직 이름을 게시하지 않은 DB | DB 페이지 형식 |

잠금 파일은 업무 데이터를 담지 않지만 동시 접근의 잠금 대상이므로 열린 DB 옆에서 임의로 지우거나 바꾸지 않는다. `.partial`은 정상 게시 시 정리한다. 남은 파일을 발견했다는 이유만으로 다른 프로세스의 생성 중 파일을 삭제하지 않는다.
