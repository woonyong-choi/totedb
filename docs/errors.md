# 오류

ToteDB 0.2.0에는 C 호출 상태와 SQL 오류 결과라는 두 오류 층이 있다. 예를 들어 `SELECT 1 / 0`은 `tote_exec`가 `TOTE_OK`로 오류 버퍼를 반환하고 Python에서는 `SqlError`가 된다. 반면 C 결과 인코딩 상한 초과는 `TOTE_LIMIT`이며 Python의 `LimitError`다. 버퍼 판독 조건은 [프로토콜](protocol.md)에 있다.

## 오류 코드

### C 호출 상태

상태 번호는 [totedb.h](../include/totedb.h), Python 매핑은 [errors.py](../python/totedb/errors.py)에 있다. C status에는 메시지 payload가 없다. Python의 `raise_for_status`는 `tote_status_name`의 이름으로 `이름 (status 번호)`를 만들지만 일부 호출은 별도 메시지를 쓴다. 표의 메시지 `없음`은 상태마다 하나의 고정된 메시지가 없다는 뜻이다.

| 코드 | 이름 | Python | 메시지 | 원인 | 대응 |
|---|---|---|---|---|---|
| `0` | `TOTE_OK` | 없음 | 없음 | 호출이 성공했다. | SQL은 결과 kind도 검사한다. |
| `1` | `TOTE_INVALID` | `InvalidArgumentError` | 없음 | 인자·시간·이름이 잘못됐거나 다른 DB의 트랜잭션이다. | 인자와 핸들 소속을 고친다. |
| `2` | `TOTE_NOMEM` | `StorageError` | 없음 | 할당이 실패했다. | 실행 단계와 반영 상태를 확인한다. |
| `3` | `TOTE_LIMIT` | `LimitError` | 없음 | payload나 반환 버퍼의 한도를 넘었다. | 크기를 줄이되 쓰기를 맹목 재실행하지 않는다. |
| `4` | `TOTE_BUSY` | `BusyError` | 없음 | writer가 있거나 close에 자식 자원이 남았다. | 경합이면 재시도하고 close면 자원을 먼저 끝낸다. |
| `5` | `TOTE_NOT_INSTALLED` | `NotInstalledError` | 없음 | 큐 테이블이 없다. | 올바른 DB·이름과 설치 여부를 확인한다. |
| `6` | `TOTE_NOT_FOUND` | `NotFoundError` | 없음 | 지정한 작업 ID가 없다. | ID와 업무 상태를 확인한다. |
| `7` | `TOTE_STALE` | `StaleError` | 없음 | fence 또는 상태가 현재 작업과 맞지 않는다. | 옛 claim으로 변경하지 않는다. |
| `8` | `TOTE_CONFLICT` | `ConflictError` | 없음 | 같은 키에 다른 payload다. | 기존 작업과 요청 내용을 대조한다. |
| `9` | `TOTE_UNSUPPORTED` | `UnsupportedError` | 없음 | 하위 트랜잭션 계층이 요청을 지원하지 않는다. | 지원 API와 트랜잭션 조건을 확인한다. |
| `10` | `TOTE_IO` | `StorageError` | 없음 | 저장소 연산이 실패했다. | 트랜잭션과 핸들 상태를 확인한다. |
| `11` | `TOTE_NOT_APPLIED` | `NotAppliedError` | 없음 | commit이 확정적으로 미반영이다. | poison 가능성도 확인하고 새 트랜잭션 여부를 정한다. |
| `12` | `TOTE_UNCERTAIN` | `UncertainError` | 없음 | commit 반영 여부를 알 수 없다. | 닫고 재개방하여 업무 상태를 확인한다. |
| `13` | `TOTE_POISONED` | `PoisonedError` | 없음 | 이 핸들로 작업을 계속할 수 없다. | 자원을 정리하고 닫아 재개방한다. |
| `14` | `TOTE_TXN_FAILED` | `TransactionFailedError` | 없음 | 호출자 트랜잭션이 실패 상태다. | rollback한 뒤 새 트랜잭션을 시작한다. |
| `15` | `TOTE_INTERNAL` | `InternalError` | 없음 | 내부 응답이 예상과 다르다. | 입력과 재현 조건을 보존한다. |
| `16` | `TOTE_CANCELLED` | `CancelledError` | 없음 | 큐 연산이 취소됐다. | 호출자 트랜잭션이라면 전체 rollback한다. |
| `50` | `TOTE_EMPTY` | `Queue.get`의 `None` | 없음 | 다음 claim 대상이 없다. | 정상 부재로 처리한다. |
| `101` | `TOTE_DB_INVALID` | `DatabaseOpenError` | 없음 | 열기 인자가 잘못됐다. | 경로와 인자를 확인한다. |
| `102` | `TOTE_DB_IO` | `DatabaseOpenError` | 없음 | 파일 열기·읽기·생성이 실패했다. | 파일 권한과 저장소 상태를 확인한다. |
| `103` | `TOTE_DB_CORRUPT` | `DatabaseOpenError` | 없음 | 열기 중 손상이나 잘림을 발견했다. | 파일을 보존하고 원인을 조사한다. |
| `104` | `TOTE_DB_LOCKED` | `DatabaseOpenError` | 없음 | 열기 잠금을 얻지 못했다. | 다른 핸들의 사용을 확인한다. |
| `105` | `TOTE_DB_UNSUPPORTED` | `DatabaseOpenError` | 없음 | 다른 파일 형식 또는 미지원 형식이다. | 맞는 파일과 버전을 사용한다. |
| `106` | `TOTE_DB_NOMEM` | `DatabaseOpenError` | 없음 | 열기나 세션 생성 할당이 실패했다. | 메모리와 재현 조건을 확인한다. |
| `107` | `TOTE_DB_NOT_FOUND` | `DatabaseOpenError` | 없음 | `create=0`인데 파일이 없다. | 경로 또는 생성 의도를 확인한다. |
| `108` | `TOTE_DB_FORKED` | `ForkedHandleError` | 없음 | 자식 프로세스가 부모 핸들을 사용했다. | 자식에서 독립적으로 연다. |

알 수 없는 비0 상태는 Python에서 `InternalError`다. `TOTE_EMPTY`는 `Queue.get`만 `None`으로 처리한다. 일반 `raise_for_status(50, ...)`는 `InternalError`이며 프로세스 종료 코드가 아니다. 미지원 SQL 문법은 일반적으로 encoded SQL 오류이지 C status `9`가 아니다.

### SQL 오류 분류

`kind=2` 결과의 `error_class: u32`, `error_code: i32`, message는 Python의 `SqlError.error_class`, `.error_code`, 예외 메시지로 옮긴다. `SqlError.status`의 기본값 `-1`은 C 호출 상태가 아니다.

| 코드 | 이름 |
|---|---|
| `0` | `NONE`, 오류 결과의 정상 분류 아님 |
| `1` | `SYNTAX` |
| `2` | `SEMANTIC` |
| `3` | `CONSTRAINT` |
| `4` | `LIMIT` |
| `5` | `NOMEM` |
| `6` | `IO` |
| `7` | `BUSY` |
| `8` | `CANCELLED` |
| `9` | `INTERNAL` |

상세 코드는 전역 고유 번호가 아니다. 같은 분류 안에서도 lexer, parser, 값 변환과 저장 계층의 번호가 겹치므로 아래 표는 발생 문맥을 포함한다. 표는 대표 공개 실행 경로를 찾기 위한 것이며 모든 내부 reason을 하나의 enum으로 합친 목록이 아니다.

| 분류·코드 | 발생 문맥 | 메시지 | 원인 | 대응 |
|---|---|---|---|---|
| `1, 5` | 바인딩 개수 검사 | `매개변수 개수가 맞지 않습니다 (SQL %u개)` | placeholder 수와 인자 수가 다르다. | 바인딩을 맞춘다. |
| `1, 20` | 문장 개수 검사 | `한 번에 문장 하나만 실행할 수 있습니다` | 문장이 하나가 아니다. | 호출을 나눈다. |
| `2, 1` | 테이블 조회 | `테이블이 없습니다: %.*s` | 지정 테이블이 없다. | DB와 테이블 이름을 확인한다. |
| `2, 2` | 열 조회 | `컬럼이 없습니다: %.*s` | 지정 열이 없다. | 열 이름을 확인한다. |
| `2, 3` | parser의 잘못된 바인딩 값 | `지원하지 않거나 올바르지 않은 문장입니다 (바이트 %zu)` | 사용한 값이 유효한 UTF-8 또는 유한 REAL이 아니다. | 값과 타입을 고친다. |
| `2, 26` | 실패한 트랜잭션의 후속 문장 | `이 트랜잭션은 이전 문장 오류로 실패한 상태입니다. rollback해야 합니다` | 앞 문장이 실패했다. | rollback한다. |
| `2, 40..53` | 식·집계·집합·서브쿼리·충돌 절 검사 | 없음, 입력 위치와 원인에 따라 다름 | 타입·0 나누기·overflow 또는 식 구성이 잘못됐다. | [database.h](../include/sql/database.h)의 해당 `DATABASE_CODE_*`와 SQL을 대조한다. |
| `3, 8` | INSERT의 NULL 검사 | `NOT NULL 제약을 어겼습니다: %.*s` | 필수 열이 NULL이다. | 열 값을 채운다. |
| `3, 11` | 기본 키·유일 색인 검사 | 없음, 열·색인·발생 경로에 따라 다름 | 키가 중복된다. | 기존 행과 키를 확인한다. |
| `3, 53` | ON CONFLICT 갱신 대상 검사 | `ON CONFLICT DO UPDATE는 한 문장에서 같은 행을 두 번 바꿀 수 없습니다` | 같은 행을 한 문장에서 두 번 갱신한다. | 입력의 충돌 대상을 정리한다. |
| `4, 25` | SQL 입력 길이 검사 | `입력이 한도를 넘습니다` | 입력 실효 상한을 넘었다. | SQL 크기를 줄인다. |
| `5, 24` | 메모리 예산 검사 | 없음, 발생 경로에 따라 다름 | 메모리 예산이 거절됐다. | 입력과 메모리 사용을 줄인다. |
| `6, 19` | poisoned 핸들의 SQL | 없음, 발생 경로에 따라 다름 | 불확정 commit 또는 I/O 실패 뒤의 핸들이다. | 닫고 재개방한다. |
| `6, 32` | 자동 commit 또는 SQL `COMMIT` | `커밋 결과를 알 수 없습니다. 데이터베이스를 닫고 다시 열어 확인하세요` | commit 반영 여부가 불확정이다. | 닫고 재개방하여 업무 상태를 확인한다. |
| `6, 33` | 자동 commit 또는 SQL `COMMIT` | `커밋하지 못했습니다. 아무것도 반영하지 않았습니다` | commit이 확정적으로 미반영이다. | poison 여부와 자원 수명을 확인한다. |
| `7, 29` | 자동 실행의 읽기 전용 경로에서 변경 거절 또는 SQL `BEGIN` 경합 | 없음, 발생 경로에 따라 다름 | writer를 얻지 못했다. | 새 호출에서 재시도한다. |
| `7, 30` | 파일 잠금을 얻는 문장 진입 경로 | `다른 연결이 파일을 쓰는 중입니다` | 다른 핸들이 writer다. | 새 호출에서 재시도한다. |
| `8, 23` | SQL 취소 | `문장이 취소되어 아무것도 반영하지 않았습니다` | 취소 요청을 관측했다. | 열린 트랜잭션을 rollback한다. |

표의 `%u`, `%.*s`, `%zu`는 소스의 형식 문자열이며 실제 메시지에는 개수·이름·위치가 들어간다. 예를 들어 코드 `30`은 분류 `7`에서 파일 잠금 경합이지만 분류 `4`의 트랜잭션 시작 경로에서는 트랜잭션 번호 한도다. Lexer의 reason도 parser의 reason과 다른 번호 집합이다. 각 경로의 정의는 [lexer.h](../include/sql/lexer.h)와 [sql_statement.h](../include/sql/sql_statement.h)에 있다.

메시지는 내부에서 최대 1024바이트로 잘릴 수 있고 C 결과 버퍼에는 잘림 표시가 없다. 메시지 전체 일치만으로 오류를 분류하지 않는다. 문맥별 생성 지점은 [database.c](../src/sql/database.c), [database_txn.c](../src/sql/database_txn.c), [database_insert.c](../src/sql/database_insert.c)에 있다.

## 정상 부재

| 경우 | 결과 |
|---|---|
| SELECT 0행 | `TOTE_OK`, rows 결과, Python `rows == ()` |
| SELECT NULL 한 행 | Python `rows == ((None,),)` |
| 다음 claim 대상 없음 | `TOTE_EMPTY`, Python `None` |
| 빈 payload claim 성공 | `TOTE_OK`, Python `Claim(payload=b'')` |
| 지정 ID 없음 | `TOTE_NOT_FOUND`, Python `NotFoundError` |
| 지정 작업이 현재 claim 불가 | `TOTE_STALE`, Python `StaleError` |
| 목록·정리 대상 0건 | 성공한 빈 목록 또는 처리 수 `0` |

할당 실패는 이 정상 부재 중 어느 것으로도 바꾸지 않는다. `persistqueue_compat`의 `Empty`, blocking과 lease 재개 정책은 native `Queue`와 다른 별도 인터페이스다.

## 실패 뒤 상태

문장 성공, commit 성공, 결과 전달 성공은 서로 다르다. 자동 SQL commit의 불확정은 `SqlError(6, 32)`이고, 명시적 `Transaction.commit()`과 큐 자체 트랜잭션 commit의 불확정은 status `12`의 `UncertainError`다. 이후 poisoned 핸들은 SQL에서 `SqlError(6, 19)`, 큐에서는 `PoisonedError`로 나타날 수 있다.

> [!WARNING]
> 결과 인코딩의 `TOTE_LIMIT`과 `TOTE_NOMEM`은 변경이 commit된 뒤에도 발생할 수 있다. 부분 버퍼가 없다는 사실은 변경 미반영을 뜻하지 않는다.

반환 실패 뒤 쓰기를 재실행하기 전에 업무 키로 저장 상태를 조회한다. 불확정 또는 poison이면 자식 자원을 정리하고 DB를 닫은 뒤 새 핸들로 확인한다. Flat API에는 core의 commit ID 조회 기능이 없다. `NOT_APPLIED`는 해당 commit의 미반영을 뜻하지만 핸들이 정상이라는 뜻은 아니다. 게시 전 fsync 실패도 poison을 남길 수 있다. 게시 경계는 [아키텍처](architecture.md), 장애 시험의 범위는 [안전성](safety.md)에 있다.

명시적 트랜잭션 안 문장 오류 뒤에는 rollback한다. 큐의 `STALE`, `NOT_FOUND`, `CONFLICT`는 무변경 거절이며 호출자 트랜잭션을 실패시키지 않는다. 그 밖의 큐 비OK는 일부 SQL이 이미 수행됐을 수 있으므로 호출자 트랜잭션 전체를 rollback한다. 같은 DB의 업무 SQL과 ACK를 묶는 범위는 [트랜잭션 큐 설계](design/transaction-queue.md)에 있다.

C `tote_commit`과 `tote_rollback`은 일반 성공·실패 모두 트랜잭션 핸들을 해제하고 NULL로 만든다. Python도 호출 시 트랜잭션을 소비한다. Fork 거절은 C 핸들을 소비하지 않는 예외다. 자식은 부모 핸들로 close도 하지 않고 새로 연다. 반환형이 void인 cancel·queue close는 fork 시 no-op이며 모든 함수가 상태 번호를 반환하는 것은 아니다.

C DB close의 BUSY는 살아 있는 큐·트랜잭션을 먼저 끝내야 한다는 뜻이다. Python DB close는 큐부터 닫으므로 열린 트랜잭션 때문에 `BusyError`가 나면 DB는 살아 있어도 큐는 이미 닫혀 있다. 최종 C close가 오류여도 DB 포인터는 이미 해제·NULL일 수 있으므로 옛 포인터를 재사용하지 않는다. 최종 close와 새 호출의 수명 동기화는 호출자가 맡는다.

BUSY 재시도 범위는 [설정](configuration.md#데이터베이스-열기)에 있다. 취소는 협력적 문장 취소이며, 게시 시작 이후에는 실제 결과로 끝난다. 실행 종료와 겹친 취소는 다음 문장 하나에 적용될 수 있다. 반환 버퍼 판독이나 Python decode까지 취소하는 기능은 아니다.
