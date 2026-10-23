# ToteDB가 문제와 검증에 따라 바꾼 선택

ToteDB의 현재 목적은 SQLite 기반 Python 큐에서 겪는 사용성·기능 제약을 줄이고, SQL과 편리한 작업 완료 확인(ACK)을 선택해서 쓰는 로컬 DB를 만드는 것이다. 특히 persist-queue 1.1.0에서 전체 payload를 복사하는 목록 조회, 처리권 기한(lease)·claim 세대 번호(fence)와 호출자 트랜잭션을 드러내지 않는 API가 개선 대상이다. 큐의 빈번한 INSERT·UPDATE·DELETE에 맞는 실행·저장·회수 구조도 목표다. SQLite 위에 필요한 ACK 상태기계를 직접 구현하는 대안도 가능하므로, 이 요구가 새 저장 엔진의 필요성을 증명하지는 않는다. 확인된 구현은 독립 SQL DB, 선택형 ACK, 메모리 예산, 변경을 새 페이지에 쓰는 COW(copy-on-write) 게시와 실패 구분이며, 현재 메모리 우위와 장기 삭제·재사용 효율은 입증하지 못했다. 실제 Python ACK 큐 비교에서는 ToteDB가 네 조건 모두 뒤처졌다. 이 제품 목적은 2026-10-20 회고에서 재확인한 현재 설명이며, 4월 팀 MiniDB 과제의 시작 동기와 구분한다. ([제품 경계 #139](https://github.com/woonyong-choi/totedb/issues/139), [첫 소비 계약 #140](https://github.com/woonyong-choi/totedb/issues/140), [현재 목표 #239](https://github.com/woonyong-choi/totedb/issues/239), [실제 큐 비교](experiments/local-db-comparison/report.md))

예를 들어 작업 목록 화면이 id와 상태만 필요할 때 모든 payload를 읽는 대신 제한된 metadata 목록을 받는 것이 요구다. 주문 저장과 작업 등록을 함께 확정하는 기능은 또 다른 선택이다. SQL만 사용하거나 업무 테이블 없이 ACK 큐만 사용하는 방식도 남긴다. 따라서 범용 SQL과 큐 전용 제품 중 하나를 고르는 구도가 아니며, outbox만을 위한 DB도 아니다. ([목록 API](../include/queue/ack_queue.h#L195-L216), [세 사용 방식](experiments/local-db-comparison/report.md#선택형-api와-범용성))

## 읽는 기준과 근거의 범위

이 문서는 CTO와 미래 기여자가 주요 제품·아키텍처·보장·측정 선택의 문제, 대안, 이유, 검증과 현재 상태를 함께 이해하도록 쓴 설명이다. D01~D51은 이 보고서의 추적 번호다. 당시 작성된 ADR이나 51번의 순차 회의를 뜻하지 않는다. 개별 lint·서식·rename 수정의 전수 Git 감사와 기록되지 않은 모든 선택은 범위 밖이다. 코드의 기초 선택 이유가 없으면 미확인으로 남긴다.

공개 코드의 현재 해석 기준은 `e49a3d2f252c0e501bc7e76cc064e77e6ec98673`이다. 과거 선택에는 해당 커밋을, 수치에는 실험별 제품·도구 버전과 공개 결과를 사용한다. 이슈 본문은 수정 가능하므로 생성 시각을 최초 발견일이나 본문 전체의 작성일로 단정하지 않는다. first-parent 이력의 반영 순서와 커밋 작성 시각도 다를 수 있다. 이번 보고서 작성에서는 빌드·성능·장애 실험을 재실행하지 않았다. 시험 통과는 연결한 공개 기록의 보고값이다. ([기준 코드](https://github.com/woonyong-choi/totedb/tree/e49a3d2f252c0e501bc7e76cc064e77e6ec98673), [보고 범위 #246](https://github.com/woonyong-choi/totedb/issues/246))

기여는 팀 과제의 공동 결과, 개인 주도 엔진 구현, 과제 종료 뒤 개인 확장을 구분한다. 팀은 5인이며 병합 기여자는 4인이라는 출처 설명을 따른다. 팀 전체 구현을 개인 성과로 합산하지 않는다. 과거 문서의 짧은 해시·커밋 수·변경 줄 수도 현재 이력의 통계로 다시 쓰지 않는다. ([기여와 출처](attribution.md))

| 근거의 종류 | 이 문서에서 뜻하는 것 | 뜻하지 않는 것 |
|---|---|---|
| 당시 공개 설계·댓글 | 확인한 시점의 요구와 선택 이유 | 더 이른 착수 동기의 증명 |
| 구현 커밋·시험 기록 | 그 버전의 구현과 보고된 검사 결과 | 이번 재실행 또는 모든 입력의 증명 |
| 현재 코드 해석 | 지금 보이는 소유권·호출·I/O 구조 | 최초 작성자의 미기록 의도 |
| 현재 회고·후속 이슈 | 재확인한 목적과 다음 판정 조건 | 과거 운영 장애·OOM의 발생 기록 |

### 시작부터 현재까지의 흐름

| 시점 | 확인되는 변화 | 다음 질문으로 이어진 이유 |
|---|---|---|
| 2026-04-16~23 | 팀 과제, 선행 디스크 MiniDB 재사용, SQL API 서버·heap·B+Tree·락 | 저장 엔진과 네트워크·병렬 처리의 경계 확보 |
| 2026-08-02 이후 | 개인 범위 조회 확장과 구조 설명 | 기존 색인을 SQL 접근 경로로 연결 |
| 2026-10-06~10 | PG18 자체 전체 호환 목표와 현행 결함 감사 | SQL 문법 외 결과·거래·저장·운영까지 목표 확대 |
| 2026-10-14 | #81 가변 값 계획, #139 독립 DB·선택형 ACK 결정, #140 소비 계약 | PG 전체 호환 대신 메모리·실패·사용 경계 재설정 |
| 2026-10-15 | 가변 값·overflow·catalog·파일 envelope·결과·새 CRUD, 거래 코어·예산·session·WAL 연결, ACK·C/Python·호환층 | 기초 모듈의 통과를 실제 SQL·큐 사용 계약으로 연결 |
| 2026-10-15 후반~16 초반 | 국소 디렉터리·지속형 색인·여유 공간, 검증된 읽기 캐시, WAL 철회·빈 목록 영속화·조건부 1-fsync·CRC32C | 전체 재작성과 중복 보존 경로 축소, 회수 안전성 유지 |
| 2026-10-16~17 | SQL 확장·해시 집계·결과 타입·큐 색인·session 통합, 구 엔진 제거, varint·ToteDB 명칭·다중 프로세스와 읽기 잠금 분리, 설치 배포 | 독립 embedded DB의 기능·수명·지원 범위 정리 |
| 2026-10-17~19 | 공식 YCSB → TEXT 범위 개선 → 독립 연결 큐 → H2/H3 후보 철회 | 유리한 내부 변경과 업무 처리량 개선을 구분 |
| 2026-10-19 이후 | 매크로 계측·빌드 혼입 방지와 0.2.0 → 420표본 → 실제 DB·큐 280표본 → 소스 대조·캐시 목표 | 일반 성능 우위 대신 남은 문장·페이지 비용과 원래 요구의 미충족 확인 |

위 표는 반영 흐름을 요약한다. 아래 장은 저장·SQL·큐·측정의 연결을 설명하려고 관련 변경을 묶는다. 특히 #139는 새 엔진·WAL 구현보다 먼저이며, WAL 철회 뒤에 제품 목적을 처음 정한 것이 아니다. #22의 10월 10일 감사 문구를 이슈 생성일인 10월 6일의 관측으로 앞당기지 않는다. ([#22](https://github.com/woonyong-choi/totedb/issues/22), [#139](https://github.com/woonyong-choi/totedb/issues/139), [가변 값 사전 계획](https://github.com/woonyong-choi/totedb/issues/81#issuecomment-6172061168))

## 1. 팀 원형에서 독립 DB와 선택형 ACK로 바뀐 목적

4월의 일은 이미 있던 디스크 엔진 위에 HTTP와 동시 실행을 붙이는 팀 과제였다. 10월의 제품은 그 서버를 계속 키운 결과가 아니라 저장·실행 계약을 새로 만들고 서버를 제거한 로컬 DB다. 현재 ACK 요구를 처음부터 예정한 설계로 읽으면 언어·락·저장 구조의 이유까지 잘못 연결한다.

### D01 선행 MiniDB 재사용

문제와 대안은 새 과제에서 DB까지 처음부터 만들지, 앞 주차 디스크 엔진을 재사용할지였다. 2026-04-21 설명은 `wk07_6` 재사용으로 소켓과 병렬 처리에 집중한다고 적었다. 최초 저장소 기록은 4월 16일이다.

선택은 선행 엔진 재사용이었다. ACK 편의나 SQLite 교체를 이때의 이유로 소급하지 않는다. 당시 문서의 73/73 검사는 선행 엔진 실행 보고이며 이번 검사가 아니다.

현재 상태는 역사적 출처다. 선행 코드의 정확한 최초 착수일과 개별 최초 작성 범위는 미확인이다. ([시작 커밋](https://github.com/woonyong-choi/totedb/commit/adb60342dda22e0214c3304b082614a7baa44a96), [재사용 설명](https://github.com/woonyong-choi/totedb/commit/c0b5440256c6881a12e3547343c122f97b65f74e), [기여와 출처](attribution.md))

### D02 heap·B+Tree·pager의 책임 분리

초기 구조는 행을 heap에 두고 B+Tree에 `id → (page, slot)`을 저장했다. 자식 주소는 메모리 pointer가 아니라 page ID다. pager는 I/O·cache·pin·dirty 규칙을 모았다. 초기 페이지는 OS 크기를 사용했고 실패 시 4096바이트였다.

8월 README는 hash보다 순서·범위 순회에 맞는 B+Tree, 데이터 이동을 줄이는 slotted heap을 이유로 설명한다. 이는 후속 설명이다. 4월 구현 전에 hash·LSM과 비교한 기록이나 C를 다른 언어보다 고른 이유는 미확인이다.

초기 통합은 160 assertions를 보고했고, 8월 224/224 기록은 sanitizer 없는 검사다. macOS ASan 초기화 정지는 통과가 아니다. 당시 WAL·일반 거래 격리는 없었고 구 엔진은 뒤에 제거했다. ([초기 구현](https://github.com/woonyong-choi/totedb/commit/6a7a648466b91763013c6500ba7d565ebc6b0e66), [8월 설계 설명](https://github.com/woonyong-choi/totedb/blob/c78e5f8/README.md))

### D03 서버와 저장 실행의 경계

네트워크 처리와 저장 실행을 한 덩어리로 만들지 않는 것이 초기 협업 문제였다. 설계는 서버가 요청·응답을, 엔진이 parse·plan·execute·저장·결과를 담당하도록 나눴다. `db_execute`가 두 담당의 계약이었다.

초기 REPL과 HTTP `POST /query`는 plain text 결과를 받았다. UPDATE·비교·COUNT·ORDER BY·LIMIT·DROP을 추가했지만 자동 id와 일부 INT·BIGINT·VARCHAR, 단일 테이블 범위였다. 당시 C11·GCC·Make·pthread·Dev Container 사용은 확인되나 도구별 경쟁 대안의 선정 이유는 미확인이다.

Step1 63 assertions 기록은 그 부분집합의 근거다. 현재 서버·REPL은 없으며 외부 경계는 C API와 session이 담당한다. 초기 기능이 새 엔진에 자동 승계된 것은 아니다. ([경계 설계](https://github.com/woonyong-choi/totedb/commit/c0b5440256c6881a12e3547343c122f97b65f74e), [서버 연결](https://github.com/woonyong-choi/totedb/commit/37d7c6d34182f2f6687d3647ec0b8b8fbcc94c18), [초기 사용 범위](https://github.com/woonyong-choi/totedb/commit/bd09a1a4ff078091e131410edf600b63fe779e57))

### D04 전역 보호에서 행·범위 락과 연결별 thread로 확장

초기 대안은 global mutex, 엔진 RWLock와 pager mutex, 처음부터 세밀한 락이었다. SELECT도 cache metadata를 바꾸므로 엔진 read lock만으로는 부족했다. 먼저 엔진 RWLock·recursive pager mutex와 행 S/X Strict 2PL을 연결했다.

이후 heap latch·B+Tree latch coupling으로 페이지를 보호하고 range gap lock으로 범위 조회 중 phantom insert를 막았다. keepalive 연결이 고정 pool worker를 점유하는 대기 문제에는 연결별 thread와 최대 128연결을 채택했다. 지속 reader와 유휴 연결 문제에는 writer 우선 및 30초 timeout을 추가했다.

행 SS/SX/XX·timeout·다른 행·4thread INSERT, 대기 writer 앞에 후행 reader가 끼지 않는 회귀 검사가 기록돼 있다. 이 Strict 2PL은 이미 여러 SQL 문장 거래와 WAL을 지원했다는 뜻이 아니다. 현재 단일 writer 엔진에서는 구 락 구조를 유지하지 않는다. ([초기 락](https://github.com/woonyong-choi/totedb/commit/bcc9252c9237388d5aaccd74f43b21735801905d), [세밀화](https://github.com/woonyong-choi/totedb/commit/4a2d5091e51f7b78857162af8756a84e54619269), [기아·연결 수정](https://github.com/woonyong-choi/totedb/commit/00c10bdf5252cd45a8d28a048efbfccbd74b4c86))

### D05 id 범위 조건을 leaf 구간 순회로 연결

개인 확장에서는 id 조건도 전체 heap을 읽을지 기존 B+Tree leaf 구간을 사용할지가 문제였다. 2026-08-02 변경은 `INDEX_RANGE`, BETWEEN과 부등호 경로를 추가했다.

선택의 구조적 효과는 범위에 해당하는 색인 경로를 SQL이 사용한다는 것이다. 당시 운영 지연이나 절대 성능 개선을 이 커밋만으로 덧붙이지 않는다. 후속 property·범위 검사는 별도 시점이다.

현재 상태는 구 엔진의 역사적 확장이다. 새 엔진의 INTEGER·TEXT 색인과 10월 TEXT 범위 개선은 별도 구현·검증이다. ([범위 경로](https://github.com/woonyong-choi/totedb/commit/233f28e6fc0920be835332f48ab30e23f671b04e), [후속 검사](https://github.com/woonyong-choi/totedb/commit/6c8e30de3f740eef9d732f151b39e17db83bb7c6))

### D06 PG18 전체 자체 호환 목표의 채택과 종료

10월의 초기 대안은 SQL 부분집합 유지, PG 서버로 실행 위임, 자체 엔진의 PG18 전체 호환이었다. 당시 공개 기록은 자체 구현을 선택하고 결과·오류·타입·거래·재시작 효과까지 비교 대상으로 넓혔다. #22는 접속·운영·복제·물리 형식·확장 ABI까지 포함했다.

이 선택은 호환 기준과 백로그였지 달성 결과가 아니다. 일부 PG 차등 검사 통과로 전체 호환을 표시하지 않는 것이 당시 원칙이었다. 더 작은 독립 DB를 선택하는 대안은 이후 #139에서 공식 목표가 됐다.

현재 PG 전체 호환 목표는 대체됐다. #22 생성은 2026-10-06 09:41:16Z, 종료는 10월 14일이며, 그 사이 본문 감사가 추가됐다. 종료는 PG 전체 구현 완료를 뜻하지 않는다. ([목표 문서](https://github.com/woonyong-choi/totedb/commit/b6af082c60c8948eb28b9fbe87c6ca0bad68e728), [#1](https://github.com/woonyong-choi/totedb/issues/1), [#22](https://github.com/woonyong-choi/totedb/issues/22), [#139](https://github.com/woonyong-choi/totedb/issues/139))

### D07 독립 DB·선택형 ACK와 남아 있는 새 엔진의 설명 공백

#139는 2026-10-14 15:36:12Z에 독립 DB와 선택형 ACK를 공식 경계로 정했다. 15:38:52Z의 #140는 persist-queue 1.1.0 호출을 첫 소비 계약으로 연결했다. 메모리 최적화 엔진과 ACK 도구는 별도 목표이며, 코어가 큐를 의존하지 않는다.

현재 요구는 SQL 단독·큐 단독·호출자 거래 결합의 선택권이다. 무제한 메모리나 내구성 생략으로 목표를 달성하지 않는다. ACK 자체는 기존 도구에도 있으므로 차별점이 아니다. 현재 회고의 핵심은 필요한 ACK 의미·사용성·반복 변경 비용이다.

직접 SQLite 위에 bounded 조회·lease·fence·업무 거래 결합을 구현하는 대안도 성립한다. 공개 근거는 기존 조합의 제약과 DB 필요성을 설명하지만, 새 엔진만이 해법인 이유나 그 편익이 구현·운영 비용을 넘는지는 증명하지 않는다. 이 공백은 보류한다. ([#139](https://github.com/woonyong-choi/totedb/issues/139), [#140](https://github.com/woonyong-choi/totedb/issues/140), [현재 요구 #239](https://github.com/woonyong-choi/totedb/issues/239))

## 2. 고정 값·파일에서 가변 값과 실패를 다루는 저장 구조로 전환

새 저장 구조가 푸는 문제는 단순히 VARCHAR 폭을 늘리는 일이 아니었다. SQL의 문자 수, 메모리 소유권, 파일 페이지 주소, 거래 게시 시점을 분리해야 했다. 고정 물리 페이지는 유지하되 가변 값과 큰 값을 다른 규칙으로 담고, 실패한 변경을 공개 상태와 섞지 않는 방향으로 바뀌었다.

### D08 조용한 문자열 절단을 길이·slot·overflow로 대체

초기 `VARCHAR(32)`는 종단문자 때문에 최대 31바이트를 저장했고 초과 입력을 오류 없이 잘랐다. 초기 예시의 전체 행은 자동 id 8바이트, name 32바이트, age 4바이트로 44바이트다. name과 age만의 36바이트를 전체 행 크기로 쓰지 않는다.

대안은 고정 배열 확대 또는 가변 codec·안정 slot·overflow였다. 10월 14일 계획은 후자를 선택하고 PG TOAST 물리 호환은 요구하지 않았다. 큰 값은 별도 페이지 사슬에 저장한다. codec 추가, overflow 연결, 교차 사슬 접합 거절은 각각 다른 구현 단계다.

NULL·빈 값·UTF-8·15~20000바이트 BLOB·재개방, 성장·축소·삭제·rollback·crash 회계가 통합 기록에 있다. slot은 압축 후에도 같은 번호를 유지하지만 삭제 번호는 재사용하지 않는다. 다른 페이지로 이동한 행은 새 locator와 색인 갱신이 필요하다. 유지하되 장기 수명주기 성능은 별도 미검증이다. ([초기 절단 코드](https://github.com/woonyong-choi/totedb/blob/6a7a648/src/storage/schema.c), [사전 선택](https://github.com/woonyong-choi/totedb/issues/81#issuecomment-6172061168), [overflow](https://github.com/woonyong-choi/totedb/commit/639924e), [통합 결과](https://github.com/woonyong-choi/totedb/issues/81#issuecomment-6215736872))

### D09 값의 소유권과 SQL 문자열 제약 분리

입력 buffer를 빌리는 값과 모든 계층의 고정 배열을 유지하면 수명과 길이 제약이 함께 묶인다. 선택은 owned value와 borrowed view를 나누고 NULL·int64·REAL·길이 기반 TEXT/BLOB을 표현하는 것이었다.

문자열 저장 byte 수와 `VARCHAR(n)`의 문자 수 제약도 분리했다. TEXT에는 선언 폭이 필요하지 않다. VARCHAR 폭은 페이지 크기나 고정 저장 공간이 아니며 넘는 값을 조용히 자르지 않는다. SQLite 동적 affinity 전체 복제는 선택하지 않았다.

정수 경계·REAL 왕복·내장 NUL·폭 초과·잘못된 폭 거절을 검사했다. 첫 비교 규칙은 binary이며 locale·ICU 전체 의미는 보장하지 않는다. 기초 값 모듈 통과와 후속 SQL 연결을 구분한 채 현재 유지한다. ([소유권 사전 계획](https://github.com/woonyong-choi/totedb/issues/38#issuecomment-6172935856), [구현](https://github.com/woonyong-choi/totedb/commit/b4f8653), [문자열 계약 #87](https://github.com/woonyong-choi/totedb/issues/87), [폭 검사](https://github.com/woonyong-choi/totedb/commit/4f317cb))

### D10 이름 없는 전역 header에서 테이블별 catalog로 전환

초기 파일은 이름을 검증하지 않아 `DROP TABLE other`가 실제 테이블을 지울 수 있었다. 첫 대응은 단일 이름 저장과 비교였다. 여러 테이블 요구에는 global header를 계속 덮는 대신 stable table ID와 테이블별 schema·행 수·identity·heap/index 위치를 가진 catalog를 선택했다.

공통 allocator와 table handle을 나눠 중간 생성 실패가 기존 테이블을 바꾸지 않게 했다. metadata, page_file 영속화, heap·SQL 연결을 차례로 추가했다. 최초 새 CRUD의 PK 중복 검사는 아직 scan이었다.

서로 다른 schema·같은 PK의 두 테이블과 SQLite CLI 재시작 대조를 추가했다. identity는 테이블별 counter이며 큰 명시 id 이후 반복 증가 제한은 후속 수정으로 없앴다. 유지한다. CLI 부재 시 SKIPPED/77은 성공으로 합산하지 않는다. ([선행 문제 #17](https://github.com/woonyong-choi/totedb/issues/17), [catalog](https://github.com/woonyong-choi/totedb/commit/0ee4356), [SQL 연결](https://github.com/woonyong-choi/totedb/commit/52916b1), [대조](https://github.com/woonyong-choi/totedb/commit/8546d76), [identity 수정](https://github.com/woonyong-choi/totedb/commit/b2c23d7))

### D11 OS 독립 envelope와 두 제어 페이지

host packed 구조체와 OS 페이지 크기에 의존하는 형식을 계속 쓸지 명시적 파일 계약을 만들지가 문제였다. 새 형식은 little-endian, 버전·종류·길이·page ID·table ID·checksum을 기록한다. 물리 페이지 8192바이트는 envelope 32바이트와 본문 8160바이트로 나뉜다.

제어 페이지 0과 1은 세대를 교대로 게시한다. 새 파일은 partial 파일에 만든 뒤 link로 게시해 기존 목적지를 덮지 않는다. 8192가 4096·16384보다 최적이라는 비교 실험은 없다. 확인된 이유는 OS와 독립된 형식 계약이다.

고정 fixture 왕복·알 수 없는 버전·잘못된 길이·불완전 DB 거절과 원본 보존을 검사했다. checksum은 torn 탐지 수단이지 lost write 전체 탐지 수단이 아니다. 초기 명시적 legacy export/import는 뒤에 제거했으므로 현재 지원으로 소개하지 않는다. ([사전 계획](https://github.com/woonyong-choi/totedb/issues/77#issuecomment-6173835031), [형식 구현](https://github.com/woonyong-choi/totedb/commit/a289cf5), [legacy 제거](https://github.com/woonyong-choi/totedb/commit/dccfa52), [현재 계약](../include/storage/page_file.h#L11-L30))

### D12 I/O 오류를 성공으로 숨기지 않는 결과 구분

구 pager에는 read·eviction·flush·fsync 반환을 무시하거나 dirty를 버리는 경로가 있었다. 새 저장 모듈 완성을 기다리는 대안 대신 exact I/O와 상위 오류 전파를 먼저 고쳤다. 이는 소스 결함과 주입 시험의 기록이며 실제 운영 데이터 유실 사건의 기록은 아니다.

현재 게시 결과는 `APPLIED`, `NOT_APPLIED`, `UNKNOWN`으로 나뉜다. 확정 미적용과 불확정을 같은 rollback 성공으로 알리지 않는다. 제어 쓰기 시도 뒤 실패처럼 결과가 불확정이면 핸들을 poison해 후속 쓰기를 막는다. 성공 sync 뒤에만 durable 성공을 반환한다.

short I/O·EINTR·0-byte·n번째 I/O/할당 실패·강제 종료를 검사한다. 안전성 문서의 crash·fault 건수는 해당 시험군의 보고값이다. 실제 power-cut·장치 lost write·network FS는 미검증이며 모든 저장장치 고장의 증명이 아니다. 현재 유지한다. ([감사 #78](https://github.com/woonyong-choi/totedb/issues/78), [오류 전파](https://github.com/woonyong-choi/totedb/commit/3cc2e72), [불확정 구분](https://github.com/woonyong-choi/totedb/commit/de3d869), [안전성 결과와 한계](safety.md))

### D13 표 문자열 대신 타입·길이·수명이 있는 결과

프로그램이 사람용 표 문자열을 다시 파싱하면 NULL·빈 값·타입·수명을 잃는다. 대안은 출력 형식 보강 또는 공통 결과 계약이었다. 선택은 rows·command·error 결과와 열 타입·nullable·값 길이, `next/release`의 정확한 한 번 종료다.

초기 결과 모듈은 DB·Python이 공유할 계약이며 PG wire OID 전체 호환은 제외했다. 후속 C/Python 연결에서 LEFT JOIN nullable과 계산 열 타입까지 전달했다. 모듈 구현만으로 모든 소비 경로 통합이 끝났다고 표시하지 않았다.

NULL·빈 값·내장 NUL·36만바이트 한글·30만바이트 BLOB·int64·LimitError 왕복과 디코딩 실패 해제 검사가 기록돼 있다. 결과를 받은 호출자가 마지막 해제 책임을 지는 계약으로 유지한다. ([결과 모듈](https://github.com/woonyong-choi/totedb/commit/3dafaa9a4f2ad3d13e3a4da9bba698ee34d0d7d9), [소비 경로 완결](https://github.com/woonyong-choi/totedb/commit/099b3143e359e844bf2ab668362a792921411bc2), [#64](https://github.com/woonyong-choi/totedb/issues/64))

### D14 입력 전체를 확인한 뒤 실행

잘린 SQL이나 후행 미지원 절을 일부만 실행하는 것은 구문 지원 부족과 다른 결함이다. 선택은 길이 기반 lexer·parser로 전체 입력, UTF-8·따옴표·주석·문장 수·binding 개수를 실행 전에 확인하는 것이었다.

값은 SQL 문자열에 재조립하지 않고 binding한다. 초기 새 parser는 소유 AST를 만들며 당시 미지원 ORDER BY·JOIN을 명시적으로 거절했다. 뒤에 추가한 문법을 최초 parser의 지원으로 소급하지 않는다.

긴 문자열·불완전 입력 무변경·placeholder 경계 검사가 기록돼 있다. primitive 추가 시점과 실행기 연결 시점을 구분하며 현재 입력 경계를 유지한다. parser가 binding 값을 복제한다는 사실은 D51의 캐시 제약으로 이어진다. ([초기 차단](https://github.com/woonyong-choi/totedb/commit/dc3f200dc329116eba478b6a79973b25dea7d16f), [lexer](https://github.com/woonyong-choi/totedb/commit/9aa79feb), [parser](https://github.com/woonyong-choi/totedb/commit/d42a6415), [#37](https://github.com/woonyong-choi/totedb/issues/37))

### D15 거래 상태 코어와 실제 WAL 복구 연결

여러 문장의 변경을 한 번에 확정하고 승인 데이터를 재시작 뒤 보존하는 것이 요구였다. 첫 거래 코어는 단일 writer·세대별 거래 ID·timeout·취소·세 가지 적용 결과와 backend 보증 상한을 다뤘다. 가짜 backend 검사를 실제 DB 내구성으로 세지 않았다.

독립 WAL은 CRC·LSN·거래 상태와 replay 모드를 제공했다. 이후 SQL과 binding을 DATA로 기록하고 WAL COMMIT fsync 뒤 applied LSN과 COW 상태를 게시하도록 연결했다. 재시작은 확정된 거래를 순서대로 재실행했다. 완전 길이 checksum 오류는 잘린 꼬리로 무시하지 않았다.

독립 WAL 271 checks·crash 8 checks는 DB 연결 전 결과다. 실제 연결은 별도 커밋이다. 로그 크기에 비례하는 열기 비용을 당시 한계로 적었고, 현재 WAL 선택은 D21에서 철회했다. 외부 HTTP·파일 부작용의 exactly-once는 이때도 보장하지 않았다. ([코어](https://github.com/woonyong-choi/totedb/commit/d1433c4), [사전 WAL 판단](https://github.com/woonyong-choi/totedb/issues/9#issuecomment-6177521322), [독립 WAL](https://github.com/woonyong-choi/totedb/commit/783593c), [실제 연결](https://github.com/woonyong-choi/totedb/commit/c57da9d))

### D16 할당 전 예산과 반환 결과의 수명

메모리 목표에는 공용 cache·여러 session·반환 결과·realloc의 old+new peak가 모두 들어간다. 사후 합산 대신 shared·session·query 예산을 할당 전에 함께 예약하고, 하나라도 부족하면 상태를 바꾸지 않는 방식을 선택했다.

열기 검사는 경로 stat 대신 잠근 동일 fd의 fstat를 사용한다. 결과가 caller에게 넘어가도 마지막 release까지 session/shared 몫이 남는다. 실제 free보다 quota를 먼저 돌려주던 경쟁은 결과·session·heap에서 각각 수정했다. 숨은 정렬 scratch 대신 제자리 heapsort를 썼다.

allocator audit·n번째 실패·동시 release/close에서 `live <= ledger peak <= limit`를 검사했다. 단위는 요청 byte이며 allocator overhead나 전체 RSS 상한이 아니다. 취소도 협력형이며 게시 시작 후 취소 성공으로 위장하지 않는다. 예산 계약은 유지하되 SQLite 대비 메모리 우위는 미검증이다. ([사전 제안](https://github.com/woonyong-choi/totedb/issues/79#issuecomment-6175353530), [session](https://github.com/woonyong-choi/totedb/commit/25c985c), [해제 순서 수정](https://github.com/woonyong-choi/totedb/commit/faa1c24), [audit](https://github.com/woonyong-choi/totedb/commit/1b2c8d7))

### D17 두 세대 지연 회수와 fresh 페이지 즉시 재사용

한 번의 commit 뒤 해제 페이지를 재사용하면 fallback 제어 세대가 아직 가리키는 내용을 덮을 수 있었다. 선택은 기존 게시 페이지를 pending, 다음 성공 후 aging, 그다음 성공 후 reusable로 옮기는 두 commit 지연이었다.

후속 10월 16일 변경은 거래 중 새로 할당한 fresh 페이지를 구분했다. 두 제어 세대가 참조한 적 없는 fresh는 rollback·거래 내 대체에서 즉시 재사용한다. reusable·aging 목록을 영속화해 재열기 누수도 막았다. 모든 페이지의 즉시 재사용도, 모든 페이지의 두 commit 대기도 아니다.

최신 control 손상 fallback·반복 rollback·매번 reopen의 회계 검사가 기록돼 있다. UNKNOWN에서는 참조 세대를 확정할 수 없어 임의 해제하지 않는다. 기능적 회수 검사는 장기 일정 backlog의 DELETE 처리량·파일 성장 우위를 입증하지 않는다. 현재 유지한다. ([fallback 수정](https://github.com/woonyong-choi/totedb/commit/e6c3d01), [fresh·목록 영속화](https://github.com/woonyong-choi/totedb/commit/38fb29d), [현재 release 규칙](../include/storage/page_file.h#L183-L209))

### D18 디렉터리 전체 재작성 대신 국소 COW

새 heap의 디렉터리 사슬은 한 행을 바꿔도 전체를 다시 썼다. #159의 수정 전 관측은 디렉터리 1·4·10개에서 쓰기도 1·4·10개로 증가했다. 대안은 현 구조 유지 또는 root·leaf로 나눠 변경 부분만 쓰는 방식이었다.

선택은 바뀐 leaf와 root만 COW(copy-on-write, 기존 페이지를 덮지 않고 새 페이지에 변경을 쓰는 방식)로 만들고 다른 leaf는 공유하는 구조였다. 변경 범위 밖 디렉터리까지 다시 쓰는 일을 줄이는지 확인했다.

디렉터리 쓰기·결합 재개방 검사를 추가했다. 이는 특정 경로 감소의 근거이지 DB 전체 성능 우위의 증명이 아니다. 현재 유지하며 남은 root·metadata·문장별 완료 비용은 D50에서 따로 다룬다. ([사전 가설](https://github.com/woonyong-choi/totedb/issues/159#issuecomment-6180524590), [수정 전 재현](https://github.com/woonyong-choi/totedb/issues/159#issuecomment-6180685127), [구현](https://github.com/woonyong-choi/totedb/commit/41ada6a))

### D19 새 지속형 색인과 여유 공간 힌트

새 CRUD는 PK 중복·동등 조회를 scan했다. 구 B+Tree 존재만으로 새 파일 위 색인 지원을 주장할 수 없었다. 새 INTEGER COW B+Tree와 heap root를 같은 catalog commit에 게시하고 조회 때 키를 재확인하는 경로를 선택했다.

TEXT 기본 키와 일반·고유 보조 색인은 후속 확장이다. 여유 공간은 디렉터리의 연속 여유 byte 힌트로 best-fit 후보를 고른 뒤 실제 페이지를 재검증한다. 삭제 데이터 공간 재사용과 삭제 slot 번호 재사용을 구분하며 후자는 금지한다.

이동·삭제·재시작·실패 뒤 heap/index 일치 검사가 있다. 당시 10만행 3회 성능 완료 조건 미충족 기록도 남아 있다. 힌트 배열 scan CPU는 잔존한다. 기능은 유지하되 모든 크기의 성능 검증 완료로 확대하지 않는다. ([#158 사전 근거](https://github.com/woonyong-choi/totedb/issues/158#issuecomment-6180460775), [INTEGER](https://github.com/woonyong-choi/totedb/commit/f1d5b64), [TEXT](https://github.com/woonyong-choi/totedb/commit/1aefbf0), [공간 힌트](https://github.com/woonyong-choi/totedb/commit/fff24a5), [당시 한계](https://github.com/woonyong-choi/totedb/issues/158#issuecomment-6181015472))

### D20 검증된 읽기 페이지 cache

반복 pread·checksum을 줄이되 무결성 검사를 없애지 않는 것이 문제였다. 선택은 핸들당 256 CLOCK frame에 이미 검증한 본문만 보관하는 읽기 cache였다. 구 pager의 dirty writeback·WAL LSN 계획과 다르다.

pwrite 성공 뒤 cache를 갱신하고 실패하면 무효화한다. cache 할당 실패는 cache 없이 계속 진행하며 상한을 admission에 계상한다. 다른 프로세스의 세대 변경에는 후속 cache flush가 필요해졌다.

hit의 pread 0, 256개 초과 eviction 뒤 cache-off와 같은 byte, 실패 write 뒤 디스크 값과 손상 거절을 검사했다. 현재 유지한다. 이것은 SQL 문장·준비 결과 cache의 구현 근거가 아니다. ([구현](https://github.com/woonyong-choi/totedb/commit/5ff636a), [범위·검증 #83](https://github.com/woonyong-choi/totedb/issues/83), [cache 계약](../include/storage/page_file.h#L80-L96))

### D21 문장 WAL을 철회하고 COW 게시로 보존 계약 통합

WAL은 없었던 기능이 아니라 D15에서 실제 연결한 구현이다. 10월 16일의 대안은 SQL 재실행 WAL과 COW 게시를 함께 유지하거나, COW의 보존 계약으로 통합하는 것이었다.

당시 명시 이유는 data fsync, control write, fsync가 원자성과 응답 뒤 보존을 제공해 문장 WAL이 해당 계약에 중복된다는 것이었다. 느리다는 추정이나 거래 내부 원자성만 필요하다는 설명으로 바꾸지 않는다. WAL·durable_pair와 관련 경로를 제거했다.

현재 `BEGIN DURABLE/VOLATILE`, WAL path·변환·정보, 큐·C/Python durability 선택은 없다. 달성 보증은 항상 DURABLE이다. 초기 #139의 두 모드 요구는 역사이며 현재 무내구 선택 모드로 소개하지 않는다. COW 게시 선택을 유지한다. ([제거 전 결정](https://github.com/woonyong-choi/totedb/issues/9#issuecomment-6205475634), [제거 구현](https://github.com/woonyong-choi/totedb/commit/d137130))

### D22 조건부 1-fsync와 두 번 동기화 경로 병존

WAL 철회와 동기화 횟수 감소는 별도 선택이다. 새 페이지만 쓰고 manifest가 256항목 이하일 때 data와 control·manifest를 쓴 뒤 fsync 한 번으로 끝내는 경로를 추가했다. open은 manifest의 page ID·checksum·파일 길이를 확인한다.

끝나지 않은 게시가 검출되면 이전 control을 선택한다. manifest 초과나 게시 페이지 덮어쓰기는 data fsync 뒤 control을 쓰고 다시 fsync하는 경로를 유지한다. 따라서 모든 commit이 항상 fsync 한 번이라는 설명은 틀리다.

짧은 파일·사라진 data write·첫 세대 미완료·양쪽 fsync 실패에서 적용 결과를 검사했다. 이는 프로세스 crash·I/O 모델 검사다. 실제 전원 차단과 모든 lost-write 조합의 동등 보증은 미검증이다. 현재 조건부 경로를 유지한다. ([후속 구현](https://github.com/woonyong-choi/totedb/commit/e3023e7), [현재 commit 계약](../include/storage/page_file.h#L221-L237), [안전성 한계](safety.md#검증하지-않은-것))

### D23 byte loop checksum을 CRC32C로 변경

8192바이트마다 FNV-1a byte loop를 도는 비용이 변경 대상이었다. 선택은 Castagnoli CRC32C이며 SSE4.2·ARMv8 지원 시 하드웨어, 아니면 표 구현을 쓴다. 파일 검증을 없애는 대안은 선택하지 않았다.

표준 입력의 `e3069283` 동일값을 확인하고 envelope·overflow checksum 형식을 함께 바꿨다. 옛 checksum 형식은 거절한다. 이는 구현 경로와 파일 계약의 변화다.

커밋의 CPU·시간은 국소 환경 보고이며 모든 SQL의 속도 보장이 아니다. 무결성 검사와 오류 전파를 유지한 채 현재 CRC32C를 사용한다. checksum만으로 거래 원자성이나 lost write 검증이 완성되지 않는다. ([선택·검증 커밋](https://github.com/woonyong-choi/totedb/commit/7d1aae7), [보장 조합](architecture.md#지켜야-할-규칙))

## 3. SQL의 폭보다 입력·결과·소유권 계약을 먼저 연결

새 CRUD 이후 SQL 기능은 10월 16일에 확장됐다. 이 과정은 SQLite의 모든 의미를 복제한 것이 아니다. 타입을 고정하는 결과와 의도된 차이, 미지원 조합, 예산 실패를 기능 설명에 함께 남겼다. 메모리 목표도 예산 장치 하나가 아니라 각 연산의 보관량을 바꾸는 선택으로 연결됐다.

### D24 AST와 고정 결과 타입의 식 실행

제한된 parser에 분기를 계속 붙이는 대신 우선순위 AST와 열번호로 컴파일한 식을 도입했다. ORDER BY·DISTINCT·LIMIT·OFFSET·별칭·복수행 INSERT·UPDATE식을 연결했다. 비교의 목표는 지원하는 결과 의미이지 문법 수용만이 아니었다.

결과 열 타입은 PG처럼 고정하고 3치 논리·넘침·0나누기 오류를 다룬다. SQLite affinity·LIKE·NULL 정렬 전체를 같은 의미로 만들지 않았다. 이 차이를 숨기지 않는 선택이다.

당시 3자 106건은 PASS 92·의도 DIFF 14로 기록됐다. 정렬 버퍼 예산과 LIMIT 조기 종료도 검사했다. 현재 유지하며 이 건수를 이후 전체 호환 비율로 재사용하지 않는다. ([구현·비교](https://github.com/woonyong-choi/totedb/commit/daf2c8a6), [#7](https://github.com/woonyong-choi/totedb/issues/7), [#160](https://github.com/woonyong-choi/totedb/issues/160))

### D25 전체 입력 정렬 집계에서 그룹별 해시 누적으로 변경

첫 COUNT·SUM·AVG·MIN·MAX·GROUP BY·HAVING은 전체 입력을 모아 정렬했다. 메모리가 입력 행 수에 비례하는 문제가 있었다. 대안은 그 구조 유지 또는 그룹별 누적이었다.

같은 날 후속 변경은 그룹별 해시 누적으로 바꿔 일반 집계 메모리를 그룹 수에 비례하게 했다. DISTINCT 집계만 값 수집·정렬을 남겼다. 이 이유는 현재 추정이 아니라 변경 기록에 명시돼 있다.

입력 400→4000행에서 최대 계상이 일정한 시험을 추가했다. 이전 구현의 203KB→1.67MB는 그 시험에서 실패한 비교다. NULL·1과 1.0 해시 의미·예산·3자 검사를 포함해 유지한다. 일반 SQL 처리량 우위는 별개다. ([최초 집계](https://github.com/woonyong-choi/totedb/commit/d1b42fd6), [해시 누적 변경](https://github.com/woonyong-choi/totedb/commit/baeb23f6), [#42](https://github.com/woonyong-choi/totedb/issues/42))

### D26 JOIN의 조건 배치와 색인 탐침

다중 테이블 요구에는 nested loop와 사용할 수 있는 색인 탐침을 연결했다. AND 조건은 필요한 열이 준비된 단계에 배정한다. `열 = 앞 단계 식`이면 색인을 탐침하고 LEFT 미일치는 NULL을 만든다.

INNER·LEFT·CROSS 뒤 첫 결합의 RIGHT를 방향을 바꾼 LEFT 실행으로 확장했다. hash·merge JOIN의 최초 탈락 이유는 미확인이다. 현재 구조가 있다는 이유로 당시 모든 알고리즘을 비교했다고 쓰지 않는다.

JOIN 29·RIGHT 7 비교와 후속 다대다·빈 테이블·색인/scan·예산 검사가 기록돼 있다. FULL·NATURAL·USING·괄호 JOIN 전체 지원은 아니다. 제한을 유지하며 다른 JOIN 알고리즘 채택은 미결이다. ([JOIN](https://github.com/woonyong-choi/totedb/commit/d1973a03), [RIGHT](https://github.com/woonyong-choi/totedb/commit/481e0422), [#43](https://github.com/woonyong-choi/totedb/issues/43))

### D27 서브쿼리의 보관과 재실행 범위 제한

비상관 scalar·EXISTS·IN·FROM subquery와 비재귀 WITH는 문장당 한 번 보관하고 IN은 이분탐색한다. 이후 외부 열 binding을 추가해 상관 서브쿼리를 실행하고 동일 외부값의 재실행을 생략했다.

선택은 모든 행에서 무조건 같은 안쪽 질의를 반복하는 비용을 줄이는 것이다. 안쪽 색인 탐침도 연결했지만 재컴파일 비용은 잔존한다고 기록했다. WITH·FROM과 집계 일부 상관 범위는 제한한다.

NULL·빈 입력·다행 scalar 오류·이름 충돌·예산 실패 무변경·중첩 상관 UPDATE/DELETE 비교가 있다. 재귀·쓰기 CTE까지 지원한다는 뜻은 아니다. 현재 제한된 실행을 유지한다. ([비상관](https://github.com/woonyong-choi/totedb/commit/e8d52bdc), [상관](https://github.com/woonyong-choi/totedb/commit/43778dd3), [#162](https://github.com/woonyong-choi/totedb/issues/162))

### D28 게시 후 RETURNING과 명시적 의미 차이

DML 결과를 먼저 반환하면 나중 게시 실패를 성공 행으로 오해한다. RETURNING은 게시 성공 뒤에만 노출한다. DEFAULT·NOT NULL·ON CONFLICT와 집합 연산도 지원하되 의미가 다른 조합은 명시적으로 거절한다.

UNION·ALL·EXCEPT·INTERSECT에서 괄호 없는 혼합 INTERSECT는 SQLite와 PG 우선순위가 달라 거절했다. 편의를 위해 한 엔진의 의미를 숨겨 선택하지 않았다. ON CONFLICT UPDATE의 key 변경과 EXCEPT/INTERSECT ALL은 제한된다.

RETURNING 예산·실패 무부분결과, 열 수·타입 오류와 ON CONFLICT 22건의 PASS 20·DIFF 2를 기록했다. 현재 유지한다. 이 차등 결과는 SQL 전체 동등성의 증명이 아니다. ([RETURNING](https://github.com/woonyong-choi/totedb/commit/f954740f), [집합](https://github.com/woonyong-choi/totedb/commit/5308cd22), [DEFAULT](https://github.com/woonyong-choi/totedb/commit/68ba0cea), [충돌](https://github.com/woonyong-choi/totedb/commit/22f5ec76))

### D29 페이지 압축 대신 작은 행의 varint 인코딩

작은 정수·짧은 TEXT 7열 1만행이 273 reachable 페이지를 썼다. 한 거래 적재와 auto-commit 크기가 같고 free가 5장이어서 당시 원인은 COW 회수보다 행 인코딩으로 좁혔다. 대안은 페이지 압축 또는 행 header·정수 표현 축소였다.

페이지 압축은 COW·checksum와 겹치고 해제 비용을 더해 채택하지 않았다. version byte, 필드 수·코드 varint, 최소폭 정수, 길이 code를 선택했다. 비최소 표현·부족하거나 남는 byte·옛 LRC1은 거절한다.

같은 입력에서 reachable 273→97페이지, 파일 2.29→0.85MB를 기록했다. scan p50 약 2ms는 같았으므로 공간 감소를 지연 개선으로 바꾸지 않는다. 왕복·canonical 변이 검사를 유지하며 구 파일 자동 읽기와 페이지 압축은 지원하지 않는다. ([문제 #192](https://github.com/woonyong-choi/totedb/issues/192), [변경·효과·무효과](https://github.com/woonyong-choi/totedb/commit/8d1bb44))

### D30 평탄 C API·ctypes와 session 일원화

언어별로 내부 구조를 노출하는 대신 핸들·길이·byte의 C API를 두고 Python은 ctypes로 호출한다. 출력 한도, 같은 DB의 거래 여부, 반환 자원의 소유권을 검사한다. ctypes와 cffi·다른 언어 대안의 최초 비교 이유는 미확인이다.

10월 16일에는 DB와 큐를 같은 session 경로로 통합했다. ledger만 큐에 전달하면 session·pool 한도와 취소가 빠지므로 그 대안은 고르지 않았다. 업무 변경과 put, 로컬 효과와 ACK를 호출자 거래에 넣을 수 있다.

공통 정상·예산·취소·결과 보유, 실패 46지점·TSan·취소 변이 검사가 기록돼 있다. 외부 HTTP·파일까지 원자적으로 묶는 것은 아니다. 현재 C/Python을 유지하며 payload 복사와 FFI 상대 비용은 D50의 미계측 항목이다. ([C API](https://github.com/woonyong-choi/totedb/commit/fac7130d), [Python](https://github.com/woonyong-choi/totedb/commit/4b69fdc3), [session 통합](https://github.com/woonyong-choi/totedb/commit/97abc0a0), [거래 scope](../src/queue/ack_queue_sql.c))

## 4. ACK의 편의와 실패 의미를 분리하고 로컬 사용 경계를 정리

persist-queue 1.1.0에도 ACK·nack·ack_failed와 재개방 복구가 있다. 차이는 ACK의 존재가 아니라 lease·fence·호출자 거래·목록 소유권 같은 구체 계약이다. 원본 `SQLiteAckQueue`는 auto_commit=False를 True로 바꾸고 내부 connection에서 확정한다. 공개 API에 임의 업무 거래를 넘기는 계약은 없다. 별도 SQLite 구현이나 비공개 connection 조작까지 불가능하다는 뜻은 아니다. ([고정 원본](https://raw.githubusercontent.com/peter-wangxu/persist-queue/v1.1.0/persistqueue/sqlackqueue.py), [거래 wrapper 원본](https://raw.githubusercontent.com/peter-wangxu/persist-queue/v1.1.0/persistqueue/sqlbase.py), 2026-10-19 확인)

### D31 호환 호출과 native API 분리

대안은 소비 프로젝트 즉시 교체, 호환층, 새 native API였다. 선택은 persist-queue 1.1.0의 일부 호출 모양을 받는 호환층과 새 보장을 드러내는 native API의 병존이었다. 합성 데이터로 put·get·ack·nack·update·목록·정리·close를 확인했다.

호환층은 완전 대체가 아니다. 해당 프로세스 get의 fence가 있어야 ACK할 수 있고 stale·terminal update를 거절한다. 기본 lease 24시간, UTF-8 직렬화와 기본 payload 1MiB 한도, 정리 순서 차이도 있다. native 거래 결합은 compat put에 노출하지 않는다.

`queue()`는 반환 모양 때문에 여전히 payload 전체 목록을 복사한다. VACUUM 상당 파일 shrink는 없고 size·stats의 전역 scan 비용도 남는다. auto_resume은 다른 살아 있는 worker가 없는지 호출자가 판단해야 한다. 합성 계약 통과와 실제 소비 프로젝트의 완전 교체·메모리 개선은 구분하며 후자는 미검증이다. ([#140](https://github.com/woonyong-choi/totedb/issues/140), [호환층](../python/totedb/persistqueue_compat.py), [합성 시험](../tests/test_ack_queue_synthetic_contract.py))

### D32 bounded claim·lease·fence와 at-least-once

무제한 pending 읽기와 id-only ACK 대신 최대 1024개 bounded claim을 제공한다. lease는 처리권의 기한이고 fence는 claim마다 증가하는 번호다. 현재 id와 fence를 함께 확인해 새 worker가 다시 가져간 뒤 옛 worker의 변경을 거절한다.

전달 계약은 at-least-once다. 만료 시각이 지났다는 사실만으로 현재 fence가 사라지지는 않는다. 다른 worker가 재claim하기 전에는 현재 claim의 ACK·extend를 허용한다. 외부 부작용 중복을 이 번호만으로 없애지 않는다.

결정적 시계로 만료·재claim·stale ACK 거절·재시작·동시 claim을 검사했다. 만료 후보를 고르는 조회와 실제 상태 전이를 구분한다. 현재 유지하며 실측 정상 부하의 고정 시계는 이 실패 경로의 성능 검증이 아니다. ([최초 큐 구현](https://github.com/woonyong-choi/totedb/commit/42b1f970), [claim·extend 계약](../include/queue/ack_queue.h#L133-L159), [시험](../tests/test_ack_queue.c))

### D33 FAILED는 별도 DLQ가 아닌 논리 상태

무제한 재시도만 둘지 명시적 실패와 시도 한도를 둘지가 선택이었다. `fail` 또는 양의 `max_attempts` 도달 뒤 `nack/recover`에서 같은 테이블의 FAILED로 격리한다. reason을 보관해 실패를 살펴볼 수 있다. 기본 `max_attempts=0`은 무제한이다.

만료 조회는 기한이 `now` 이하인 후보를 고르는 단계다. claim은 시도를 소진한 LEASED를 제외하고 recover가 그 상태를 FAILED로 끝낸다. recover·release_leases는 호출자가 실행하며 background daemon이 없다. FAILED는 읽기와 include_failed purge가 가능하지만 전용 redrive API나 별도 DLQ queue는 없다.

같은 fence의 중복 ACK·fail과 이미 READY/FAILED인 중복 nack은 허용하고 다른 fence는 STALE로 거절한다. delay·3회 한도·만료 recover 검사가 있다. 현재 유지한다. ‘논리적 DLQ’는 현재 상태기계의 설명이지 별도 재처리 제품 구현의 주장이 아니다. ([실패·재시도 계약](../include/queue/ack_queue.h#L149-L186), [상태 전이 소스](../src/queue/ack_queue.c#L594-L900), [retry 시험](../tests/test_ack_queue.c#L478-L560))

### D34 enqueue 중복과 ACK 재시도 구분

응답 유실 뒤 등록을 다시 하면 무조건 새 작업을 만들지 판단할 수 있어야 한다. 같은 idempotency key와 같은 payload는 기존 id를, 다른 payload는 CONFLICT를 반환한다. key 없는 같은 payload는 별도 작업이다.

purge 뒤에도 기본 7일 동안 key를 보존하도록 확장했다. 길이·CRC32C·FNV64 요약으로 비교한다. ACK 재시도의 동일 fence 허용과 enqueue의 동일 key 판정은 다른 계약이다.

같은 key·다른 payload·key 없음·purge 뒤 보존을 검사했다. 요약 충돌 확률은 0이 아니며 외부 exactly-once를 뜻하지 않는다. 현재 유지하고 보존 기간을 포함한 장기 정리 비용은 별도 평가 대상으로 남긴다. ([보존 구현](https://github.com/woonyong-choi/totedb/commit/0ebd1c64), [공개 계약](../include/queue/ack_queue.h#L22-L26), [보존 시험](../tests/test_ack_queue.c#L330-L439))

### D35 metadata cursor로 반환량 제한

원본 `queue()`는 조건 없는 전체 행 읽기와 역직렬화 목록을 만든다. 전체 pending를 이 경로로 얻으면 payload 읽기·복사가 발생한다. 이것은 소스 비용의 근거이며 실제 OOM 발생이나 메모리 사용량의 측정치는 아니다.

native 목록은 `after_id·limit`으로 나누고 기본값은 payload를 복사하지 않는다. claim은 선택한 payload만 복원한다. cursor는 반환·보관량의 경계이며 모든 SQL의 상수 시간 실행 보장은 아니다.

3개씩 순회·payload NULL·선택 payload 복사 검사가 있다. stats의 전체 scan과 compat `queue()`의 전체 복사는 남는다. 현재 native API를 유지하지만 소비 경로의 고정 입력 전후 메모리 개선량은 미검증이다. ([원본 목록](https://raw.githubusercontent.com/peter-wangxu/persist-queue/v1.1.0/persistqueue/sqlackqueue.py), [native 목록 계약](../include/queue/ack_queue.h#L195-L216), [호환 목록](../python/totedb/persistqueue_compat.py#L159-L161), [목록 시험](../tests/test_ack_queue.c#L562-L617))

### D36 전용 저장 엔진 대신 SQL 색인으로 큐 검색

초기 상태 검색 scan을 줄이려고 큐가 저장 내부에 직접 접근하는 대안 대신 session SQL 경계를 유지했다. state·idempotency 색인, RETURNING, id순 비고유 색인을 적용했다.

claim·recover·purge는 상태 조건·id순·LIMIT으로 대상을 찾는다. ACK는 PK 한 행을 읽고 필요한 작업만 payload를 복원한다. stats 전체 scan은 별도다.

현재 구현과 시험은 이 경로의 근거다. 커밋만으로 큐 처리량 개선 배수를 만들지 않는다. 현재 유지하지만 같은 계약을 더 적은 SQL로 수행할지는 D50~D51의 후속 판단이다. ([상태·RETURNING](https://github.com/woonyong-choi/totedb/commit/508d7b01), [id순 색인](https://github.com/woonyong-choi/totedb/commit/2356cb64), [검색 경계](../include/queue/ack_queue.h#L41-L44))

### D37 구 서버·엔진 제거와 새 실행기 역할 분리

두 엔진이 병존하면 빌드·CI와 이슈 책임이 흐려졌다. 대안은 병존, 빌드만 분리, REPL·HTTP 이식, 삭제였다. 로컬 DB·큐 범위에 맞춰 구 구현을 제거하고 이력에 보존하는 선택을 했다.

별도로 약 5500줄 새 SQL 실행기를 10개 파일과 공유 header로 나눴다. 당시 이유는 기능 위치 파악과 다중 프로세스 변경 범위 분리였다. 동작 변경을 목표로 하지 않았다. 모든 세부 서식 변경을 이 보고서의 선택으로 합산하지 않는다.

전체·3자·thread 검사 통과 보고가 있다. 수학적 등가성 증명은 아니다. 현재 서버·REPL은 없고 역할 분리는 유지한다. 구 엔진 보존 ref는 이슈의 태그 목표와 완료 브랜치 표현이 달라 현재 원격 ref 자체는 미확인이다. ([제거 #186](https://github.com/woonyong-choi/totedb/issues/186), [제거 커밋](https://github.com/woonyong-choi/totedb/commit/86fc3835), [분리 #198](https://github.com/woonyong-choi/totedb/issues/198), [분리 커밋](https://github.com/woonyong-choi/totedb/commit/6362b397))

### D38 다중 프로세스에서도 한 거래의 writer만 허용

열린 writable pager가 파일을 계속 독점하면 부모·자식 worker 배치가 막힌다. 대안은 거래 동안만 writer 독점, 병렬 writer, 별도 서버였다. 선택은 거래 동안만 잠그고 잠금 뒤 세대를 확인해 cache·catalog·free-list를 갱신하는 방식이었다.

fork로 상속한 handle은 거절한다. 당시 병렬 writer는 행락·MVCC의 큰 변경에 비해 짧은 큐 쓰기의 기대 이득이 작다고 판단했고, 서버는 embedded 범위와 맞지 않았다. 여러 프로세스 사용과 여러 writer 동시 실행은 다르다.

4·16프로세스·fork 거절·SIGKILL·회계·갱신 누락 변이 검사가 기록돼 있다. 해당 시험의 유실·중복 0을 외부 exactly-once로 확대하지 않는다. 현재 단일 writer를 유지하고 병렬 writer는 #238에 남긴다. ([#199](https://github.com/woonyong-choi/totedb/issues/199), [구현](https://github.com/woonyong-choi/totedb/commit/67c42dd7), [후속 #238](https://github.com/woonyong-choi/totedb/issues/238))

### D39 쓰는 동안 읽기와 게시 순간의 배타를 분리

writer 거래 전체에서 조회를 막는 대신 실제 게시 순간만 reader와 배타가 되도록 바꿨다. #200 초기안의 reader 세대 registry·horizon 공유파일은 실제 채택 구조가 아니다.

실제 구현은 별도 `<db>-lock`의 writer 독점과 DB의 reader 공유·게시 배타 잠금을 나눴다. reader가 읽는 동안 게시 자체를 막아 두 세대 회수와 충돌하지 않게 했다. 새 reader 관문은 writer 굶주림을 줄인다.

초기 0.5초·시간 비율 시험은 CI 오판 때문에 reader 준비·게시 전 조회·writer 완료의 진행 확인으로 교체했다. 현재 유지한다. nonblocking은 쓰기 작업 중 조회 가능이라는 뜻이며 게시 순간이나 새 handle 열기의 무대기 보장은 아니다. ([#200](https://github.com/woonyong-choi/totedb/issues/200), [실제 잠금](https://github.com/woonyong-choi/totedb/commit/20d00656), [시험 교정](https://github.com/woonyong-choi/totedb/commit/99fc183c), [남은 경계 #216](https://github.com/woonyong-choi/totedb/issues/216))

### D40 공개 이름을 ToteDB로 통일

공개 이름과 접두사가 혼재해 설치·호출을 추적하기 어려웠다. alias를 남기는 대신 1.0 이전이라는 조건에서 ToteDB, `tote_*`, `totedb`, `libtotedb`로 통일했다. 이름의 풀이는 Transactional Outbox Table Engine이다.

이 이름이 제품을 outbox 전용으로 제한하지 않는다. 내부 역할을 가리키는 `ackq`까지 모두 바뀐 것도 아니다. 이슈의 내부 접두사 선택지와 실제 커밋의 역할명 보존을 구분한다.

공개 이름 변경과 검사·사용 예 갱신은 완료 기록이 있다. 현재 이름은 확정이며 후보 재탐색 대상이 아니다. 지원·성능 보장은 이름 변경으로 달라지지 않는다. ([#194](https://github.com/woonyong-choi/totedb/issues/194), [이름 변경](https://github.com/woonyong-choi/totedb/commit/c5dc16ae))

### D41 플랫폼 wheel과 검증된 배포 범위

source·PYTHONPATH만 제공하면 사용자가 compiler와 library 위치를 관리해야 했다. 대안은 sdist만, Python ABI별 wheel, ctypes용 플랫폼 wheel이었다. 일반 공유 library를 쓰므로 `py3-none-platform` wheel과 source 설치용 sdist를 선택했다.

게시 인증은 token 대신 Trusted Publishing을 쓴다. Linux x86_64·aarch64와 macOS arm64 wheel을 제공하며 Windows·macOS x86_64 wheel은 제외했다. Python은 3.9 이상이다. 그 밖 POSIX는 source build 범위다.

버전 일치·wheel/sdist 설치·동봉 library loader smoke·게시 완료 기록이 있다. 뒤의 0.2.0은 D47의 별도 배포다. 로컬 FS만 지원하며 NFS·동시 multiwriter·다른 언어 binding은 지원하지 않는다. 모든 지원환경을 이번에 재실행한 것은 아니다. ([#205](https://github.com/woonyong-choi/totedb/issues/205), [배포 구현](https://github.com/woonyong-choi/totedb/commit/a829971d), [배포 실행](https://github.com/woonyong-choi/totedb/actions/runs/38982672814), [현재 지원](../README.md))

## 5. 특정 병목 개선은 유지하고 업무 효과 없는 후보는 철회

실험은 같은 질문의 반복이 아니었다. 처음에는 공식 SQL 부하로 범위 조회 결함을 찾았고, 이후 독립 연결 큐·후보 전후 비교·배포 비용·실제 패키지 비교로 질문을 넓혔다. 각 실험의 완료 검사는 속도 우위와 다르다. 합성 입력의 정상 재개방 검사는 전원 장애 동등성도 아니다.

### D42 초기 비교 도구의 완료량·메모리 검증

초기 ACK 비교와 문장별 DB 비용 도구는 실제 패키지 사용 경로와 자체 SQLite 기준군을 나눴다. 단순 성공 반환이나 빠른 오류를 처리량으로 세는 위험 때문에 완료량·저장 결과와 절대 peak 기록을 보강했다.

#140의 초기 비교 완료 표시는 그 당시 계약 검사의 상태다. 이후 모든 보장과 메모리 우위를 포괄하는 판정이 아니다. 전체 pending 복사 비용도 DB 비용과 분리해야 했다.

도구는 유지한다. 현재 보고서는 초기 완료 체크만으로 실제 앱 교체·OOM 해소·지속 메모리 절감 수치를 만들지 않는다. 후속 RSS 측정 한계는 D46에서 드러났다. ([초기 도구](https://github.com/woonyong-choi/totedb/commit/0475f78a), [검증 보강](https://github.com/woonyong-choi/totedb/commit/29b9075d), [#140](https://github.com/woonyong-choi/totedb/issues/140))

### D43 공식 YCSB로 TEXT 범위 병목 확인

단일 합성 비교 대신 공식 YCSB 0.17.0 core·A~F와 공통 Java/JNI SQL 경계를 채택했다. Mac M4에서 1천·1만행, worker 1·4, 조건당 5회, SQLite DELETE/FULL을 비교했다. 제품은 ToteDB 0.1.0, 소스 `be5fb10`이다.

정식 240회와 사전 48회는 완료·반환·재개방 검사를 통과했다. 1만행·worker 1의 E는 ToteDB 59.4ops/s, SQLite 4048.6ops/s였다. 대응 비 0.015 [0.013, 0.016]은 해당 조건 5쌍의 값이다. 사전 E 네 조건 모두 열세 기준에 해당했다.

관측은 유지하되 이 단계에서 엔진 변경은 없었다. SQLite WAL·prepare cache 최적 설정 비교나 DB 전체 순위가 아니다. 초기 환경 조회 실패는 workload 표본 0회로 보존했다. ([#226](https://github.com/woonyong-choi/totedb/issues/226), [YCSB 결과](experiments/ycsb-sqlite/report.md), [YCSB 요약](experiments/ycsb-sqlite/results/summary.json))

### D44 TEXT 하한 순회와 안전한 LIMIT 조기 종료 채택

E는 범위 밖 heap까지 읽고 정렬했다. top-k 정렬만으로는 전체 읽기가 남고 정수키 치환은 입력 의미를 숨긴다. 선택은 TEXT B+Tree의 하한부터 상한까지 순회하고 안전한 원본 PK ASC에서 LIMIT만큼 얻으면 중단하는 경로였다.

개발 중 출력 alias가 PK 이름을 가릴 때 정렬을 생략하는 회귀를 red/green으로 고쳤다. 실제 출력 열까지 확인하고 DESC·복합 정렬은 기존 경로를 남겼다. 이 red는 원 baseline 결함의 재현이 아니다. Mac 사전 timeout은 실패로 보존하고 별도 Linux 계획으로 전후를 다시 비교했다.

Linux `04f9f2d`→`b590d614`, 1만행·worker 1의 정식 20회에서 전후 5쌍 비 47.867 [46.313, 49.043]이었다. 39.4→1883.2ops/s였지만 SQLite after 2710.0ops/s보다 낮았다. 이 조건의 개선을 채택·유지한다. Mac 자원 압박을 timeout의 유일 원인으로 확정하거나 Mac·Linux를 합치지 않는다. ([#228](https://github.com/woonyong-choi/totedb/issues/228), [TEXT 결과](experiments/text-range/report.md), [TEXT 요약](experiments/text-range/results/summary.json), [alias red](experiments/text-range/validation/text-range-alias-red.json), [alias green](experiments/text-range/validation/text-range-alias-green.json))

### D45 독립 연결 큐에서 작업자 확장성 평가

공유 handle mutex와 실제 파일 경합을 혼동하지 않으려고 worker별 Database·Queue를 열었다. enqueue·drain·mixed·outbox, payload 256·4096바이트, 역할별 worker 1·4·8을 비교했다. SQLite는 WAL/FULL·DELETE/FULL과 문장 cache·상태 색인을 사용했다.

Linux `b590d61` 기준 실행은 정식 360회·사전 72회, 실행당 256작업이었다. mixed worker 8/1 대응 비는 두 payload에서 0.812 [0.711, 0.885], 0.761 [0.652, 0.881]였다. 두 배 확장 기대는 충족하지 못했고 증가 시 오히려 낮은 처리량을 관측했다.

단일 writer 선택은 유지하고 원인별 기여율은 미확정으로 남겼다. 정상 실측은 고정 시계로 lease 만료를 제외했다. 운영 장기 적체·다중 프로세스·실제 작업 업무의 재현은 아니다. Mac 정식 큐 수집은 진행하지 않았으며 Linux와 합산하지 않는다. ([큐 결과](experiments/queue-contention/report.md), [큐 요약](experiments/queue-contention/results/summary.json), [Linux 계획](experiments/queue-contention/linux-design.md))

### D46 H2 직접 복제·H3 버퍼 재사용의 정확성 통과와 제품 철회

큐 격차를 곧바로 WAL·다중 writer 재설계의 근거로 삼지 않았다. H2 `65a20bed`는 catalog encode/decode 복제를 직접 복제로, H3 `106bdbac`는 세대 변경 cache 본문 재할당을 버퍼 재사용으로 바꿨다. 당시 대안은 여러 변경 동시 반영 또는 단계별 비용·정확성·효과 비교였다.

정확성·소유권·실패 보존 검사는 통과했다. 그러나 mixed 4096작업·256바이트, worker 1·8의 누적 5쌍 비는 각각 0.999930 [0.954412, 1.010933], 1.002200 [0.977494, 1.036711]이었다. H2 worker 8의 작은 신호만으로 추가·누적 업무 이득을 채택하지 않았다.

두 제품 후보는 철회했고 실행 제품 소스가 `45be867c`와 같음을 대조한 기록을 남겼다. 정본에는 후보 철회와 평가 근거를 `128cbe6c`로 반영했다. TEXT 범위 개선까지 되돌린 것이 아니다. 정확성 시험·진단 도구·실패 자료는 유지한다. 최초 perf 탐색 실패, controller만 보는 cProfile, 전체 프로세스 trace와 상속 peak RSS의 한계도 보존했다. 개선 미입증은 무효과·동등성 증명이 아니다. ([#232](https://github.com/woonyong-choi/totedb/issues/232), [철회 반영 커밋](https://github.com/woonyong-choi/totedb/commit/128cbe6cd0bcaf8d2519073512177e53acff633f), [후속 평가와 철회](experiments/queue-contention/report.md#단일-writer-후속-비용-평가), [후보별 요약](experiments/queue-contention/results/single-writer-cost.json))

철회 범위는 이 두 실험 구현이다. FAILED 실패 격리, 현재의 읽기 페이지 cache, 앞으로 검증할 SQL 문장 cache 목표를 철회한 것이 아니다. 당시 판정은 mixed 업무의 처리량·지연·공정성 채택 근거가 중심이었다. 복사·할당 감소의 자체 효과나 원래 목표인 메모리 효율을 모두 부정하는 실험은 아니며, 그 기여도와 원 소비 경로의 이익은 별도 검증이 남는다.

### D47 매크로 계측·빌드 혼입 방지와 0.2.0 배포

외부 trace만으로 요청 단계의 원인을 구분하지 못했다. 선택은 상시 계측이 아니라 `BENCHMARK=1`과 `TOTE_BENCHMARK`에서만 요청·단계 wall/thread CPU·I/O·잠금을 기록하는 방식이었다. OFF에는 계측 정의·참조·TLS를 넣지 않는다.

종료 drain의 마지막 drop 누락, stripped ELF 동적 심볼 잔류 검사, stale 공유 library와 header version 의존성 문제를 발견해 수정했다. 계측 정확성과 배포 산출물 분리가 채택 대상이며 제품 속도 개선은 아니었다.

최종 native N=12의 ON 36레코드·drop 0, Python 49 passed·0 skipped를 기록했다. 후보 단계 45 passed·4 skipped와 다른 시점이다. `d79ad8c8`과 v0.2.0 게시·실설치 smoke는 유지한다. 이번 보고서에서 새 배포나 게시를 한 것은 아니다. ([PR240](https://github.com/woonyong-choi/totedb/pull/240), [제품 커밋](https://github.com/woonyong-choi/totedb/commit/d79ad8c86dcac985f7df5138d646bd1e8a2b3ee3), [완료 #232](https://github.com/woonyong-choi/totedb/issues/232), [v0.2.0](https://github.com/woonyong-choi/totedb/releases/tag/v0.2.0))

### D48 420표본에서 미시 UPDATE와 큐 결과 분리

단일 미시 시험의 우위를 전체 제품으로 확대하지 않으려고 동일 제품 `d79ad8c8`의 native UPDATE·공식 YCSB·큐를 나눴다. SQLite prepare-each와 prepared-reuse도 구분했다. ON은 원인 진단이고 최종 처리량은 OFF로 판정한다.

수정 수집은 정식 420회인 OFF 390회·ON 30회와 사전 84회였다. native N=1024에서 ToteDB/SQLite DELETE/FULL prepare-each 비는 2.5374 [2.4209, 2.5648]이지만 mixed 256바이트·worker 1의 ToteDB/SQLite WAL 비는 0.5930 [0.5610, 0.6112]였다. ON/OFF 0.9051 [0.8704, 0.9244]는 측정 자체의 교란을 보였다.

초기 중단 native 정식 60회·사전 12회는 별도 보존해 합계에서 제외했다. ON의 fsync wall이 큰 단계였다는 관측은 그 요청·부하의 진단이며 물리 쓰기량이나 SQLite 격차의 유일 원인이 아니다. 관측·계측 분리는 유지하고 제품은 바꾸지 않았다. ([#242](https://github.com/woonyong-choi/totedb/issues/242), [배포 비용 결과](experiments/release-cost/report.md), [배포 비용 요약](experiments/release-cost/results/summary.json), [실행 출처](experiments/release-cost/results/provenance.json))

### D49 실제 DB·Python 큐 280표본에서 성능 기대 미충족 확인

자체 SQLite 큐만으로 실제 라이브러리 교체를 판정할 수 없어 실제 패키지를 설치했다. 2026-10-19 확인 버전은 DuckDB 1.5.6, lmdb 3.0.0, rocksdict 0.3.29, persist-queue 1.1.0, Litequeue 0.14, DiskCache 5.6.3, Huey 3.4.0과 linked SQLite 3.45.1이다. SQL·KV 기록, ACK, 제거형 FIFO는 따로 비교했다.

제품 기준 `a86cf241`, 수집 도구 `5beed7f7`의 DB 180회·큐 100회가 정식 280회다. 예열 56회와 API 검사 6개는 별도다. 단일 client, 1024작업·256바이트의 정상 ACK 경로는 ToteDB 599, persist-queue 1649, Litequeue 1461jobs/s였다. ToteDB/persist-queue 대응 5쌍 비는 0.3628 [0.3458, 0.3639]이며 네 ACK 조건 모두 열세였다.

Litequeue에도 claim_id·retry_expired가 있고 persist-queue에도 auto_resume가 있다. 실패 보장은 같지 않으며 정상 재개방 통과를 모든 장애 동등성으로 읽지 않는다. SQL 두 INSERT와 KV 두 key, FIFO pop과 ACK도 같은 작업이 아니다. 관측은 유지하고 현재 속도 우위 기대는 미충족으로 판정한다. 전체 matrix와 p99는 원 보고서를 따른다. ([#244](https://github.com/woonyong-choi/totedb/issues/244), [실제 DB·큐 결과](experiments/local-db-comparison/report.md), [실제 DB·큐 요약](experiments/local-db-comparison/results/summary.json), [환경](experiments/local-db-comparison/env.json))

### 서로 합산하지 않는 측정 근거

| 질문 | 정식 표본과 별도 자료 | 판정 범위 |
|---|---|---|
| 기존 공식 SQL 부하 | YCSB 240회·사전 48회 | Mac·구 제품·DELETE/FULL 조건의 E 병목 |
| TEXT 변경 효과 | Linux 20회·전후 5쌍 | 해당 TEXT 범위·LIMIT 경로 개선 |
| 독립 연결 큐 확장성 | 360회·사전 72회 | 짧은 큐 부하의 worker 확장성 |
| H2·H3 효과 | H2 30회, H3 추가·누적 60회, 조건별 5쌍 | 후보 추가·누적 개선 미입증 |
| 배포판 다각도 비용 | 420회·사전 84회 | OFF 성능과 ON 진단의 분리 |
| 실제 DB·큐 | 280회·예열 56회·API 6개 | 단일 client 정상 경로와 설정 검사 |

표의 비와 구간은 서로 다른 환경·소스·부하의 결과다. 비를 곱하거나 하나의 신뢰구간·종합 순위로 만들지 않는다. 대부분 조건당 5쌍의 명목 bootstrap 95% 구간이며 다중 비교 보정이 없다. p99는 실행별 분위수의 중앙값이지 모든 요청을 합친 분위수가 아니다. 실측 결과를 복제하는 수치 행렬 대신 각 공개 요약을 정본으로 둔다. ([후보 한계](experiments/queue-contention/report.md#해석과-한계), [배포 비용 한계](experiments/release-cost/report.md#한계), [실제 비교 한계](experiments/local-db-comparison/report.md#한계))

## 6. 남은 반복 비용을 가설로 좁히되 원래 요구의 충족은 별도 판정

비교 결과는 단일 writer, COW, C 언어 중 하나를 범인으로 정하지 않는다. 단일 client에서도 격차가 있고 256바이트에서도 느리며 put·get·ack 모두 비용이 남았다. 소스 대조는 어디에서 일이 반복되는지 설명하지만 그 비중을 측정한 것은 아니다. 이 차이가 다음 구현과 실험의 범위를 정한다.

### D50 commit 묶음과 문장·페이지 변경 누적의 차이

현재 `catalog_clone`은 encode, decode, 임시 buffer 해제로 깊은 소유권을 만든다. 직렬화 검증·구성 경로를 재사용한다는 설명은 현재 코드 해석이며 최초 채택 이유는 미확인이다. 실행마다 lexer·binding 확인·parser를 거치고, INSERT마다 work catalog를 복제한다.

`publish_inner`는 `heap_batch_finish`를 끝낸 뒤 명시적 거래의 `stage_adopt`로 간다. `page_file_write`는 단순 dirty 표시가 아니라 encode·`write_full`·cache 갱신이며 하위에서 pwrite한다. batch32의 512→16 commit 감소에도 한 commit 안 INSERT 64개의 문장별 복제·heap 완료·페이지 쓰기는 남는다. 모든 문장이 fsync한다는 뜻은 아니다.

정상 no-idempotency·재시도 없음·첫 ACK 경로의 정적 대조는 ToteDB put/get/ack가 1/3/2SQL로 합계 6SQL·3쓰기 거래, persist-queue가 4SQL·3거래, Litequeue가 3SQL·3거래다. BEGIN/COMMIT·하위 SELECT·trigger 전체 수나 동적 counter가 아니다. 구조는 확인되지만 파싱·복제·I/O·FFI의 시간 기여율은 미확정이다. ([clone](../src/storage/catalog.c#L249-L257), [실행·게시](../src/sql/database.c), [heap 완료](../src/storage/heap_store.c), [쓰기](../src/storage/page_file.c#L1186-L1197), [큐 경로와 원인 분석](experiments/local-db-comparison/report.md#성능-우위-기대가-충족되지-않은-이유), [persist-queue 고정 소스](https://github.com/peter-wangxu/persist-queue/tree/b4fb6d186e375850b2d9ac49fc635779be95f173), [Litequeue 0.14 배포 소스](https://files.pythonhosted.org/packages/c1/3c/4a065c1e63f39682ea88422fcec8cd3387c8df2fb53205160026c9b5d2dc/litequeue-0.14.tar.gz))

SQLite는 이미 writeable인 페이지의 재사용과 dirty list를 제공한다. CPython sqlite3도 문장 cache를 경유한다. 실제 DB 기준군은 `cached_statements=128`이지만 실측 hit율은 미확인이다. Litequeue 0.14는 cache를 끄므로 경쟁 큐의 모든 우위를 prepared cache 하나로 설명할 수 없다. upstream SQLite 3.45.1·CPython 3.12.15와 배포 바이너리의 정확한 patch·revision 일치는 확인하지 않았다. ([SQLite pager](https://raw.githubusercontent.com/sqlite/sqlite/version-3.45.1/src/pager.c), [SQLite page cache](https://raw.githubusercontent.com/sqlite/sqlite/version-3.45.1/src/pcache.c), [CPython cache 경로](https://raw.githubusercontent.com/python/cpython/v3.12.15/Modules/_sqlite/cursor.c), [실제 runner](../bench/local_db_compare.py), 2026-10-19 확인)

LMDB 0.9.36의 `mdb_page_touch`는 같은 최상위 거래의 dirty 페이지를 다시 쓴다. RocksDict 0.3.29의 WriteBatch는 여러 key를 모아 한 번 제출하고 vendored RocksDB는 WAL·memtable 경로로 보낸다. SQL 없는 KV와 SQL 두 문장은 같은 계층 비용이 아니다. lmdb binding 버전 3.0.0과 linked native 0.9.36은 구분하며, RocksDict 실측 wheel의 exact native revision은 Unknown이다. DuckDB도 거래 묶음과 한 행씩 execute하는 입력 경계가 달라 이번 수치가 분석형 벡터화 이익의 평가가 아니다. ([LMDB touch](https://github.com/LMDB/lmdb/blob/LMDB_0.9.36/libraries/liblmdb/mdb.c#L2485-L2550), [RocksDict batch](https://github.com/rocksdict/RocksDict/blob/v0.3.29/src/write_batch.rs#L135-L151), [RocksDict 제출](https://github.com/rocksdict/RocksDict/blob/v0.3.29/src/rdict.rs#L1095-L1118), [vendored RocksDB 쓰기](https://github.com/facebook/rocksdb/blob/44e95d8af5d7ec503b3f1d5754c3379ab6c29a9d/db/db_impl/db_impl_write.cc#L531-L629), [DuckDB local storage](https://github.com/duckdb/duckdb/blob/v1.5.6/src/storage/local_storage.cpp#L459-L615), 2026-10-19 확인)

FFI에도 SQL result에서 native payload로, ctypes에서 Python bytes로 복사하는 경로가 있다. C API Item 전달 자체가 payload를 다시 memcpy하는 것은 아니다. 경쟁 구현에도 row·bytes·JSON 복원이 있다. 이 경로의 존재를 전체 복사량·주병목으로 바꾸지 않는다. 현재 판단은 구조적 비용 후보를 유지하고 동적 기여율 판정은 보류하는 것이다. ([큐 payload 소유권](../src/queue/ack_queue.c#L194-L221), [C API Item](../src/capi/totedb.c#L585-L608), [Python claim](../python/totedb/queue.py#L178-L195))

### D51 제한된 SQL cache 목표와 아직 충족하지 못한 요구

#239의 확정 목표에는 SQL 문장·준비 결과의 제한된 cache가 포함된다. 현재 parser는 실행 binding을 `value_clone`하므로 SQL text를 key로 AST 자체를 재사용하면 실행별 값이 섞일 수 있다. template와 binding 소유권, schema 무효화, 예산·eviction·실패 보존을 분리해야 한다.

이 cache는 미구현이다. 채택 조건은 hit뿐 아니라 miss·반복 없는 SQL 비용과 비계측 end-to-end 효과다. H2/H3를 근거 없이 재도입하거나 먼저 개선 배수를 약속하지 않는다. 코어의 큐 의존은 없지만 Python Database 생성자의 `_queues=set()`과 close의 목록 순회는 남아 있어 미사용 전용 비용 0을 달성했다고 쓰지 않는다.

원래 요구의 다음 판정에는 불편·미지원 시나리오와 우회 코드를 고정한 실제 pending 조회 메모리 비교가 필요하다. 빈번한 변경 효율은 일정 backlog에서 put·claim·ack·purge/DELETE를 반복하며 cleanup 포함 처리량·tail·쓰기량·파일 성장·재사용으로 판정해야 한다. 기존 fresh DB 처리량과 측정 밖 purge는 이 조건을 충족하지 않는다. 다중 writer는 #238의 별도 미결이다. ([#239](https://github.com/woonyong-choi/totedb/issues/239), [Python lifecycle](../python/totedb/database.py#L206-L279), [현재 parser](../src/sql/sql_statement.c), [기존 측정 범위](experiments/local-db-comparison/report.md#ack-큐-네-조건의-정상-처리량), [#238](https://github.com/woonyong-choi/totedb/issues/238))

### 유지·철회·보류와 다음 판정 조건

| 현재 상태 | 대상 | 다음 판정 조건 |
|---|---|---|
| 유지 | 독립 SQL·선택형 ACK·호출자 거래 결합 | 세 사용 방식의 계약·실패·미사용 비용을 따로 검사 |
| 유지 | 가변 값·catalog·색인·COW 게시·지연 회수·예산·소유권 | 실패 때 옛/새 상태와 회계·소유권 보존 |
| 유지 | TEXT 구간 순회·안전한 LIMIT, 매크로 계측·배포 격리 | 지원 정렬 의미와 OFF/ON 분리의 회귀 방지 |
| 철회 | PG 전체 호환, 구 서버·엔진, 문장 WAL·두 durability 모드 | 현재 제품의 기능으로 다시 소개하지 않는 조건 |
| 철회 | H2 직접 clone·H3 cache buffer 재사용 제품 후보 | 동일 계약의 새 전후 근거 없이는 재채택 금지 |
| 보류 | 병렬 writer·WAL 재설계·공개 prepared API | 주병목·충돌·복구·효과의 사전 기준 필요 |
| 미구현 | 제한 SQL 문장·준비 결과 cache | binding·schema·예산·hit/miss와 OFF 업무 효과 |
| 요구 미충족 | 현재 메모리 우위·기존 큐 대비 속도·미사용 전용 비용 배제 | 원 소비 경로 메모리·정상/실패 계약별 비교와 lifecycle 비용 분리 |
| 미검증 | 새 엔진의 총 편익·장기 INSERT/UPDATE/DELETE 효율 | SQLite 직접 ACK 대안의 기능·개발·검증·운영 비용, cleanup 포함 수명주기 비교 |

### 독자가 확인해야 할 미확인 범위

원형에서 자료구조·언어를 처음 고른 비교 이유, 이슈 본문의 변경 시점 전체, 구 엔진의 현재 보존 ref는 이번 근거로 확정하지 못했다. 이 공백은 현재 구조의 합리성으로 메우지 않는다. 보고서에 연결한 공개 파일과 커밋은 추적 근거이며, CI 원시 로그 전체와 외부 배포 바이너리의 소스 일치를 전수 감사했다는 주장은 하지 않는다.

제품 차원에서는 실제 소비 프로젝트의 완전 대체, 기존 불편·미지원의 구체 재현, pending 메모리 개선량이 남아 있다. 새 엔진이 필요했던 필연성과 현재 총 편익도 미입증이다. 안전성 차원에서는 power-cut·lost write·network FS, 성능 차원에서는 장기 cleanup·회수·재사용과 각 반복 비용의 기여율이 미검증이다. 이 범위는 캐시 구현 완료나 정상 재개방 PASS로 대신할 수 없다. ([현재 목표](https://github.com/woonyong-choi/totedb/issues/239), [안전성 한계](safety.md#검증하지-않은-것), [실제 비교 한계](experiments/local-db-comparison/report.md#한계))

## 결론

현재 선택의 기준은 ‘새 엔진으로 구현할 수 있다’가 아니라 ‘필요한 보장을 유지하면서 사용·실행·정리 비용을 줄였다는 근거가 있다’여야 한다. TEXT 개선과 H2/H3 철회의 차이는 내부 작업 제거가 아니라 비계측 업무 효과를 확인했는지에 있다. 같은 기준을 원래 요구에 적용하면 기능 경계는 구현됐지만 메모리·반복 삭제 효율·실제 교체 편익은 아직 완료라고 부를 수 없다.

다음 판단은 SQLite 위 직접 ACK라는 대안을 남긴 채 필요한 사용 장면을 고정하는 것이다. 문장 cache나 페이지 변경 누적은 그 장면의 비용을 줄일 후보이지 제품 필요성의 대체 증거가 아니다. 일정 backlog와 cleanup을 포함한 수명주기 비교가 목표를 충족하지 못하면 그 결과도 후보 철회와 같은 수준의 결정 근거로 남겨야 한다. 이 보고서는 기존 공개 근거를 종합한 Markdown 정본이며, 새 실험·코드·배포 결과를 만들지 않는다.
