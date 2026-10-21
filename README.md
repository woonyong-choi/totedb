<h1 align="center">ToteDB</h1>

<p align="center">
  애플리케이션에 내장해 사용하는 SQL 데이터베이스입니다.<br>
  데이터 변경과 작업 등록을 하나의 트랜잭션으로 커밋할 수 있습니다.
</p>

<p align="center"><a href="https://pypi.org/project/totedb/"><img src="https://img.shields.io/pypi/v/totedb" alt="PyPI 버전"></a> <a href="https://github.com/woonyong-choi/totedb/actions/workflows/ci.yml"><img src="https://github.com/woonyong-choi/totedb/actions/workflows/ci.yml/badge.svg" alt="CI"></a> <a href="https://github.com/woonyong-choi/totedb/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue" alt="MIT 라이선스"></a></p>

---

<picture>
<source media="(max-width: 999px)" srcset="docs/assets/transaction-queue-narrow.svg" width="340.7" height="2003.1">
<img src="docs/assets/transaction-queue.svg" alt="주문과 작업을 같은 트랜잭션으로 저장하고 워커가 작업을 받아 ACK를 보냅니다" width="100%">
</picture>

[시나리오 선택하기: 넓은 화면](docs/assets/transaction-queue.html) · [좁은 화면](docs/assets/transaction-queue-narrow.html)

주문을 저장하면서 큐에 작업을 등록하고, 워커가 그 작업을 처리하는 흐름입니다. 재생 화면에서 정상 처리, 롤백, 워커 중단 후 재전달 과정을 선택할 수 있습니다.

작업 큐가 필요 없다면 SQL 데이터베이스로만 사용할 수도 있습니다.

첫 장면은 주문 3건과 작업 3건을 함께 커밋한 뒤 작업 1건을 처리합니다. 채워진 큐 칸은 대기 중인 작업을 뜻합니다. 이 예시에서 3칸을 사용했을 뿐, ToteDB의 큐에 3건 제한이 있는 것은 아닙니다.

트랜잭션 안에서 조회한 값과 커밋된 DB의 값을 구분해 표시했습니다. 커밋 전의 DB 숫자는 마지막 커밋 상태를 그대로 보여 줍니다. 별도 연결에서 동시에 조회한 결과는 아닙니다.

외부 API를 호출하는 대신 호출 횟수만 세는 모의(Mock) 함수를 사용했습니다. 워커 중단은 ACK를 생략해 재현했으며 프로세스를 강제 종료하지는 않았습니다. 동작을 따라가기 쉽도록 재생 시간을 늘렸으므로 성능 측정값으로 해석해서는 안 됩니다.

## 빠른 시작

Python 3.9 이상이 필요합니다. 가상 환경(venv)에서 설치하세요.

Linux(x86_64·aarch64)와 macOS(arm64)용 wheel 패키지를 제공합니다. 다른 POSIX 환경에서 빌드하려면 C 컴파일러와 GNU Make가 필요합니다.

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install totedb
```

아래 코드는 `orders.db`에 주문을 저장하고 큐에 처리할 작업을 등록합니다. 테이블 생성은 처음 한 번만 실행합니다. 기존 DB를 삭제하거나 덮어쓰는 코드는 넣지 않았습니다.

```python
from totedb import Database, Queue

with Database("orders.db") as db:
    db.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, status TEXT)")
    queue = Queue(db, "orders")
    with db.begin() as txn:
        txn.execute("INSERT INTO orders VALUES (?, ?)", [1, "paid"])
        queue.put(b"process-order-1", txn=txn)

    claim = queue.get(lease_ms=30_000)
    print(db.execute("SELECT * FROM orders").rows)
    print(claim.payload)
    queue.ack(claim)
```

```text
((1, 'paid'),)
b'process-order-1'
```

주문 저장과 작업 등록을 같은 트랜잭션으로 커밋합니다. 그다음 큐에서 작업을 하나 가져와 내용을 출력하고 ACK를 보냅니다. 실제 주문 처리나 외부 API 호출은 하지 않습니다.

프로그램이 종료돼도 DB 파일은 남습니다. 다른 프로세스에서 같은 파일을 다시 열어 저장한 데이터를 조회할 수 있습니다.

```python
from totedb import Database

with Database("orders.db") as db:
    print(db.execute("SELECT * FROM orders").rows)
```

실제 애플리케이션에서는 스키마를 준비하는 코드와 주문을 처리하는 코드를 분리하고, 요청마다 고유한 주문 ID를 사용합니다. `CREATE TABLE IF NOT EXISTS`는 현재 지원하지 않습니다.

## 해결하는 문제

주문을 저장한 뒤 큐에 작업을 등록하기 전에 프로그램이 멈추면 주문만 남을 수 있습니다. ToteDB에서는 두 변경을 같은 트랜잭션으로 커밋하거나 롤백할 수 있습니다. 주문만 저장되고 작업은 빠지는 상황을 막습니다. 별도의 DB 서버나 메시지 큐 서버도 필요하지 않습니다.

워커가 작업을 끝냈더라도 ACK 전에 멈추면 같은 작업이 다시 전달될 수 있습니다. 메일 발송이나 외부 API 호출에는 중복 실행에 대비한 멱등성 처리가 필요합니다.

## SQLite·Python 도구와 비교

2026-10-19의 [비교 실험](https://github.com/woonyong-choi/totedb/blob/main/docs/experiments/local-db-comparison/report.md)에서 ToteDB는 SQLite WAL/FULL보다 DB 기록 처리량이 낮았고, `persist-queue`와 Litequeue보다 ACK 큐 처리도 느렸습니다.

아래 수치는 단일 클라이언트에서 측정한 5회 중앙값입니다. 높을수록 처리량이 많습니다. 현재 배포판을 새로 측정한 결과는 아닙니다.

측정 환경은 Ubuntu 24.04·Python 3.12.15·SQLite 3.45.1입니다. 큐는 `persist-queue` 1.1.0·Litequeue 0.14, ToteDB는 커밋 `a86cf241`을 사용했습니다. 전체 설정은 [실험 보고서](https://github.com/woonyong-choi/totedb/blob/main/docs/experiments/local-db-comparison/report.md)에 있습니다.

### DB 기록 처리량

단위는 논리 작업/초입니다. 각 실행은 512작업이며, 한 건 기록은 매번 커밋합니다. 두 건 함께 기록은 업무 행과 outbox 행을 한 트랜잭션으로 저장합니다. 32쌍 배치는 이 두 행 묶음 32개를 한 번에 커밋합니다. 이 표는 DB 기록 성능이며, 완성된 ACK 큐를 실행한 결과는 아닙니다.

| 작업 | 본문 크기 | ToteDB | SQLite DELETE/FULL | SQLite WAL/FULL |
|---|---|---|---|---|
| 한 건 기록 | 256B | 2,206 | 1,279 | 2,546 |
| 한 건 기록 | 4,096B | 1,745 | 954 | 3,100 |
| 두 건 함께 기록 | 256B | 1,854 | 1,246 | 2,820 |
| 두 건 함께 기록 | 4,096B | 1,409 | 903 | 3,266 |
| 32쌍 배치 | 256B | 8,128 | 25,616 | 49,044 |
| 32쌍 배치 | 4,096B | 5,905 | 17,672 | 18,070 |

ToteDB는 작업마다 커밋할 때 SQLite DELETE/FULL보다 높았지만, WAL/FULL보다 낮았습니다. 배치 처리에서는 두 SQLite 설정 모두보다 낮았습니다.

### Python ACK 큐 처리량

단위는 작업/초입니다. 모든 작업을 넣은 뒤 꺼내서 ACK를 마칠 때까지 측정했습니다. DB 준비·종료·재개방 검사·완료 작업 정리는 측정에서 제외했습니다. 각 칸은 중앙값 [최소, 최대]입니다.

| 작업 수 | 본문 크기 | ToteDB | persist-queue SQLiteAckQueue | Litequeue |
|---|---|---|---|---|
| 128 | 256B | 702 [661, 732] | 898 [822, 928] | 772 [749, 844] |
| 128 | 4,096B | 596 [541, 626] | 833 [769, 986] | 924 [836, 997] |
| 1,024 | 256B | 599 [583, 604] | 1,649 [1,633, 1,685] | 1,461 [1,400, 1,490] |
| 1,024 | 4,096B | 522 [494, 533] | 855 [835, 867] | 1,327 [1,288, 1,334] |

측정한 네 조건 모두 ToteDB가 느렸습니다. 1,024개·256B 조건에서 반복별 ToteDB/persist-queue 처리량 비의 중앙값은 0.3628, 95% bootstrap 구간은 [0.3458, 0.3639]였습니다. 현재 측정 결과로는 큐 속도를 ToteDB의 장점으로 설명할 수 없습니다.

이 비교는 정상 처리 경로에 한정됩니다. SQLite 큐의 WAL/FULL 설정을 확인했지만 직렬화 등은 각 라이브러리의 방식을 유지했습니다. Lease 만료·재전달·오래된 ACK 거절 등 실패 복구 동작은 서로 다르며, 이 실험에서는 그 비용을 측정하지 않았습니다. 전체 설정·반복별 수치·검사 결과는 [실험 보고서](https://github.com/woonyong-choi/totedb/blob/main/docs/experiments/local-db-comparison/report.md)와 [집계 원본](https://github.com/woonyong-choi/totedb/blob/main/docs/experiments/local-db-comparison/results/summary.json)에 있습니다.

### 기능과 제약

SQLite에서도 주문 테이블과 작업 테이블을 같은 연결로 갱신하면 함께 커밋할 수 있습니다. ToteDB만 가능한 기능은 아닙니다. 아래는 Python 표준 라이브러리 `sqlite3`, SQLite 기반 큐 라이브러리인 `persist-queue`의 `SQLiteAckQueue`, ToteDB의 기본 `Queue` API를 비교한 내용입니다.

아래 기능 비교는 `persist-queue` 1.1.0의 `SQLiteAckQueue`와 ToteDB 0.2.0의 기본 `Queue` API를 기준으로 합니다.

| 항목 | SQLite + Python `sqlite3` | SQLite + `persist-queue` | ToteDB |
|---|---|---|---|
| 제공 기능 | SQL DB. 큐는 직접 구현 | SQLite 기반 영속 큐와 ACK | SQL DB와 ACK 기반 큐 |
| 주문 저장과 작업 등록의 원자성 | 같은 연결의 트랜잭션으로 가능 | 공개 `put` API에 외부 트랜잭션을 전달할 수 없음 | `put(txn=txn)`으로 주문 저장과 함께 커밋·롤백 |
| ACK와 재시도 | 상태 테이블과 처리 코드 직접 구현 | `ack`·`nack` 제공 | `ack`·`nack` 제공 |
| 워커 중단 후 재전달 | 처리 기한과 복구 코드 직접 구현 | `auto_resume=True`로 큐를 열 때 미확인 작업을 다시 대기 상태로 변경 | Lease가 지나면 다음 `get`에서 재획득 |
| 다른 워커가 받은 작업에 이전 워커가 ACK | 작업 소유권 검사 직접 구현 | 작업 ID로 ACK 가능. 재수신 후 이전 인스턴스의 ACK도 허용됨 | `fence` 검사로 이전 claim의 ACK 거절 |
| 작업 데이터 | 테이블 형식과 직렬화 직접 결정 | 기본 pickle 직렬화로 Python 객체 저장 | `bytes` 저장. 객체 직렬화는 호출자가 처리 |
| SQL과 파일 관리 | SQLite SQL·`VACUUM` 사용 가능 | 큐 API는 범용 SQL API가 아님. 파일 축소 메서드 제공 | SQL 일부 지원. `CREATE TABLE IF NOT EXISTS`·`VACUUM` 미지원 |

`SQLiteAckQueue`의 `get(timeout=...)`은 작업을 기다리는 시간이며, 가져온 작업의 처리 기한인 Lease와는 다릅니다. ToteDB도 외부 API의 중복 호출까지 막지는 않습니다.

## 작동 방식

작업을 처리하는 코드는 애플리케이션에서 작성합니다. 워커는 `get`으로 작업을 가져와 처리한 뒤 `ack`로 완료를 알립니다. ACK 없이 처리 기한(Lease)이 지나면 다른 워커가 같은 작업을 다시 가져갈 수 있습니다.

다른 워커가 같은 작업을 가져가면 `fence`가 증가합니다. DB는 이 번호로 현재 작업을 받은 워커의 ACK인지 확인하고, 이전 번호의 ACK는 거절합니다. 롤백과 재전달 과정은 [주문 저장과 작업 등록을 한 트랜잭션으로 처리하기](docs/design/transaction-queue.md)에서 볼 수 있습니다. 저장 방식과 잠금 규칙은 [아키텍처](https://github.com/woonyong-choi/totedb/blob/main/docs/architecture.md)에 있습니다.

## 지원 기능과 제약 사항

[v0.2.0 릴리스](https://github.com/woonyong-choi/totedb/releases/tag/v0.2.0)를 제공합니다. DB 파일을 저장하고 다시 여는 기능, 트랜잭션과 작업 큐를 실제 애플리케이션에서 사용할 수 있습니다.

SQL 조회·갱신과 JOIN·집계·서브쿼리를 지원합니다. C와 Python에서 사용할 수 있고, 작업 큐는 필요할 때 추가합니다.

여러 프로세스가 같은 DB 파일을 열 수 있지만 쓰기 트랜잭션은 한 번에 하나만 실행됩니다. NFS 등 네트워크 파일 시스템은 지원하지 않습니다.

더 많은 SQL 기능이 필요하다면 SQLite를, 동시 쓰기와 서버 운영 기능이 필요하다면 PostgreSQL을 함께 검토하세요. 1.0 전에는 파일 형식과 API가 바뀔 수 있습니다.

강제 종료와 I/O 오류에서 확인한 동작은 [안전성 테스트](https://github.com/woonyong-choi/totedb/blob/main/docs/safety.md)에 정리했습니다. 실제 전원 차단까지 검증한 것은 아닙니다.

## 문서

- [주문 저장과 작업 등록을 한 트랜잭션으로 처리하기](docs/design/transaction-queue.md)
- [아키텍처](https://github.com/woonyong-choi/totedb/blob/main/docs/architecture.md)
  - [빌드 및 테스트](https://github.com/woonyong-choi/totedb/blob/main/docs/build-and-test.md)
  - [안전성 테스트](https://github.com/woonyong-choi/totedb/blob/main/docs/safety.md)
- [멀티 프로세스 Outbox 예제](https://github.com/woonyong-choi/totedb/tree/main/examples/outbox)
- [전체 문서](https://github.com/woonyong-choi/totedb/blob/main/docs/README.md)

## 개발

빌드와 테스트에는 GCC 또는 Clang, GNU Make, Python 3, pytest가 필요합니다. SQLite와 결과를 대조하는 SQL 차등 테스트에는 SQLite 개발 라이브러리도 필요합니다.

```sh
make all
make test-all test-runner test-fault test-result
make test-sql-diff
```

저장소의 Python 바인딩을 직접 사용할 때는 `PYTHONPATH=python`을 지정하세요. 테스트 범위와 PR 제출 절차는 [기여 안내](https://github.com/woonyong-choi/totedb/blob/main/.github/CONTRIBUTING.md)에 있습니다.

## 라이선스

[MIT](https://github.com/woonyong-choi/totedb/blob/main/LICENSE)
