# TEXT 색인 구간 순회: 실험 결과

## 요약

같은 Linux VM에서 ToteDB의 공식 YCSB E 처리량은 변경 전 39.4ops/s에서 변경 후 1,883.2ops/s로 증가했다. 전후 5쌍의 대응 비율 중앙값은 47.867이며 bootstrap 95% 구간은 [46.313, 49.043]이다. Linux 계획의 H1·H2를 채택한다. TEXT 기본 키 구간을 색인에서 순회하고 필요한 결과에 도달하면 중단하는 경로를 [아키텍처](../../architecture.md)에 반영했다.

이 조건에서도 변경 후 ToteDB 처리량은 SQLite의 2,710.0ops/s보다 낮다. DB 전체의 우위나 다른 workload의 개선을 뜻하지 않는다. 최초 Mac 실행은 사전 timeout으로 중단했으며 Linux 표본과 합치지 않았다.

## 실험 환경

| 항목 | 값 |
|---|---|
| 계획 | [Mac 계획](design.md) `79981f1`, [Linux 계획](linux-design.md) `c1a7cdc` |
| Linux 실행 id | `20261019T031416Z-b590d61` |
| 소스 | before `04f9f2d14d94da7284b90b60370ded5ebefab65c`, after `b590d614fa20e7e6bd44f2dbbb6caabb5a1d36b1` |
| 환경 | [VM 기록](machine.json), [첫 after 환경](data/raw/20261019T031416Z-b590d61/r1-after/env.json), 상위 manifest의 모든 child 환경 |
| 머신 | GitHub Actions Ubuntu 24.04, Linux 6.17.0-1022-azure, AMD EPYC 7763, 할당 vCPU 4개, MemTotal 16,373,452kB |
| 도구 | 공식 YCSB 0.17.0, Temurin JDK 21, 시스템 C compiler, SQLite 3.45.1. 실제 build 명령·버전·해시를 child 환경에 보존. 2026-10-19 확인 |
| 설정 | 10,000행, TEXT 기본 키, 10필드×100바이트 BLOB, worker 1, 실행당 1,000논리 연산 |
| 저장 설정 | 자동 commit, 페이지 8,192바이트, 연결당 cache 256페이지. SQLite DELETE/FULL, fullfsync OFF, mmap OFF |
| 표본 | Linux 정식 20회, preflight 20회, baseline load 20회. 엔진·전후마다 정식 5회 |

양쪽 worktree의 빌드를 모두 끝낸 뒤 측정했다. 공식 workload와 Java/JNI 바인딩 소스는 전후 동일했다. 상위 manifest가 소스와 빌드 산출물의 SHA-256을 고정했고 매 child 실행 전과 수집 종료 뒤 변하지 않았음을 확인했다. 요청 난수열은 독립적이며 같은 trace가 아니다. 전후 순서는 반복마다 교대했다. 각 child 내부의 ToteDB·SQLite 순서는 기존 runner를 유지했다.

공식 runtime은 worker 시작부터 join까지이며 연결 초기화·종료를 포함한다. 외부 wall time은 JVM 시작을 포함한다. 매 실행 전 전체 audit를 읽었으므로 cold cache 측정이 아니다. JIT 예열은 하지 않았다.

## 계획과 다른 점

| 변경 | 시점 | 이유 | 결론에 미친 영향 |
|---|---|---|---|
| Mac 첫 collect에서 전체 전후 matrix 중단 | Linux 수집 전 | ToteDB preflight가 180초 제한을 초과했다. 당시 process 기록의 부하 평균이 높았고 별도 관측에서 swap 사용량도 컸다. 자원 압박이 timeout의 유일한 원인이라고 확정하지 않았다. | Mac 비교는 판정 불가다. 실패를 교체하지 않고 부분 raw·로그·요약·해시를 보존했다. |
| Linux 독립 계획으로 다시 수집 | 새 수집 전 | 같은 VM에서 두 제품 소스를 비교하고 다른 로컬 작업과 측정을 분리했다. | 환경을 바꾼 별도 실험이다. Mac과 Linux를 합산하지 않았으며 Linux 계획을 수집 전에 커밋했다. |
| 실패 DB artifact 보존 경로 보완 | Linux 수집 뒤 | 독립 검토에서 실패 DB가 runner build 폴더에만 남는 설정을 발견했다. | 이번 Linux 수집은 전부 성공했다. 성공 표본과 가설을 바꾸지 않고 후속 workflow에서 실패 DB의 별도 업로드를 추가했다. |

Mac 실행 `20261019T030026Z-c18643c`는 r1-before 하나만 끝났다. [child 요약](data/raw/20261019T030026Z-c18643c/r1-before/results/summary.json)의 정식 실행은 2회, preflight는 2회, 실패는 1회다. 정식 두 실행은 완료했지만 before·after 5쌍을 이루지 못했다. [로그](logs/all-20261019T030026Z-c18643c.log)와 [자원 관측](validation/macos-resource-observation.json)을 보존했다. 부분 자료의 해시·집계 재현 성공은 workload 정확성 통과를 뜻하지 않는다.

## 데이터 처리

| 단계 | 수 |
|---|---|
| Mac 부분 수집 | 정식 2회·preflight 2회, 그중 preflight timeout 1회. 전후 비교에서 제외 |
| Linux baseline load | 20회, 각 10,000행과 10,000,000필드 바이트 확인 |
| Linux preflight | 20회, 오류·저장 검사 통과. 정식 처리량에서 제외 |
| Linux 정식 분석 | 20회, 각 1,000논리 연산. 엔진·전후마다 5,000연산 |
| Linux 정식·preflight timeout·non-OK·binding 오류·재시도 예산 소진 | 모두 0건 |
| Linux 정식 표본의 교체·삭제·이상치 제외 | 0회 |
| 별도 Linux profile | 정식 표기 2회·preflight 2회, baseline load 2회. 전부 정식 전후 통계에서 제외 |

[원자료 안내](data/README.md), [전체 요약](results/summary.json), [CSV](results/summary.csv), [정규화 레코드](data/processed/records.jsonl)를 함께 보존했다. 대응은 같은 outer 반복 번호의 before·after다. 각 child는 새 JVM과 기준 DB의 새 파일을 사용했다.

## 가설 검증

| 가설 | 지표 | 값 | 사전 지정 신뢰구간 | n | 판정 |
|---|---|---|---|---|---|
| H1 | 완료·반환 상태·재개방 audit | 정식 20/20회 각각 1,000연산 완료, 오류·audit 실패 0건 | 전수 검사로 해당 없음 | 20회 | 채택 |
| H2 | ToteDB after/before 처리량 비율 >2 | 대응 비율 중앙값 47.867 | percentile bootstrap 95% [46.313, 49.043] | 5쌍 | 채택 |

아래 처리량은 실행별 중앙값 [최소, 최대]다. 대응 비율은 중앙값끼리 나눈 값과 다를 수 있다.

| 엔진 | before ops/s | after ops/s | 대응 after/before [95% 구간] |
|---|---|---|---|
| ToteDB | 39.4 [38.5, 40.5] | 1883.2 [1841.6, 1934.2] | 47.867 [46.313, 49.043] |
| SQLite 대조군 | 2754.8 [2557.5, 2849.0] | 2710.0 [2631.6, 2849.0] | 1.019 [0.950, 1.051] |

ToteDB의 대응 비율 다섯 개는 47.867, 49.043, 46.460, 46.313, 48.749였다. SQLite의 구간은 1을 포함했다. bootstrap은 seed 228로 10,000회 복원추출한 대응 중앙값의 percentile 구간이다. 작은 표본과 같은 머신의 반복 변동만 반영하며 다른 장치의 효과를 보장하지 않는다.

### 시간과 지연

| 엔진 | 단계 | 공식 runtime 중앙값 ms | 외부 wall 중앙값 s | SCAN p50 µs | SCAN p95 µs | SCAN p99 µs |
|---|---|---|---|---|---|---|
| ToteDB | before | 25355 | 25.528 | 26383 | 39807 | 41951 |
| ToteDB | after | 531 | 0.706 | 446 | 784 | 1144 |
| SQLite | before | 363 | 0.544 | 252 | 511 | 661 |
| SQLite | after | 369 | 0.544 | 248 | 471 | 683 |

지연은 실행별 histogram의 분위수를 구한 뒤 다섯 값의 중앙값을 취했다. 전체 요청을 합친 분위수가 아니다. 실제 E의 SCAN·INSERT 수와 각 분위수의 전체 범위는 요약에 보존했다.

### 실제 작업과 저장 검사

| 엔진 | 단계 | 논리 연산 | 반환 행 | 반환 필드 바이트 | BUSY 재시도 |
|---|---|---|---|---|---|
| ToteDB | before | 5000 | 241997 | 241997000 | 0 |
| ToteDB | after | 5000 | 239455 | 239455000 | 0 |
| SQLite | before | 5000 | 237692 | 237692000 | 0 |
| SQLite | after | 5000 | 239438 | 239438000 | 0 |

양쪽이 실제 반환 결과를 소비했고 반환 바이트는 행 수의 1,000배였다. 매 child에서 두 엔진의 baseline 전체 해시가 같았다. 실행 전후 별도 JVM으로 다시 열어 키·값 형식과 최종 행 수를 검사했다. 최종 추가 행은 성공 INSERT 수와 일치했고 missing_rows는 모두 0이었다. 독립 검증자는 child load와 전후 audit를 원자료에서 다시 대조했다.

## 추가 분석

수집 뒤 [별도 profile](profile/results/summary.json)을 실행했다. 양쪽 엔진의 사전·분석 실행 모두 오류가 없었다. 기존 runner는 Linux에서 macOS sample을 제공하지 않는다. 이 profile로 새 native 병목을 확정하지 않는다.

기존 [Mac before profile](../ycsb-sqlite/profile/data/raw/20261018T003900Z-be5fb10-profile/trials/10000-E-t1-measured-1-totedb/sample.txt)에는 전체 `scan_rows`와 `merge_sort`가 관측됐다. 새 경로의 [정확성 근거](validation/text-range-correctness-inputs.json)와 [SQL 실행 출력](validation/text-range-final-sql.stdout)에서는 전체 행 스캔 없이 색인 조회를 사용했다. 이 시험의 방문량을 YCSB 전체 실행의 카운터로 바꾸어 해석하지 않는다. profile의 처리량·CPU·RSS는 가설 판정에 합치지 않았다.

## 해석

이 변경은 범위 밖 전체 행을 읽고 정렬하던 일을 줄인다. TEXT 색인의 하한부터 상한까지 순회하며 안전한 단일 기본 키 오름차순 정렬에서 필요한 결과를 얻으면 멈춘다. SQLite 대조군이 비슷한 범위에 머무른 동안 ToteDB의 SCAN 지연과 end-to-end 처리량이 함께 개선됐다. 기존 profile과 새 경로의 동작 검증에 부합하는 관측이다.

출력 별칭이 원본 기본 키 이름을 가릴 때 정렬을 잘못 생략하는 개발 중 회귀도 재현하고 고쳤다. [red](validation/text-range-alias-red.json)·[green](validation/text-range-alias-green.json)은 해당 중간 구현의 근거다. red를 원래 제품 baseline의 결과라고 주장하지 않는다. 정렬은 실제 계획의 출력 열까지 확인하며 DESC·복합 정렬·긴 경계의 기존 경로를 유지한다. 디스크 형식과 공식 workload는 바꾸지 않았다.

## 위험 요소 점검

| 위험 요소 | 이 실험에서 |
|---|---|
| 머신 부하·열·시간 추세 | Mac 실패를 별도로 보존했다. Linux 단일 VM에서 전후 순서를 교대했고 SQLite를 대조군으로 남겼다. 공유 host와 cache·온도를 통제하지 않았다. |
| E의 무작위 scan 길이와 insert 비중 | 실제 연산 수·반환 행·바이트를 보존했다. 동일 요청열로 해석하지 않았다. |
| audit가 cache를 데움 | 기존 audit 순서를 유지했다. cold cache 결과로 일반화하지 않았다. |
| 작은 대응 표본과 독립성 한계 | 5쌍의 전체 범위와 bootstrap 구간을 보고했다. 다른 장치·부하에 일반화하지 않았다. |
| 빌드·소스 변경 | 빌드 선행과 매 child 전·수집 후 HEAD·소스·산출물 해시 대조가 통과했다. |
| Linux profile에 native stack 없음 | 새 병목은 미확인으로 남겼다. 코드 경로·기존 before profile·기능 실행 근거와 구분했다. |

## 한계

- 1만 행·worker 1·E만 새로 비교했다. A~F 전체, 더 큰 데이터, 여러 writer의 성능을 증명하지 않는다.
- SQLite는 DELETE/FULL과 호출마다 prepare/finalize하는 기존 YCSB 조건이다. WAL·문장 cache를 튜닝한 최상 성능 비교가 아니다.
- 정상 commit과 reopen을 확인했다. 전원 장애 내구성 동등성은 검증하지 않았다.
- 기본 heap 시험의 Mac 60초 timeout과 별도 300초 한도 성공 기록을 함께 보존했다. Linux sanitizer CI 통과를 Mac timeout 원인 분석으로 바꾸지 않는다.

## 재현 방법

저장소 루트에서 저장된 Linux 자료를 검사한다. 원자료를 다시 수집하거나 덮어쓰지 않는다. `analyze`는 processed와 results 생성물을 다시 만든다.

```sh
RUN_ID=20261019T031416Z-b590d61-check ./docs/experiments/text-range/run.sh verify
RUN_ID=20261019T031416Z-b590d61-check ./docs/experiments/text-range/run.sh analyze
python3 bench/ycsb/run.py verify --output docs/experiments/text-range/profile
python3 bench/ycsb/run.py verify --output docs/experiments/text-range/data/raw/20261019T030026Z-c18643c/r1-before
```

동일한 check 로그가 있으면 새 RUN_ID와 `DATA_RUN_ID=20261019T031416Z-b590d61`를 지정한다. Mac child의 `failed_runs: 1`은 보존된 실패 상태다. Linux 원자료·파일 목록과 processed·summary JSON/CSV 재생성은 바이트까지 일치했다. [독립 검증](validation/text-verification.json)도 별도 구현으로 bootstrap과 실제 연산을 대조했다.

새 실측은 [Linux 계획](linux-design.md)의 고정 source worktree와 빌드 순서를 따른다. [수집 CI](https://github.com/woonyong-choi/totedb/actions/runs/39193707075)는 기능 검사와 두 실험·별도 profile·최종 재검증을 통과했다. 원격 VM의 source는 수집 당시 SHA로 고정됐으며 결과 문서만으로 최신 소스를 측정했다고 주장하지 않는다.

| 파일 | SHA-256 |
|---|---|
| `data/SHA256SUMS-20261019T031416Z-b590d61` | `6acf839bc538cc3b1b4d8c5e6346ed3971420ecbd51d1b30690ab577dca65b32` |

## 결론

| 가설 | 판정 | 반영한 문서 |
|---|---|---|
| Linux H1 | 채택. 정식 20회 완료·저장 검사 통과 | [빌드와 시험](../../build-and-test.md) |
| Linux H2 | 채택. 대응 개선 구간 하한 >2 | [아키텍처](../../architecture.md), [실험 목록](../README.md) |
| 최초 Mac 계획 | 비교 판정 불가. 실패 자료 보존 | 이 보고서의 계획과 다른 점 |
