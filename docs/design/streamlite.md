# StreamLite 리뉴얼 구조

| 항목 | 값 |
|---|---|
| 상태 | 제안 |
| 관련 검토 | [구조 결정 #257](https://github.com/woonyong-choi/totedb/issues/257), [브랜드 전환 #258](https://github.com/woonyong-choi/totedb/issues/258) |
| 브랜드 | StreamLite, 기존 배포·API의 ToteDB 명칭 유지 |

## 요약

StreamLite는 SQL, 영속 이벤트 스트림, ACK 작업 처리를 각각 선택하거나 같은 트랜잭션에서 결합하는 내장 엔진을 목표로 한다. 여러 독립 쓰기 트랜잭션이 실제로 겹쳐 진행하면서 결과·격리·복구·자원 상한을 지키는 것이 다중 writer의 요구다. 요청을 받아 한 writer가 차례로 실행하는 것만으로 이 요구를 충족했다고 표시하지 않는다.

현재 구현은 SQL 부분집합·단일 writer·선택형 ACK다. 문장 캐시·트랜잭션 변경 누적·영속 fanout 스트림·실제 동시 writer의 갱신 설계는 구현 완료가 아니다. StreamLite 표시명을 채택해도 기존 `totedb` 패키지·Python import·`tote_*` C ABI·`libtotedb`·DB 파일 형식은 그대로 둔다.

### 하지 않는 것

- NATS wire protocol 전체 호환: 사용 중인 애플리케이션 EventBus를 구현하는 것과 다른 호환성 과업이다.
- 다중 노드 복제·자동 failover: 로컬 다중 writer와 분산 합의는 별개다.
- 기존 파일 자동 덮어쓰기: 새 저장 형식은 별도 이관·내용 대조·복원 검증이 필요하다.
- 외부 작업의 exactly-once: DB 밖 HTTP·메일·결제에는 별도 멱등 처리가 필요하다.

## 해결하는 문제

로컬 작업의 재시작 보존, 외부 서비스 없는 배포, 업무 상태와 enqueue의 분리 commit, 오래 쌓인 이벤트의 보존·메모리 비용에는 실제 요구가 있다. [Inngest 개발 서버 요청](https://github.com/inngest/inngest/issues/5028)은 외부 DB 없는 개발용 persistence를, [goqite 배포 요구](https://github.com/maragudk/goqite/discussions/53)는 배포 마찰을, [Moneymentum 결함](https://github.com/dataclique/moneymentum/issues/404)은 상태 기록과 enqueue 사이의 실패를 보여 준다. 이 사례만으로 자체 SQL 엔진·모든 기능 결합·유료 고객 수요를 입증하지 않는다. 2026-10-23 확인이다.

SQLite 기반 큐·outbox, DBOS 같은 내구 실행 SDK, 단일 노드 NATS도 대안이다. 제품의 채택 조건은 필요한 로컬 전달 의미를 충족하고, 같은 보장 아래 전체 자원·배포·연동 비용을 줄이며, SQLite 조합 대비 이익을 보여 주는 것이다. 현재 [실측](../experiments/local-db-comparison/report.md)은 속도 우위를 보여 주지 않는다.

## 사용 예

### SQL만 사용하는 애플리케이션

1. 앱은 일반 SQL로 데이터를 저장하고 조회한다.
2. 큐·스트림 객체나 소비자 상태를 생성하지 않는다.
3. 코어의 SQL 실행과 commit 경로에 큐 전용 polling·조회·hook을 추가하지 않는다.

### 여러 로컬 처리자의 독립 소비

1. 생산자는 이벤트 원본을 기록한다.
2. 같은 그룹의 처리자들은 작업을 나눠 처리한다.
3. 다른 소비자 그룹은 같은 이벤트를 자기 진행 위치와 ACK 상태로 독립 처리한다.
4. 한 그룹의 실패나 ACK가 다른 그룹의 전달 완료를 뜻하지 않는다.

### 업무 상태와 이벤트의 같은 commit

1. 같은 DB의 업무 변경·이벤트 기록 또는 로컬 효과·소비 완료를 한 트랜잭션으로 확정한다.
2. 업무 DB를 PostgreSQL에 그대로 두면 두 저장소 사이의 outbox·재전송 경계는 남는다.
3. 브로커 교체만으로 외부 DB와의 원자성이 생겼다고 표시하지 않는다.

## 작동 방식

### SQL과 저장의 공통 비용

SQL template와 실행별 binding을 분리하고 제한된 준비 결과 캐시를 검토한다. 현재 AST에는 binding 값이 복제되므로 SQL 문자열만 key로 AST를 재사용하지 않는다. 무효화는 schema 의미와 logical ID에 연결하고, 데이터 commit마다 물리 root가 바뀐다는 이유로 모든 준비 결과를 버리지 않는다.

불변 schema와 트랜잭션의 root·행 수 등 변경 상태를 분리한다. transaction-private dirty workspace에서 문장 간 변경을 누적하고 중복 인코딩·페이지 쓰기를 줄인다. 기존 fresh-page 재사용·읽기 cache는 유지하되 이것만으로 문장별 heap 완료 비용이 없어졌다고 보지 않는다. cache·결과·read/write set·private page·spill·회수 pin을 예산에 포함하고 OS 페이지 cache는 별도 측정한다.

### 영속 로그와 소비자 상태

이벤트 원본은 하나의 논리 payload 정본으로 보존하고, 소비자별 cursor·ACK hole·lease·fence·시도·실패 상태를 hot metadata로 관리한다. 원본 하나라는 뜻은 WAL·COW·cache·FFI 복사가 전혀 없다는 뜻이 아니다. 우선 같은 저장 backend와 transaction을 사용하고 별도 segment가 필요하면 그 끝 위치·checksum과 SQL root를 함께 commit하는 계약을 정한다.

새 claim은 소비자·이벤트·현재 상태를 조건으로 소유권을 얻고 fence를 갱신한다. ACK·retry·실패 격리·redrive는 해당 소비자의 소유권과 상태를 검사한다. 재생 위치와 보존 한도, 긴 reader·느린 소비자의 pin을 함께 관리한다. 느린 소비자를 무기한 보존하면서 저장량 제한과 모든 publish 성공을 동시에 약속하지 않는다.

### 다중 writer의 검증·병합·게시

각 transaction은 snapshot·schema version·필요 read/range set·logical mutation·private dirty map·할당/회수 집합을 소유한다. 다른 행을 바꿔도 같은 leaf·ancestor·catalog root를 공유할 수 있으므로, 옛 root를 그대로 교체하는 방식은 금지한다. 읽기·unique/range 충돌을 검증하고 최신 root와 변경을 병합한 뒤 짧은 게시 구간에서 다시 확인한다.

같은 프로세스의 여러 connection과 현재 지원하는 같은 호스트 다중 프로세스를 구분해 검증한다. 회수는 활성 snapshot과 복구 fallback root의 참조가 끝난 뒤에만 수행한다. 현재 두 commit 지연 규칙을 임의 길이 snapshot의 다중 writer에 그대로 적용하지 않는다. 예약 sequence와 committed visibility를 구분하고 미완료 번호를 소비자가 완료로 건너뛰지 않게 한다.

group commit은 독립 transaction의 충돌·결과·순서를 유지하고 자신의 결과가 포함된 durable barrier 뒤에만 성공을 응답한다. 게시 시작 뒤 timeout을 무반영 성공으로 돌려주지 않고 NOT_APPLIED와 UNKNOWN을 구분한다. 외부 callback이나 달라진 RETURNING 결과를 몰래 재실행하는 retry도 금지한다.

### 성능과 안전을 함께 고르는 순서

조건부 출발점은 공통 비용 제거 후 buffered COW·검증/병합과 로그·hot state 모델을 분리 검증하는 것이다. 기존 COW가 항상 가장 빠르거나 안전하다는 결정은 아니다. shared-root rebase·게시 대기·경로 재작성·snapshot 회수가 목표 tail·공정성·자원 상한을 못 채우거나 새 검증 비용이 WAL 설계 수준이면 WAL·buffer pool·MVCC 안과 비교한다.

WAL 안도 WAL-before-data·page LSN·torn-page 보호·복구·checkpoint·version GC가 필요하다. SQLite·LMDB 또는 WAL이라는 이름만으로 진짜 동시 writer와 복구 안전성을 충족했다고 표시하지 않는다. 일반 SQLite backend는 총 구현 위험을 줄이는 대안이지만 실제 병렬 writer 요구는 충족하지 못한다.

### 요구사항

| 요구사항 | 검증 계획 |
|---|---|
| SQL·ACK·stream을 독립 또는 같은 DB 거래로 결합한다. | SQL 단독·ACK 단독·stream 단독·결합의 결과와 미사용 기능 호출·할당·I/O 대조 |
| 독립 transaction이 실제로 겹쳐 진행한다. | 다른 table·같은 leaf의 다른 행·같은 행·unique/range·append·공유 cursor의 진행과 충돌 검사 |
| 입력·결과·메모리 소유권과 자원 한도를 유지한다. | 반복 binding·schema 변경·cache hit/miss·eviction·OOM·spill·긴 reader·중단 writer 검사 |
| claim·ACK·재생·실패 격리가 소비자별로 정확하다. | fanout·out-of-order ACK·lease 재획득·stale fence·retry·DLQ·redrive·sequence hole 검사 |
| 성공 응답과 저장·복구 결과가 일치한다. | 게시 전후 종료·짧은/torn/reordered write·sync 실패·disk full·UNKNOWN 재조회·이관/복원 검사 |
| 빈번한 갱신·삭제에서 총비용을 줄인다. | PUT/CLAIM/ACK/DELETE steady cycles, cleanup·checkpoint·회수 포함 CPU·지연·쓰기량·파일 성장 측정 |
| 단일 노드 JetStream·SQLite 대안과 같은 보장을 비교한다. | NATS R=1 file의 periodic sync와 per-write sync를 구분하고 전체 앱 자원·배포·통합 비용 비교 |

## 단점

- COW의 병합·회수와 WAL의 복구·checkpoint 모두 새 안전성 검증 비용이 크다.
- 소비자·ACK hole·보존·실패 metadata는 별도 상한과 청소가 필요하다.
- 별도 server가 없다는 사실만으로 NATS보다 CPU·RAM·지연이 작다는 결론은 나오지 않는다.
- 원래의 bounded 조회·장기 삭제 효율과 현재 성능 열세를 이름 변경으로 해결할 수는 없다.

## 대안

- SQLite 기반 큐·outbox: 구현·복구 부담을 줄일 수 있으므로 총비용 비교에 남긴다.
- 단일 노드 NATS: 이미 가볍게 배포할 수 있고 Go에는 서버 내장도 가능하므로 다중 노드 설정만 비교하지 않는다.
- WAL 기반 저장: 동시성·갱신과 복구 비용을 같은 workload로 대조한 뒤 채택 여부를 판단한다.

## 미해결

- isolation·conflict 반환·자동 retry 범위·다중 프로세스 계약의 확정과 구현 분할 ([#257](https://github.com/woonyong-choi/totedb/issues/257))
- workload·지연·RAM·저장량 예산과 효과 채택 기준, COW/WAL 분리 실험 ([#257](https://github.com/woonyong-choi/totedb/issues/257))
- 느린 소비자·보존 한도 충돌, 지원 subject·replay·DLQ/redrive의 정확한 API 계약 ([#257](https://github.com/woonyong-choi/totedb/issues/257))
- 표시명 이후 패키지·import·ABI·경로·파일 형식의 별도 호환 전환과 상표 등 권리 확인 ([#258](https://github.com/woonyong-choi/totedb/issues/258))
