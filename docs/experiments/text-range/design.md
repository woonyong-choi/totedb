# TEXT 색인 구간 순회: 실험 계획

| 항목 | 값 |
|---|---|
| 추적 | [TEXT 범위 조회 개선 #228](https://github.com/woonyong-choi/totedb/issues/228) |
| 관련 설계 | [아키텍처](../../architecture.md), [기존 YCSB 계획](../ycsb-sqlite/design.md) |
| 사전 데이터 | 기존 YCSB 보고서의 E 1만 행·worker 1 결과와 기존 before profile을 관측했다. 이 자료는 새 전후 대응 표본에 합치지 않는다. |

## 목적

TEXT 기본 키의 구간 순회 변경이 공식 YCSB E 처리량을 개선하는지 확인한다. 같은 머신에서 변경 전후를 새로 수집하여 정확성과 2배 개선 여부를 판단한다.

## 가설

| 가설 | 예측 | 적용 범위 |
|---|---|---|
| H1 | 정식 실행 20회가 모두 논리 연산 1,000회를 완료하고 연산 오류와 재개방 audit 실패가 0건이다. | 아래 E·1만 행·worker 1의 양쪽 엔진·전후 조건 |
| H2 | ToteDB의 대응 after/before 처리량 비율 중앙값의 95% 신뢰구간 하한이 2.0보다 크다. | 같은 반복 번호의 전후 5쌍 |

## 비교 조건

| 항목 | 값 |
|---|---|
| 조건 | before 제품은 `04f9f2d`와 동일한 `src/`, `include/`, `Makefile`을 사용한다. after는 #228 변경을 포함한 고정 커밋이다. 수집 소스의 정확한 SHA와 제품 기준 SHA를 상위 manifest에 따로 보존한다. |
| 실행 순서 | 반복 1·3·5는 before 다음 after, 반복 2·4는 after 다음 before다. 각 기존 collect 내부 순서는 변경하지 않는다. |
| 평가 방식 | 기존 공식 runner와 자동 검사다. 사람의 점수 평가는 없어 맹검은 해당 없다. |

각 collect는 ToteDB와 SQLite를 모두 실행한다. 기존 Java·JNI 바인딩, workload·dependency lock과 기존 benchmark 소스는 전후 동일해야 한다. 기존 공개 C API·내구성·재시도·audit·fresh JVM 조건을 유지한다. 비교 대상은 ToteDB의 전후 변화이며 SQLite 전후 비율은 머신과 실행 시점의 변동을 살피는 대조군이다. 엔진 전체의 승자를 정하지 않는다.

## 측정 지표

| 지표 | 종류 | 정의 | 단위 |
|---|---|---|---|
| 조건 | 조건 값 | 10,000행·E·스레드 1·1,000논리 연산·내부 반복 1 | 행·개 |
| 처리량 | 측정 값 | 공식 `Throughput(ops/sec)`의 전후·엔진별 중앙값·최소·최대 | ops/s |
| 시간 | 측정 값 | 공식 `RunTime(ms)`와 JVM 시작을 포함한 외부 wall time의 중앙값·최소·최대 | ms·s |
| 지연·실제 연산 비중 | 측정 값 | READ·SCAN·INSERT별 p50·p95·p99와 실제 연산 수. E의 확률적 요청 구성은 강제하지 않는다. | µs·건 |
| 오류·저장 계약 | 측정 값 | timeout·non-OK·binding 오류·재시도·결과 행/바이트·재개방 행/값 audit | 건·행·바이트 |
| 대응 비율 | 계산 값 | 반복별 after 처리량/before 처리량 5개의 중앙값과 percentile bootstrap 95% 구간 | 비율 |

## 실험 환경

| 항목 | 값 |
|---|---|
| 운영체제 | 기존 환경은 macOS 26.5.2 build 25F84 arm64다. 수집 시 각 child env가 실제 값을 기록한다. |
| 실행 방식 | 동일 로컬 머신의 두 독립 Git worktree다. `BASELINE_ROOT`, `CHANGED_ROOT`를 필수로 지정한다. |
| 하드웨어 | 기존 머신은 Apple M4 10코어·메모리 24GiB다. 각 child env에 실제 hardware·cpu_count를 보존한다. GPU는 사용하지 않는다. |
| 도구 | 기존 공식 YCSB 0.17.0·Microsoft OpenJDK 21·C compiler·native SQLite다. 실제 버전과 명령은 child env 및 build.json에서 기록한다. 잠금 파일은 `bench/ycsb/dependencies.lock.json`이다. |
| 대상 | before 제품 기준 `04f9f2d`, after는 수집 시 변경 소스 HEAD다. 양쪽 수집 SHA·관련 소스와 빌드 산출물 SHA-256은 manifest에 고정한다. |
| 모델 | 해당 없음 |
| 공통 값 | 기존 YCSB 설정인 10필드×100바이트, 자동 commit, 페이지 8,192바이트, 연결당 cache 256페이지, Java `-Xms256m -Xmx1g`, histogram p50·p95·p99를 사용한다. |
| 입력 | 공식 E와 잠금 파일의 SHA-256 고정 입력이다. child manifest의 기존 파일별 source_sha256과 상위 manifest를 보존한다. |
| 시드 | 공식 요청열은 ThreadLocalRandom이라 고정하지 못한다. 대응은 같은 요청열이 아니라 시간상 가까운 반복 쌍이다. bootstrap 시드는 228이다. |
| 실행 명령 | 아래 명령을 실험 계획 커밋 이후 실행한다. |

```sh
# 두 worktree 경로를 지정한 셸에서 시작한다.
: "${BASELINE_ROOT:?기준 worktree 절대 경로 필요}"
: "${CHANGED_ROOT:?변경 worktree 절대 경로 필요}"
export BASELINE_ROOT CHANGED_ROOT
export RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-$(git rev-parse --short=7 HEAD)"
# 두 worktree를 먼저 준비하고 각각 기존 build를 완료한다.
python3 "$BASELINE_ROOT/bench/ycsb/run.py" build
python3 "$CHANGED_ROOT/bench/ycsb/run.py" build
# BASELINE_ROOT와 CHANGED_ROOT는 export한 서로 다른 절대 경로다.
./docs/experiments/text-range/run.sh collect
# collect 로그 첫 줄의 RUN_ID를 같은 자료의 후속 명령에 전달한다.
RUN_ID="$RUN_ID" ./docs/experiments/text-range/run.sh analyze
RUN_ID="$RUN_ID-check" ./docs/experiments/text-range/run.sh verify
```

수집 전 관련 소스의 clean 상태, build.json의 존재·소스 이후 생성 시각, 빌드 산출물 경로·존재·해시를 검사한다. 수집 중 빌드는 금지한다. 수집 전후와 매 collect 전에 관련 소스·산출물 해시와 HEAD가 그대로인지 검사한다. mtime 검사는 최신 빌드의 보조 증거이며 실제 build 명령 실행을 대신하지 않는다.

### 조건별 값

| 설정 | before | after |
|---|---|---|
| 제품 소스 | 기준 제품과 동일한 소스 | TEXT 색인 구간 순회 변경 소스 |
| 수집 위치 | `BASELINE_ROOT` | `CHANGED_ROOT` |

## 표본

| 항목 | 값 |
|---|---|
| 출처 | 기존 공식 YCSB runner의 합성 데이터·E 독립 요청열 |
| 크기 | 10개 collect × 2엔진 = 정식 20회다. ToteDB 전후 5회씩과 SQLite 전후 5회씩이다. |
| 크기 근거 | 같은 머신에서 허용하는 수집 한도를 전후 5쌍으로 고정한다. 모집단 효과의 정밀도를 보장하는 표본 수는 아니다. |
| 중단 규칙 | 10개 collect가 끝나면 중단한다. 실패하면 즉시 중단하고 부분 raw를 보존하며 다른 실행으로 교체하지 않는다. |
| 반복과 예열 | 매 collect는 내부 반복 1·preflight 1회씩 양쪽 엔진에서 실행한다. preflight 총 20회와 baseline load 총 20회는 정식 처리량에서 제외한다. JIT 예열은 하지 않는다. |

## 분석 방법

| 가설 | 지표 | 방법 | 채택 기준 |
|---|---|---|---|
| H1 | 완료·오류·재개방 audit | 모든 child의 기존 analysis.verify와 원자료 재파싱으로 20개 정식·20개 preflight 및 20개 baseline load를 확인한다. | 정식 20회가 각각 1,000논리 연산을 완료하고 오류·audit 실패가 모두 0건이다. |
| H2 | ToteDB 대응 after/before 비율 | 대응 비율 5개를 복원 추출하여 중앙값을 10,000회 계산한다. seed 228, 정렬 표본의 선형 보간 2.5·97.5 percentile을 사용한다. 기존 YCSB bootstrap 구현의 방법을 재사용하되 시드를 변경한다. | 하한 >2.0이면 채택, 상한 ≤2.0이면 기각, 그 외 보류다. 모든 실행·검증이 정상일 때만 판정한다. |

| 항목 | 규칙 |
|---|---|
| 제외 기준 | 이상치를 제외하지 않는다. 실패·누락·불완전 쌍은 전체 비교를 실패시킨다. |
| 실패한 실행 | 종료 코드·누락 표본·audit 오류와 부분 자료를 보존하고 표본을 교체하지 않는다. |
| 다중 비교 | 확인할 성능 가설은 ToteDB H2 하나다. SQLite 비율은 별도 기술 통계로만 보고한다. |
| 유효 숫자 | JSON·CSV에는 계산 값을 보존하며 보고서 처리량은 소수 1자리, 비율·구간은 소수 3자리로 표시한다. |

원자료는 `data/raw/RUN_ID/r1-before`부터 `r5-after`까지 child collect 전체 출력인 raw·env·results를 그대로 보존한다. DB 파일은 각 소스 worktree의 기존 `build/ycsb/runs/`에 생성된다. 상위 `data/SHA256SUMS-RUN_ID`는 상위 manifest를 포함한 원자료 파일 목록과 해시를 고정한다. `verify`는 목록·내용 일치, child 기존 검증, 상위 JSON Lines·JSON·CSV 재생성의 바이트 일치를 검사한다. `analyze`는 항상 raw에서 다시 전처리한다. `DATA_RUN_ID`는 로그용 RUN_ID와 분석 대상 자료 id를 다르게 지정할 때 사용한다.

정규화 레코드는 기존 run_id·trial_id·condition·ts_utc·input_identifier를 보존하고 source_phase와 source_repetition만 추가한다. 모든 보고 수치는 `results/summary.json`에서 가져온다. `summary.csv`는 같은 요약을 항목 경로와 값으로 펼친다.

## 판정

| 결과 | 설계 문서에 반영 |
|---|---|
| 채택 | 정확성 회귀 검사와 함께 구간 순회 변경의 이 조건에서의 효과를 보고한다. |
| 기각 | 2배 개선 주장을 채택하지 않고 관측된 효과와 한계를 보고한다. |
| 보류 | 작은 표본의 불확실성을 보고하고 추가 측정은 새 계획으로 분리한다. |

## 추가 분석

기존 before profile은 보존한다. after profile은 별도 경로에서 담당자가 수집하며 이 도구의 정식 수집·통계에 포함하지 않는다.

## 위험 요소와 대응

| 위험 요소 | 대응 |
|---|---|
| 머신 부하·열·시간 추세가 처리량을 바꾼다. | 전후 순서를 번갈아 실행하고 SQLite 전후 비율을 함께 보고한다. 다른 성능 측정과 동시에 실행하지 않는다. |
| E의 무작위 scan 길이와 insert 비중이 달라진다. | 실제 연산 수·반환 행·바이트와 지연을 보존한다. 같은 요청열이라고 주장하지 않는다. |
| audit가 파일 cache를 데워 cold cache 측정을 방해한다. | 기존 audit 순서를 유지하고 cold cache 결과로 일반화하지 않는다. |
| 5쌍은 작고 같은 머신의 반복이라 독립성에 한계가 있다. | bootstrap 구간은 이 실행 조건의 반복 변동만 설명하며 다른 장치·부하·내구성 설정에 일반화하지 않는다. |
| 빌드가 소스보다 오래되거나 수집 중 변경된다. | 수집 전에 각 worktree build를 수행하고 소스·산출물 해시를 고정하여 변화를 거부한다. |
