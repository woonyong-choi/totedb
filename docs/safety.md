# 안전성 시험 결과

ToteDB의 SQL 트랜잭션은 게시된 페이지를 덮어쓰지 않는 COW와 로컬 파일 잠금을 전제로 한다. 아래 프로세스 종료·I/O 실패 시험에서는 다시 연 DB가 트랜잭션 전부 또는 전무 상태였고, 성공을 돌려준 commit은 프로세스 종료 뒤에도 남았다. 오류 응답만으로 미반영을 판단하지 않는다. 불확정 결과는 닫고 재개방한 뒤 업무 상태를 확인한다. 이 문서는 시험 조건과 관측, 확인하지 않은 범위를 적는다. 게시와 복구 경계는 [아키텍처](architecture.md#최종-게시와-결과-전달), 호출자 대응은 [오류](errors.md#실패-뒤-상태)에 있다.

현재 결과의 기준은 소스 `1ae11543b0c6b7e28a9381b00bab014474ea04cc`의 2026-10-20 실행이다. macOS 26(Darwin 25)·Apple clang 17 환경에서 `make test-all test-runner test-fault test-result`가 종료 코드 0으로 끝났으며 sanitizer는 undefined였다. 아래 종료·I/O·할당 실패 수치는 이 실행 출력과 대조했다. Linux ASan·TSan이나 전원 차단을 새로 실행한 결과는 아니다.

## 요약

| 확인한 것 | 결과 | 시험 | 재현 |
|---|---|---|---|
| commit 중 모든 쓰기 지점에서 프로세스 종료 | 33곳·57곳(큰 값) 모두 부분 반영 0, 승인한 commit 유실 0 | `tests/test_database_txn.c` `test_crash_points` | `make test-transaction` |
| commit 중 모든 쓰기·fsync 지점의 I/O 실패 | 32곳·56곳(큰 값) 모두 어긋남 0, 결과를 모르는 2곳은 핸들 poison | 같은 시험 | `make test-transaction` |
| 문장마다 할당·I/O 실패 주입 | 7850/7850개 검사 통과 | `tests/test_database_fault.c` | `make test-database` |
| 다시 연 파일의 페이지 회계 | 고아·이중 소유 0 | `database_check_pages`를 쓰는 위 시험들 | `make test-database test-transaction` |
| 쓰는 도중 SIGKILL된 여러 프로세스 | 남은 프로세스가 계속 쓰고, 행 수와 카운터가 같으며, 회계 0 | `tests/test_database_txn.c` `test_processes_killed_while_writing`, `tests/test_multiprocess.py` `test_killed_writer_leaves_no_half_applied_transaction` | `make test-transaction test-ack-queue-python` |
| SIGKILL된 reader | 게시가 다시 진행 | `tests/test_page_file.c` `test_locking_and_unpublished_files` | `make test` |
| 같은 큐를 쓰는 4·16개 프로세스 | 160·640건 유실·중복 0 | `tests/test_multiprocess.py` `test_workers_share_one_queue_without_loss_or_duplicates` | `make test-ack-queue-python` |
| 큐 연산의 I/O·할당 실패 | 1352개 검사 통과 | `tests/test_ack_queue_fault.c` | `make test-ack-queue` |
| claim한 worker의 SIGKILL | lease가 끝난 뒤 다른 worker가 처리, 출고 중복 0 | `tests/test_outbox_example.py` | `make example-outbox` |

## 무엇을 어떻게 시험했나

### 종료 지점

`test_crash_points`는 쓰기 트랜잭션 하나(여러 테이블의 INSERT·UPDATE·DELETE)를 실행하며 파일에 쓰거나 `fsync`하는 지점을 모두 센다. 그 뒤 지점 k마다 fork한 자식이 같은 트랜잭션을 실행하다 k번째 지점에서 곧바로 끝난다. 부모는 파일을 다시 열어 상태가 트랜잭션 전(옛 상태)이거나 트랜잭션 뒤(새 상태)인지 확인한다. 자식이 commit 성공을 받았으면 새 상태여야 한다. 큰 값(15000바이트 overflow 체인)을 넣는 조건으로 한 번 더 실행한다.

| 조건 | 지점 | 종료 | 승인 | 부분 반영·승인 유실 |
|---|---|---|---|---|
| 기본 | 33 | 32 | 1 | 0 |
| 큰 값 | 57 | 56 | 1 | 0 |

### I/O 실패

같은 지점마다 쓰기나 `fsync`가 실패하게 만든다. commit이 성공을 돌려주면 새 상태, 확정 실패면 옛 상태, 결과를 알 수 없으면(제어 페이지를 쓴 뒤 실패) 둘 중 하나이고 그 핸들은 더 쓰지 못한다(poison).

| 조건 | 지점 | 주입 | 불확정 | 어긋남 |
|---|---|---|---|---|
| 기본 | 32 | 32 | 2 | 0 |
| 큰 값 | 56 | 56 | 2 | 0 |

`tests/test_database_fault.c`는 문장 종류마다 할당 실패와 I/O 실패를 차례로 넣는다. 현재 기준 실행은 7850/7850개 검사를 통과했다. 오류 응답 뒤 핸들이 정상인 확정 실패는 게시 상태가 문장 전과 같다. Poison 핸들은 이후 문장을 거절하며, 닫고 다시 열면 문장 전 또는 후 상태다. 제어 페이지 쓰기 뒤 `fsync` 실패는 오류 응답이어도 재개방 시 새 상태가 보이는 불확정 결과다. 무조건 같은 쓰기를 재실행하지 않는다.

### 페이지 회계

`database_check_pages`는 카탈로그에서 도달하는 페이지와 빈 페이지 목록을 대조해 고아(어디서도 가리키지 않는 페이지)와 이중 소유(두 곳이 가리키거나 빈 목록과 겹치는 페이지)를 센다. 종료·I/O 실패 시험과 여러 프로세스 시험은 다시 열 때마다 이 값이 0인지 확인한다.

### 여러 프로세스

- `test_processes_killed_while_writing`: fork한 writer 셋이 큰 값 갱신·행 추가·카운터 증가를 한 트랜잭션으로 계속 확정한다. 부모가 둘을 쓰는 도중 SIGKILL하고, 그 사이 남은 writer의 카운터가 계속 오르는지, 끝난 뒤 행 수와 카운터가 같은지, 페이지 회계가 0인지 확인한다.
- `test_workers_share_one_queue_without_loss_or_duplicates`: spawn한 worker 4·16개가 claim, 처리 기록, ack를 한 트랜잭션으로 처리한다. 처리 기록의 기본 키가 작업 id라 같은 작업을 두 번 처리하면 commit이 실패한다.
- `test_reads_do_not_wait_for_another_process_writing`: 다른 프로세스가 4MB를 쓴 트랜잭션을 쥔 동안 조회 50번이 commit 전 상태를 돌려주고, writer 해제 신호 전에 reader가 끝난다.
- `test_writer_keeps_committing_while_readers_read_continuously`: reader 넷의 첫 조회 신호를 받은 뒤 writer가 commit 40번을 끝내고, 중지 신호 전까지 reader가 살아 있음을 확인한다.
- `test_forked_child_cannot_use_the_parent_handle`: fork한 자식이 부모 핸들로 쓰면 `ForkedHandleError`이고 파일 바이트가 그대로다.

두 읽기 진행 시험은 Event 신호와 프로세스 종료 상태를 검사한다. 60초 대기 한도는 멈춘 시험을 끝내기 위한 제한이지 처리 시간이나 성능 합격선이 아니다.

### 결함 주입으로 시험 자체를 확인

아래는 [최초 안전성 기록](https://github.com/woonyong-choi/totedb/blob/dd42a3a3/docs/safety.md)의 과거 결함 주입 결과다(기록일 2026-10-16). 당시 Linux·ASan 관련 기록과 시간값을 보존한 표이며, 현재 소스에 결함을 다시 넣어 재검증한 결과는 아니다. 개별 결함 주입의 소스 SHA와 측정일은 원기록에 따로 명시되지 않았다.

| 넣은 결함 | 실패한 시험 |
|---|---|
| 행 체인 해제 생략 | 페이지 회계 고아 4 |
| rollback의 새 페이지 반환 생략 | 페이지 회계 고아 6 |
| 놓은 게시 페이지 곧바로 재사용 | 열린 거래 동안 다른 세션 읽기 실패 |
| 다른 프로세스의 게시 뒤 재적재 생략 | `tests/test_multiprocess.py` 4개 |
| 다른 프로세스의 게시 뒤 빈 목록 재적재 생략 | `test_database_txn` 경계 밖 접근(ASan) |
| fork 자식의 fd 사본 정리 생략 | `test_page_file` 중단 |
| writer 우선 관문 생략 | `test_writer_keeps_committing_while_readers_read_continuously`(31초) |
| SELECT의 writer 잠금 사용 | `test_reads_do_not_wait_for_another_process_writing` |
| 큐 연장의 fence 검사 제거 | `tests/test_ack_queue.c` |
| 예제 worker의 ack 생략 | `tests/test_outbox_example.py` |

## SQLite와의 강제 종료 비교

`bench/sqlite_workload.py`로 SQLite(DELETE journal, `synchronous=FULL`)와 같은 입력을 비교했다(소스 `f04f93e`, 2026-10-16 측정). 1천·1만·10만 행마다 15번, 행 절반을 바꾸는 UPDATE를 보낸 뒤 0~20ms 안에 SIGKILL하고 다시 열어 UPDATE 전부 또는 전무인지 확인했다. 두 엔진 모두 위반 0이었다. 다시 열어 기본 키 조회 하나에 답할 때까지 p50은 ToteDB 2.2~2.5ms, SQLite 2.5~3.2ms였다.

이 수치는 과거 Linux 기록이며 2026-10-20의 macOS 기준 시험에서 재측정하지 않았다. 아래 명령은 새 측정을 시작하므로 현재 기준 시험의 재현 명령과 구분한다.

```sh
python3 bench/sqlite_workload.py
python3 bench/sqlite_workload.py --analyze
```

## 검증하지 않은 것

- 전원 차단. 시험은 프로세스 종료와 I/O 오류만 다룬다. 저장 장치가 `fsync` 성공을 돌려준 뒤 쓰기를 버리면(lost write) 이전 세대가 정상으로 열리며 탐지하지 못한다.
- 네트워크 파일 시스템. 잠금은 로컬 파일 시스템의 `flock`을 전제로 한다.
- 여러 writer의 동시 commit. 쓰기 트랜잭션은 파일 전체에서 하나씩 직렬로 실행한다.
- 다른 운영체제·sanitizer 조합의 전수 검증. 과거 Linux 기록과 현재 macOS undefined 실행만 구분해 적었으며, 이번에 Linux ASan·TSan을 다시 실행하지 않았다.
