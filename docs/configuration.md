# 설정

ToteDB 0.2.0의 flat C API와 Python API에는 설정 파일이 없다. 예를 들어 `Database("app.totedb", create=False)`는 기존 파일만 열며 read-only로 여는 옵션이 아니다. API 선언은 [totedb.h](../include/totedb.h), Python 구현은 [database.py](../python/totedb/database.py)와 [queue.py](../python/totedb/queue.py)에 있다.

## 우선순위

API 인자는 해당 호출이나 핸들에 적용하며 환경 변수가 덮어쓰지 않는다. Python의 공유 라이브러리 탐색만 아래 순서를 사용한다.

1. `TOTEDB_LIB`에 지정한 파일을 찾는다.
2. 설치 패키지 폴더의 `libtotedb.dylib` 또는 `libtotedb.so`를 찾는다.
3. 저장소 `build/lib/`의 같은 파일을 찾는다.

macOS는 `.dylib`, 그 밖은 `.so` 이름을 쓴다. 후보가 없으면 다음 경로로 넘어가지만, 존재하는 파일을 로딩하다 실패하면 그 예외가 전파된다. 모든 후보를 찾지 못하면 `OSError`다. 탐색 구현은 [_native.py](../python/totedb/_native.py)에 있다.

`TOTEDB_LIB`는 Python이 공유 라이브러리를 탐색할 때 처음 시도하는 파일의 경로 문자열이다. 기본값은 미지정이며 API 인자를 바꾸는 환경 변수가 아니다.

## 항목

아래 API 인자에 대응하는 환경 변수는 없다.

### 데이터베이스 열기

| 키 | 타입 | 기본값 | 뜻 |
|---|---|---|---|
| `busy_timeout_s` | Python `float`, 초 | `5.0` | Python의 BUSY 재시도 기간 |
| `create` | C `int`, Python `bool` | C 필수 인자, Python `True` | 파일이 없으면 생성 |
| `path` | C 문자열, Python 경로 | 없음, 필수 | DB 파일 경로 |

`Database` 생성자의 `tote_open` 호출은 한 번이며 `busy_timeout_s`로 재시도하지 않는다. 이 값은 `Database.execute`의 SQL BUSY, `begin`, 큐 열기와 트랜잭션 인자를 받지 않은 큐 호출의 BUSY 재시도에 쓰인다. 명시적 `Transaction.execute`와 `txn=`을 받은 큐 호출은 자동 재시도하지 않는다. 초기 대기는 0.5밀리초이고 두 배씩 늘려 최대 20밀리초로 둔다. 운영체제 스케줄링과 호출 시간 때문에 실제 경과가 timeout을 넘지 않는다는 보장은 아니다.

Flat C API는 writer 경합을 호출자에게 반환하며 Python과 같은 재시도 루프가 없다. Commit은 항상 `DURABLE`이고, 이 API에는 read-only, isolation, durability, 세션 풀·메모리 예산을 선택하는 인자가 없다. Core 헤더의 별도 옵션을 Python `Database` 옵션으로 전달할 수 없다.

### 큐 열기

옵션은 열린 큐 핸들의 값이며 파일에 저장한 큐 전역 설정이 아니다. 여러 worker와 재개방 호출의 옵션 일치는 호출자가 관리한다.

| 키 | 타입 | 기본값 | 뜻 |
|---|---|---|---|
| `clock` | C callback, Python callable | `NULL` 또는 `None` | 실시간 시계 대신 밀리초 절대 시각 제공 |
| `clock_ctx` | C `void *` | `NULL` | C clock에 전달하는 문맥 |
| `idem_retention_ms` | `uint64_t`, 밀리초 | `604800000`, 7일 | Purge 후 키 보존 기간 |
| `install` | C `int`, Python `bool` | C 필수 인자, Python `True` | 큐 테이블이 없으면 설치 |
| `max_attempts` | `uint32_t` | `0`, 무제한 | claim 시도 횟수 상한 |
| `max_payload_bytes` | C `size_t`, Python `int` | `1048576`, 1MiB | payload 한 건의 상한 |
| `name` | 문자열 | 없음, 필수 | `[A-Za-z0-9_]{1,40}` |

C의 `options=NULL` 또는 0으로 초기화한 옵션은 기본값이다. C의 `idem_retention_ms=0`은 기본 7일이고 `UINT64_MAX`는 purge 뒤 키를 보존하지 않는다. Python의 `idem_retention_ms=0`은 보존하지 않는다는 뜻이며 내부에서 이 sentinel로 변환한다. 일반 보존 기간의 최대는 `3162240000000`밀리초다.

C의 `max_payload_bytes=0`은 기본 1MiB다. Python은 0 이하를 거절한다. 설정 가능한 최대는 `1073741824`바이트지만 내부 행·결과 한도가 더 먼저 적용될 수 있다. 이름, 키와 reason의 NUL 경계는 [데이터](data.md#sql-값과-바인딩)에 있다. 음수나 범위를 넘는 Python 숫자는 unsigned ctypes 변환 전에 모든 인자에서 일관되게 검사하지 않으므로 자동 보정되는 입력으로 취급하지 않는다.

### 큐 연산 인자

| 키 | 타입 | 기본값 | 뜻 |
|---|---|---|---|
| `after_id` | `uint64_t` | `0` | 목록에서 이 ID보다 큰 행만 조회, 최대 `INT64_MAX` |
| `idem_key` | UTF-8 문자열 | Python `None`, C `NULL` | 미지정 시 중복 제거 없음, 지정 시 1~128바이트 |
| `include_failed` | bool | `False` | purge에 FAILED 포함 |
| `item_id` | `uint64_t` | Python `None`, C `0` | 다음 작업 claim, 양수는 특정 작업 |
| `lease_ms` | `uint64_t`, 밀리초 | 없음, 필수 | `get`·`extend`의 기간, 1~31622400000 |
| `limit` | C `size_t`, Python `int` | `list_items` 100, 정리 연산 1024 | C 허용 범위 1~1024 |
| `page_size` | Python `int` | `100` | `iter_items`의 한 쪽 크기 |
| `reason` | UTF-8 문자열 | Python `''` | fail·nack 이유, 최대 200바이트, 빈 값 허용 |
| `retry_delay_ms` | `uint64_t`, 밀리초 | Python `0`, C 필수 인자 | nack 후 대기, 0~31622400000 |
| `with_payload` | bool | `False` | 목록에 payload 복사 |

Python `list_items`는 큰 limit을 1024로 줄인다. `recover`, `release_leases`, `purge`는 줄이지 않고 C의 범위 검사를 받는다. `iter_items`는 `list_items`를 반복한다. 실시간 clock의 후퇴는 lease 대기를 늘릴 수 있다. `extend`는 기존 만료에 더하지 않고 `현재 시각 + lease_ms`로 바꾼다. 시각 덧셈 overflow나 `INT64_MAX` 초과는 LIMIT이다.

### SQL 입력과 결과 한도

| 항목 | 기본값 또는 고정값 | 공개 설정 |
|---|---|---|
| SQL 입력 실효 한도 | 1MiB, UTF-8 바이트 | Flat C·Python 변경 옵션 없음 |
| C 인코딩 결과 전체 | 64MiB, `67108864`바이트 | `tote_set_max_result_bytes(size_t)` |
| 내부 결과 행 | 기본 64MiB, 셀 배열과 값 바이트 합 | Flat C·Python 변경 옵션 없음 |
| 내부 builder 결과 | 기본 256MiB, metadata와 행 합 | Flat C·Python 변경 옵션 없음 |
| 결과 열 수·이름 | 최대 1024개·이름 256바이트 | 없음 |
| 명령 tag·오류 메시지 | 최대 32바이트·1024바이트 | 없음 |

SQL 입력은 [database.c](../src/sql/database.c)의 bounded 기본값을 적용한다. Lexer 자체의 16MiB 한도가 flat API의 실효 기본값은 아니다. 세션 풀의 무제한 설정도 SQL 입력이나 결과를 무제한으로 만들지 않는다. 내부 결과 한도는 [result.h](../include/sql/result.h)에 있다.

C 결과 cap은 DB별이 아닌 프로세스 전역 atomic 값이다. 결과를 만들기 전에 설정하고 `tote_max_result_bytes()`로 읽는다. `SIZE_MAX/2`보다 큰 값은 그 값으로 줄이며 `0`은 무제한이 아니라 0바이트 cap이다. Python 공개 생성자에는 대응 옵션이 없다. Cap은 전체 프로세스 메모리 상한이 아니며, 실패가 commit 뒤에도 발생할 수 있다는 점은 [오류](errors.md#실패-뒤-상태)에 있다.

## 전체 예

새 DB 경로에서 Python 기본값을 모두 명시하는 예다. Lease는 기본값이 없으므로 이 예에는 claim 호출을 넣지 않는다.

```python
from totedb import Database, Queue

with Database("defaults.totedb", create=True, busy_timeout_s=5.0) as db:
	queue = Queue(db, "jobs", install=True, max_attempts=0,
		max_payload_bytes=1048576, idem_retention_ms=604800000, clock=None)
	queue.close()
```

같은 기본 큐 옵션을 C에서는 `tote_queue_open(db, "jobs", 1, NULL, &queue)`로 지정한다. 실행 파일과 헤더의 버전은 `tote_version()`과 `TOTE_VERSION`으로 비교할 수 있지만, Python 로더가 자동으로 버전 일치를 강제하지는 않는다.
