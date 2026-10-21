# 주문 저장과 작업 등록을 한 트랜잭션으로 처리하기

주문을 저장하면서 큐에 작업을 등록한다. 두 변경을 같은 트랜잭션으로 커밋하고, 워커는 큐에서 작업을 가져가 처리한다.

## 해결하는 문제

주문을 저장한 뒤 큐에 작업을 등록하기 전에 프로그램이 멈추면 주문만 남는다. 두 변경을 같은 트랜잭션에 넣어 이 문제를 막는다. 커밋하면 함께 저장하고, 롤백하면 함께 취소한다. 작업을 가져온 뒤의 외부 API 호출과 ACK는 이 트랜잭션에 포함하지 않는다.

## 사용 예

```python
with db.begin() as txn:
    txn.execute("INSERT INTO orders VALUES (?, ?)", [1, "paid"])
    queue.put(b"process-order-1", txn=txn)
```

`db`와 `queue`를 여는 전체 예제는 [README](../../README.md#빠른-시작)에 있다. 저장을 끝낸 뒤에는 워커가 `get`으로 작업을 가져온다.

## 작동 방식

[움직이는 실행 예시: 넓은 화면](../assets/transaction-queue.html)과 [좁은 화면](../assets/transaction-queue-narrow.html)에서 주문 저장, 커밋 전 취소, 워커 중단 장면을 고른다. 큐에 남은 작업 수와 워커 A·B가 받은 작업, 처리 번호가 어떻게 바뀌는지 볼 수 있다.

아래 그림은 워커가 작업을 가져오고 ACK를 보낼 때 상태가 어떻게 바뀌는지 보여 준다.

<picture>
<source media="(max-width: 600px)" srcset="../assets/queue-lifecycle-narrow.svg" width="321.2" height="297">
<img src="../assets/queue-lifecycle.svg" alt="워커가 작업을 가져와 ACK하면 완료로 표시하고 기한이 지난 작업은 다른 워커가 다시 가져올 수 있다" width="100%">
</picture>

[상태 경로 재생: 넓은 화면](../assets/queue-lifecycle.html)과 [좁은 화면](../assets/queue-lifecycle-narrow.html)

`get`으로 작업을 받고 ACK로 완료를 알린다. 그림의 대기, 처리 중, 완료는 각각 내부 상태 `READY`, `LEASED`, `ACKED`에 해당한다.

작업을 재획득하면 처리 권한 번호(`fence`)가 증가한다. 이전 번호의 ACK는 거절된다. 처리 기한(Lease)이 지나도 DB 상태는 다음 `get`이 재획득할 때까지 `LEASED`로 유지되며, 중간에 `READY`로 전환하지 않는다.

| 상황 | 결과 |
|---|---|
| 주문과 작업을 같은 트랜잭션으로 커밋한다 | 둘 다 저장한다 |
| 커밋 전에 롤백한다 | 두 변경을 모두 취소한다 |
| 워커가 ACK 전에 멈춘다 | 기한이 지난 뒤 작업을 다시 가져올 수 있다 |
| 다른 워커가 작업을 재획득한다 | 이전 처리 권한 번호의 ACK를 거절한다 |

외부 API 호출에 성공해도 ACK 전에 멈추면 같은 API를 다시 호출할 수 있다. DB 변경을 원자적으로 커밋한다고 해서 외부 작업도 정확히 한 번만 실행되는 것은 아니다. 외부 작업에는 중복 실행에 대비한 멱등성 처리가 필요하다.

예시에서는 외부 API 호출 대신 호출 횟수를 세는 함수를 썼다. 워커가 멈춘 상황은 ACK를 하지 않는 방식으로 재현했다. 실제 프로세스를 강제 종료하거나 외부 서비스에 요청을 보내지는 않았다. [재생에 사용한 값](../assets/transaction-queue.trace.json)을 함께 확인할 수 있다.

반납, 실패 처리와 기한 연장은 이 그림에 포함하지 않았다. 전체 동작과 저장·잠금 조건은 [아키텍처](https://github.com/woonyong-choi/totedb/blob/main/docs/architecture.md#큐-작업)와 [안전성 시험](https://github.com/woonyong-choi/totedb/blob/main/docs/safety.md)을 함께 확인한다.
