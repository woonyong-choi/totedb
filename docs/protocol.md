# 프로토콜

ToteDB 0.2.0의 `tote_exec`는 같은 프로세스의 C 호출자에게 길이가 있는 바이너리 결과 버퍼를 반환한다. PostgreSQL wire, 소켓 서버나 JSON-RPC 프로토콜이 아니다. 예를 들어 `SELECT 42 AS answer`의 결과는 kind `0`으로 시작하는 28바이트다.

## 전송 방식

| 항목 | 값 |
|---|---|
| 방식 | 공유 라이브러리 함수 호출 |
| 형식 | `uint8_t *out`, `size_t out_size` |
| 메시지 구분 | 한 성공 호출의 버퍼 하나, 바깥 길이는 `out_size` |
| 인코딩 | 고정 폭 수와 길이 접두 바이트열, little-endian |
| 문자열 | UTF-8, 길이는 문자 수가 아닌 바이트 수, 종료 NUL 없음 |

반환 버퍼는 디스크의 [데이터 형식](data.md)과 별개다. 명령 tag와 오류 메시지도 길이 접두 문자열이며, BLOB은 UTF-8이 아닌 임의 바이트다.

## 연결 순서

1. 호출자가 `tote_open`으로 DB 핸들을 연다.
2. 호출자가 출력 포인터를 NULL, 길이를 0으로 초기화하고 `tote_exec`를 부른다.
3. 호출자가 C status를 검사한다. `TOTE_OK`일 때만 버퍼 kind를 읽는다.
4. 호출자가 결과를 소비하고 `tote_free(out)`으로 한 번 해제한다.
5. 호출자가 큐와 트랜잭션 핸들을 끝내고 `tote_close`로 DB를 닫는다.

성공한 반환 버퍼는 호출자 소유이며 다음 실행, rollback, DB close 뒤에도 해제 전까지 유효하다. CAPI는 내부 `result_t`를 인코딩 직후 해제한다. DB mutex는 실행·인코딩·내부 결과 해제 구간을 보호하며 호출자가 반환 버퍼를 읽고 해제하는 동안 보유하는 잠금이 아니다. Python은 바이트를 복사해 decode하고 `finally`에서 native 버퍼를 해제한다.

## 요청

### `tote_exec`

SQL 문장 하나를 실행하고 행, 명령 또는 SQL 오류 결과를 반환한다.

| 항목 | 값 |
|---|---|
| 방향 | 호출자에서 `libtotedb`, 결과는 호출자에게 반환 |
| 부작용 | SQL에 따른 DB 변경, 트랜잭션 밖 변경 문장은 자동 commit |
| 순서 | `db`와 선택적 `txn`이 유효해야 하며 `txn`은 같은 DB 소속 |

#### 매개변수

| 이름 | 타입 | 필수 | 뜻 |
|---|---|---|---|
| `db` | `tote_db_t *` | 예 | 열린 DB |
| `txn` | `tote_txn_t *` | 아니오 | NULL이면 기본 세션, 아니면 명시적 트랜잭션 |
| `sql` | `const char *` | 예 | NUL 종료 SQL, 내부 `strlen` 길이 |
| `params` | `const tote_param_t *` | 인자가 있으면 예 | type과 값 필드를 담은 배열 |
| `param_count` | `size_t` | 예 | 배열 길이, 인자가 없으면 0 |
| `out` | `uint8_t **` | 예 | 성공한 소유 버퍼 주소 |
| `out_size` | `size_t *` | 예 | 성공한 전체 버퍼 바이트 수 |

입력 parameter의 type과 길이·바인딩 규칙은 [데이터](data.md#sql-값과-바인딩)에 있다. 입력 배열과 참조 바이트는 동기 호출 종료까지 유효하게 유지한다. C 구조체 자체의 메모리 배치를 바이트 프로토콜로 직렬화해 보내는 방식은 아니다.

#### 결과

`u8`, `u32`, `u64`는 부호 없는 1·4·8바이트 수다. `i32`, `i64`는 부호 있는 2의 보수 수이며 `f64`는 IEEE 754 double 비트열이다. 구조체 정렬 padding은 넣지 않는다. 첫 `u8 kind` 뒤에 해당 형식 하나만 온다.

| kind | 뒤따르는 필드 순서 | 종료 |
|---|---|---|
| `0`, rows | `u32 column_count`, 열 metadata 반복, 행 반복 | `u8 0` |
| `1`, command | `u32 tag_len`, tag 바이트, `u64 affected_rows` | 마지막 수 뒤 |
| `2`, error | `u32 error_class`, `i32 error_code`, `u32 message_len`, message 바이트 | 메시지 뒤 |

열 metadata는 `u32 name_len`, 이름 바이트, `u8 type`, `u8 nullable` 순서다. Nullable은 `0` 또는 `1`이다. 열 type `0`은 식 타입을 정하지 못했다는 뜻이며 `SELECT NULL`이 그 예다. 각 행은 `u8 1`로 시작하고 metadata의 열 수만큼 cell을 담는다. 행 수 헤더는 없다. Metadata 바로 뒤 `u8 0`이면 정상 0행이다.

| Cell type | 뒤따르는 payload |
|---|---|
| `0`, NULL | 없음 |
| `1`, INTEGER | `i64` |
| `2`, REAL | `f64` |
| `3`, TEXT | `u32 byte_len`, UTF-8 바이트 |
| `4`, BLOB | `u32 byte_len`, 임의 바이트 |

NULL에는 길이 필드도 없다. 빈 TEXT와 빈 BLOB은 각각 type `3`과 `4` 뒤에 길이 `0`을 넣는다. Cell마다 type이 있으므로 열 metadata만 보고 NULL payload를 읽지 않는다.

#### 오류

| 조건 | 반환 |
|---|---|
| SQL 문장 오류 | `TOTE_OK`, kind `2` |
| 잘못된 C 인자 | 비OK, 결과 판독 금지 |
| 인코딩 cap 또는 u32 길이 초과 | `TOTE_LIMIT`, 부분 버퍼 없음 |
| 인코딩 할당 실패 | `TOTE_NOMEM`, 부분 버퍼 없음 |
| 행 공급 중 실패 | 이미 모은 행을 버리고 kind `2`로 교체, 오류 자체 인코딩 실패 시 비OK |

초기 C 인자 검증은 출력 초기화보다 먼저 끝날 수 있으므로 실패 시 모든 out 인자가 항상 초기화된다고 가정하지 않는다. 오류 번호의 뜻과 실패 뒤 DB 상태는 [오류](errors.md)에 있다. 결과 cap과 내부 결과 생성 한도는 [설정](configuration.md#sql-입력과-결과-한도)에 있다. 부분 결과 없음은 commit 미반영 보장이 아니다.

#### 사용 예

요청은 `sql="SELECT 42 AS answer"`, `txn=NULL`, `params=NULL`, `param_count=0`이다. 성공 status는 `0`이고 버퍼는 아래 28바이트다.

```text
00 01 00 00 00 06 00 00 00 61 6e 73 77 65 72 01
01 01 01 2a 00 00 00 00 00 00 00 00
```

순서대로 rows kind, 열 수 1, 이름 길이 6과 `answer`, INTEGER type 1, nullable 1, 행 시작 1, cell INTEGER type 1, 정수 42, 행 끝 0이다. 마지막 0은 정수의 일부가 아니라 별도의 행 종료 바이트다.

## 호환성

버퍼에는 버전 헤더나 format negotiation이 없다. 헤더의 `TOTE_VERSION`과 `tote_version()`은 현재 `0.2.0`이며 `1.0` 전에는 API와 파일 형식이 바뀔 수 있다. 버전 문자열이 미래 decoder 호환이나 자동 migration을 보장하지 않는다.

바인딩은 매 필드에서 남은 길이를 확인한 뒤 수와 바이트를 읽고, 길이·개수 계산의 overflow를 거절해야 한다. 알 수 없는 kind·cell type, 잘린 필드, 잘못된 행 marker와 불필요한 뒤쪽 바이트도 정상 응답으로 해석하지 않는다. 이 조건은 바인딩 작성 시 필요한 검사이며 현재 Python decoder가 모두 강제한다는 뜻은 아니다. Python decoder는 신뢰하는 native 버퍼를 소비하며 임의 외부 바이트를 검증하는 네트워크 parser가 아니다.

형식의 구현은 [totedb.c](../src/capi/totedb.c)의 `encode_result`, 선언은 [totedb.h](../include/totedb.h), Python 해석은 [database.py](../python/totedb/database.py)의 `decode_result`에 있다. 별도 알림이나 연결 handshake는 없다.
