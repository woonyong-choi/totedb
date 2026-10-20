# 주문 저장과 작업 등록을 한 트랜잭션으로 처리하기

| 항목 | 값 |
|---|---|
| 상태 | 구현 |
| 관련 결정 | [DB·선택형 ACK 큐 범위 결정 #139](https://github.com/woonyong-choi/totedb/issues/139#issuecomment-6172915462) |

## 요약

주문을 저장하면서 큐에 작업을 등록한다. 두 변경은 같은 DB 트랜잭션에서 함께 commit하거나 rollback하고, 워커는 게시된 작업을 가져가 처리한다. ACK 큐는 선택 기능이며 일반 SQL 테이블과 색인 위에서 동작한다. 큐를 설치하지 않은 DB도 SQL만으로 사용한다.

### 하지 않는 것

- 외부 HTTP·파일 쓰기를 DB와 함께 확정하지 않는다. DB 밖의 작업은 ACK 전에 성공했어도 다시 실행할 수 있다.
- 큐 핸들을 열 때 워커를 자동 실행하지 않는다. 애플리케이션이 작업 획득과 처리·종료를 맡는다.

## 해결하는 문제

주문을 저장한 뒤 큐에 작업을 등록하기 전에 프로그램이 멈추면 주문만 남는다. 두 변경을 같은 트랜잭션에 넣으면 이 틈을 없앤다. 워커가 DB의 업무 행을 바꾸고 작업을 끝내는 경우에도 업무 변경과 ACK를 같은 거래로 묶어 한쪽만 남는 일을 막는다.

작업을 가져온 뒤 외부 API 호출과 ACK는 별개다. API 호출은 성공했지만 ACK 전에 워커가 멈추면 같은 작업을 다시 받으므로 외부 서비스의 업무 키를 이용한 멱등성 처리가 필요하다. DB 안의 원자성과 외부 작업의 중복 방지는 서로 다른 책임이다.

## 사용 예

### 주문과 작업의 공동 commit

`db`·`queue`와 `orders(id, status)` 준비는 [README](../../README.md#빠른-시작)에 있다. 같은 DB를 가리키는 거래를 두 호출에 넘긴다.

```python
with db.begin() as txn:
	txn.execute("INSERT INTO orders VALUES (?, ?)", [1, "paid"])
	queue.put(b"process-order-1", txn=txn)
```

1. 거래 안의 SQL과 `put`은 자기 변경을 읽지만 다른 세션에는 아직 보이지 않는다.
2. 예외 없이 거래를 끝내면 두 변경을 함께 게시한다. 그 뒤 워커가 `get`으로 작업을 받을 수 있다.

### commit 전 취소

```python
txn = db.begin()
txn.execute("INSERT INTO orders VALUES (?, ?)", [2, "paid"])
queue.put(b"process-order-2", txn=txn)
txn.rollback()
```

1. rollback은 주문과 작업 등록을 모두 취소한다.
2. 게시 상태에서 주문 2를 조회하면 정상 0행이며 큐에도 새 작업이 없다. 조회 0행은 SQL 실패와 다르다.

### 업무 변경과 ACK의 공동 commit

```python
claim = queue.get(30_000)
if claim is not None:
	with db.begin() as txn:
		txn.execute("UPDATE orders SET status = ? WHERE id = ?", ["done", 1])
		queue.ack(claim, txn=txn)
```

1. `get`은 작업과 현재 처리 권한 번호인 fence를 반환한다. 받을 작업이 없는 정상 결과는 `None`이고, 빈 payload를 받은 성공과 다르다.
2. 업무 행 변경과 ACK는 함께 게시한다. 거래를 rollback하면 업무 행은 이전 상태이고 작업도 LEASED로 남는다.

이 코드는 주문 1의 작업을 받는 단일 작업 사례다. 여러 종류의 작업을 처리하는 앱은 payload로 업무 대상을 확인한다. lease 값은 예제의 입력이지 모든 작업에 권장하는 처리 시간은 아니다.

### ACK 생략 뒤 재획득

1. 워커 A가 작업을 받고 외부 처리를 끝낸 뒤 ACK를 생략한다.
2. lease 만료 뒤 시도가 남아 있으면 워커 B가 같은 작업을 다시 받고 더 큰 fence를 얻는다.
3. A의 옛 fence로 ACK하면 거절하고 B의 현재 fence로 ACK하면 완료한다.

[움직이는 실행 예시](../assets/transaction-queue.html)는 정상 commit, rollback, ACK 생략 장면을 제공한다. [재생에 사용한 값](../assets/transaction-queue.trace.json)은 실제 DB 실행의 주문·큐 관측을 담지만 외부 API 부분은 호출 횟수를 세는 모형이다. 워커 중단은 ACK 생략으로 재현하며 실제 SIGKILL 시험이나 외부 서비스 요청이 아니다. 그림의 세 칸은 표시용이고 제품 큐의 용량이 아니다. 실제 다중 프로세스 예제의 빌드·실행은 [빌드와 시험](../build-and-test.md)의 outbox 안내로 구분한다.

## 작동 방식

### 같은 DB 거래의 경계

`Queue(db, name)`은 같은 DB 안에 큐 테이블 `tote_queue_<name>`, 멱등성 키 보존 테이블 `tote_queue_<name>_keys`와 색인을 설치하거나 연다. 별도 저장 엔진이 아니며 DB 코어는 큐를 참조하지 않는다. 업무 테이블 `orders`는 앱이 정하는 예제이지 제품 고정 스키마가 아니다.

`ackq_scope_open`은 호출자 세션에 거래가 있으면 그 거래를 사용한다. 거래 없이 변경 연산을 호출하면 자체 DURABLE 거래를 열고 끝에서 commit한다. 따라서 `txn=txn`으로 실행한 큐 호출의 성공은 아직 영속 성공이 아니며 최종 거래 commit이 필요하다. 다른 DB의 거래는 실행 전에 거절한다. 저장·게시와 잠금은 [아키텍처](../architecture.md#최종-게시와-결과-전달), 실제 분기는 [ack_queue_sql.c](../../src/queue/ack_queue_sql.c)에 있다.

### 상태와 처리 권한

![워커가 작업을 가져와 ACK하면 완료로 표시하고 기한이 지난 작업은 다른 워커가 다시 가져올 수 있다](../assets/queue-lifecycle.svg)

[상태 경로 재생](../assets/queue-lifecycle.html)은 READY·LEASED·ACKED와 재획득만 보여 준다. 반납·실패·기한 변경은 그림에 추가하지 않고 아래 상태 표로 구분한다.

| 상태 | 뜻 | 다음 상태 |
|---|---|---|
| `READY` | 실행 시각을 기다리는 작업 | 시각이 됐을 때 `get`으로 `LEASED` |
| `LEASED` | fence와 lease를 가진 작업, 만료만으로 상태 변경 없음 | 현재 fence의 `ack`으로 `ACKED`, `fail`로 `FAILED`, `nack`으로 `READY` 또는 시도 한도 도달 시 `FAILED` |
| `ACKED` | 완료를 기록한 작업 | `purge`로 물리 삭제, 같은 fence의 중복 ACK는 성공 |
| `FAILED` | 명시적 실패 또는 시도 한도에 도달한 작업 | `include_failed`를 사용한 `purge`로 물리 삭제 |

`get`은 실행 시각이 된 READY 또는 시도가 남은 만료 LEASED 중 작은 id를 고르고 fence와 attempts를 증가시킨다. 만료 자체는 fence를 무효화하지 않으므로 아직 재획득되지 않았다면 현재 fence로 ACK나 기한 변경을 할 수 있다. 다른 워커가 재획득한 뒤에야 옛 fence를 거절한다. `recover`는 만료 LEASED를 READY 또는 시도가 끝난 FAILED로 바꾸는 별도 연산이며 자동 백그라운드 작업이 아니다.

`extend`는 fence를 유지하고 만료 시각을 현재 시각에 새 lease를 더한 값으로 바꾼다. 기존 만료보다 항상 늦어진다고 보장하지 않는다. 큐 핸들의 close는 lease 반납이 아니다. `release_leases`는 살아 있는 워커가 없다는 판단을 호출자가 한 경우에만 사용한다.

### 중복 등록과 정리

같은 멱등성 키와 같은 payload의 `put`은 기존 작업 id를 반환하고, payload가 다르면 충돌로 거절한다. ACK는 물리 삭제가 아니며 `purge`가 끝난 행을 지운다. 키를 보존하는 설정이면 purge 뒤에도 정해진 기간 동안 키와 payload 요약을 남긴다. 보존 기간 뒤 재등록은 새 작업이고, 요약 비교를 무기한·무충돌 중복 제거라고 부르지 않는다.

큐 옵션은 이름에 저장하는 전역 정책이 아니라 열린 핸들의 값이다. 여러 워커와 재개방 코드가 같은 시도·payload·보존·시계 정책을 전달할 책임이 있다. 정확한 매개변수와 C·Python의 0 변환 차이는 [totedb.h](../../include/totedb.h)와 [queue.py](../../python/totedb/queue.py), 상태 변경 규칙은 [ack_queue.h](../../include/queue/ack_queue.h)에 있다.

### 실패 뒤 거래와 자원

STALE·NOT_FOUND·CONFLICT는 변경 없는 거절이므로 호출자 거래를 실패시키지 않는다. 그 밖의 큐 오류에서는 일부 SQL이 이미 실행됐을 수 있어 호출자 거래 전체를 rollback한다. 불확정 commit은 성공으로 취급하지 않고 DB를 닫아 재개방한 뒤 업무 상태를 확인한다. 같은 DB의 업무 갱신과 ACK를 묶어도 외부 부작용까지 rollback하지는 않는다.

C `get`의 payload는 `tote_queue_item_free`, 목록 payload는 `tote_queue_list_free`로 해제하고 목록 배열 자체는 호출자가 소유한다. Python은 payload를 bytes로 복사하고 native 자원을 해제한다. 거래를 먼저 끝내고 큐·DB를 닫는다. C의 열린 거래나 큐는 DB close를 BUSY로 만들 수 있으며, Python DB close는 등록 큐부터 닫는다. 종료와 오류 규칙은 [totedb.h](../../include/totedb.h), [database.py](../../python/totedb/database.py)에 있다.

### 요구사항

기준 `1ae11543`의 기본 브랜치에 포함된 구현과 대응 시험을 기준으로 한다. 표는 검증하는 동작을 연결하며 장애 주입과 모형 재생을 같은 검증으로 취급하지 않는다.

| 요구사항 | 검증 계획 |
|---|---|
| 큐 없이 SQL을 사용하고 큐 설치는 요청할 때만 수행한다. | [test_ack_queue_python.py](../../tests/test_ack_queue_python.py)의 `test_database_without_queue_supports_crud_and_creates_no_queue_table`, `test_queue_install_creates_table_only_when_requested` |
| 업무 행과 작업 등록을 같은 거래에서 함께 게시한다. | [test_ack_queue.c](../../tests/test_ack_queue.c)의 `test_same_transaction` |
| lease 뒤 같은 작업을 재획득하고 이전 fence를 거절한다. | [test_ack_queue_python.py](../../tests/test_ack_queue_python.py)의 `test_queue_stale_claim_after_lease_expiry_is_rejected`, `test_queue_restart_preserves_state_and_fence_continues` |
| 업무 변경과 ACK는 함께 게시하거나 취소한다. | [test_ack_queue_python.py](../../tests/test_ack_queue_python.py)의 `test_queue_ack_in_same_transaction_commits_or_rolls_back_with_business_update` |
| 실패와 반납, 재시도 시각 및 시도 한도를 구분한다. | [test_ack_queue_python.py](../../tests/test_ack_queue_python.py)의 `test_queue_nack_retry_delay_and_terminal_failure` |
| 키 보존 기간 안의 중복 등록과 충돌을 구분한다. | [test_ack_queue_python.py](../../tests/test_ack_queue_python.py)의 `test_queue_retains_idempotency_key_after_purge` |

## 단점

- 큐 변경도 SQL writer를 사용하므로 업무 쓰기와 큐 연산이 같은 파일의 단일 writer를 두고 경합한다.
- lease 만료 뒤 재전달이 가능하므로 외부 작업은 중복 실행에 대비해야 한다.
- 같은 DB 거래를 길게 잡으면 다른 writer가 기다리므로 외부 요청을 기다리는 동안 거래를 열어 두지 않는다.
