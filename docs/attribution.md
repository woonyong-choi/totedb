# 출처와 기여 경계

현재 코드, 외부 구성 요소, 과거 팀 기여를 정리한다.

## 현재 엔진의 구현 범위

SQL 해석·실행과 행·색인·페이지 저장 구현은 [src/sql/](../src/sql/)과 [src/storage/](../src/storage/)에 있다. 기본 공유 라이브러리는 이 C 소스들을 빌드한다. C API는 [src/capi/](../src/capi/), Python 바인딩은 [python/totedb/](../python/totedb/)에 있다. 선택형 큐는 [src/queue/](../src/queue/)에서 일반 SQL과 트랜잭션 API를 사용한다.

계층별 책임은 [아키텍처](architecture.md), 기본 빌드 구성은 [Makefile](../Makefile)과 [queue.mk](../src/queue/queue.mk)에 있다.

## 외부 구성 요소와 비교 도구

- 파일 읽기·쓰기·동기화·잠금은 운영체제의 `pread`, `pwrite`, `fsync`, `flock`을 사용한다. 스레드 동기화와 수학 함수의 기본 링크 의존성은 pthread와 libm이다.
- Python 공유 라이브러리 호출에는 표준 라이브러리의 ctypes를 사용한다. Python 객체의 메모리 관리는 Python 런타임이 맡는다.
- SQLite와 다른 외부 DB·큐 라이브러리는 비교 대상으로 사용한다. 선정 범위와 역할은 [비교 대상 조사](experiments/local-db-comparison/catalog.md)에 있다.
- 부하 생성에는 공식 YCSB Client와 workload를 사용한다. 이 저장소의 SQL·JNI 연결 코드는 [공식 YCSB 비교 바인딩](../bench/ycsb/README.md)에 설명한다. 공식 자료의 Apache 2.0 LICENSE·NOTICE는 별도로 보존한다.
- 문서 그림은 [DiagramPlay](https://github.com/woonyong-choi/diagramplay)로 생성한다. 입력 원본과 도구 버전은 [그림 재생성 안내](architecture.md#그림-원본과-재생성)에 있다.

## 참고 자료와 코드 재사용

YCSB 비교의 bootstrap 구간은 [SciPy bootstrap 문서](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.bootstrap.html)의 percentile 정의를 따른다. Python 표준 라이브러리를 사용하는 계산 방법과 조건은 [YCSB 측정 계획](experiments/ycsb-sqlite/design.md)에 있다.

COW·B+Tree·인코딩 개발의 참고 자료와 코드 차용 이력은 미확인이다.

## 팀 과제

> [!NOTE]
> 이 절과 다음 개인 확장 절은 팀 과제와 옛 엔진 시기의 기록이다. 표의 parser·planner·executor·pager·bptree·table·lock_table 코드는 커밋 `5e6b265`에서 삭제했다.

이 저장소는 크래프톤 정글 팀 과제([Jungle-12-303/wk08_1](https://github.com/Jungle-12-303/wk08_1))에서 시작했다. 과제 종료 후 개인 저장소에서 수정과 확장을 이어갔다. 팀 코드 전체를 개인 구현으로 보지 않는다. 팀 기간의 기여 구분은 원본 저장소의 커밋 저자와 파일별 diff로 확인한다.

| 항목 | 내용 |
|---|---|
| 원본 팀 저장소 | [Jungle-12-303/wk08_1](https://github.com/Jungle-12-303/wk08_1) |
| 기간 | 2026-04-16 ~ 2026-05-08 (커밋 `40327ef` ~ `93114c8`). 다른 팀원의 마지막 커밋은 2026-04-22 |
| 팀 구성 | 5인. `main`에 병합된 기여자는 4인(최우녕·정범진·이호준·최현진)이고, 나머지 1인의 커밋은 병합되지 않은 브랜치에 남아 있다. `git shortlog -sne --all`로 확인 |
| 본인 담당 | 저장소 전반의 엔진 코드(parser·planner·executor·pager·bptree·table·lock_table)를 개인 주도로 대부분 직접 구현 |

## 개인 확장 (팀 과제 종료 후)

커밋 범위: [`f70b243`](https://github.com/woonyong-choi/totedb/commit/f70b243) (2026-08-01) ~ `e5bfabf` (2026-09-23), 23개 커밋.
`git log f70b243^..e5bfabf --reverse --format='%h %ad %s' --date=short` 기준이다.

| 무엇이 달라졌나 | 커밋 | 파일 |
|---|---|---|
| B+Tree 인덱스 범위 스캔(`INDEX_RANGE`) 추가. `BETWEEN`·부등호 파싱, 접근 경로, 리프 순회 | `7ea5a09` | `bptree.c`, `parser.c`, `planner.c`, `executor.c` |
| 1M 행 INSERT의 O(N²) 결함 2건 수정. 힙 재탐색 힌트, REPL lock 해제 누락 | `e640fdd` | `table.c`, `pager.c`, `main.c`, `executor.c` |
| sanitizer 선택 빌드(ASAN/UBSAN)와 테스트를 묶는 CI 게이트 도입 | `f814357`, `f862e3e` | `Makefile`, `.github/workflows/ci.yml` |
| 저장소 정리. 빌드 산출물·Python 캐시·작업용 스테이징 폴더 제거 | `9b92a14`, `df55747` | `.gitignore` 외 |
| 문서. 저장 구조 선택 근거, 실행 방법, 출처·기여 경계 명시 | `f70b243`, `cce95b9`, `413db92`, `5b3d044` | `README.md`, `docs/` |
| B+Tree 구조 불변식 property test 추가. 무작위 삽입·삭제 후 트리 전체를 걸어 8종 검사, 결함 5종 주입으로 검증 | `239f302` | `tests/test_bptree_property.c` |
| `INDEX_RANGE` 엔드투엔드 테스트 추가. 경계·LIMIT·삭제 후, 힙 스캔 결과와 대조 | `239f302` | `tests/test_step1_sql_ext.c` |
| EXPLAIN이 실행기와 다른 계획을 보고하던 버그 수정 + 회귀 테스트 | `7f6cb84` | `parser.c`, `planner.c`, `tests/` |
| 규모별 기울기 벤치마크(`make bench`). 재현되지 않던 절대 배수를 배가 계수로 교체, 당시 그래프 생성 포함 | `7f6cb84` | `bench/scaling.py` |
| 설계 노트. 버린 대안과 이유, 정확성 검증의 한계, 다음 병목 | `7f6cb84` | `docs/design.md` |
| Strict 2PL lock 해제·힙 체인 재탐색 회귀 테스트(`make test-step3`) | `e5bfabf` | `tests/test_step3_regression.c` |

팀 과제 종료 시점(`93114c8`) 대비 `e5bfabf`의 엔진 코드 변경은 `src/` 기준 7개 파일 +462/-88행이다(`git diff --shortstat 93114c8 e5bfabf -- src/`, 2026-09-23 기준).
