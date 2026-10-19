# 큐 쓰기 경합 Linux 재측정 계획

| 항목 | 값 |
|---|---|
| 추적 | [YCSB 동등 조건 측정 #226](https://github.com/woonyong-choi/totedb/issues/226) |
| 관련 설계 | [기존 실험 계획](design.md), [CI](../../../.github/workflows/ci.yml) |
| 사전 데이터 | 기존 Mac 계획과 관측을 배경으로만 사용한다. Linux 정식 자료는 수집 전이다. |

## 목적

Ubuntu VM에서 공개 Python 큐 경로의 처리량·지연·경합·저장 결과를 독립적으로 비교한다. 수집 전에 이 계획을 커밋으로 고정하며 Mac 계획·실측을 고치거나 대체하지 않는다.

## 가설

| 가설 | 예측 | 적용 범위 |
|---|---|---|
| H1 | 모든 정식 실행이 작업 256개를 완료하고 중복 완료·누락·payload 불일치·재개방 저장 실패가 없다. | 정식 360회 |
| H2 | mixed의 worker 8에서 ToteDB의 완료 처리량은 worker 1의 두 배 미만이다. | payload별 5쌍 |

## 비교 조건

| 항목 | 값 |
|---|---|
| 기준 조건 | SQLite WAL/FULL·autocheckpoint 1,000페이지·문장 cache 128 |
| 추가 조건 | SQLite DELETE/FULL·문장 cache 128 |
| 비교 조건 | ToteDB 공개 Python Database·Queue·DURABLE COW commit |
| 실행 순서 | 기존 workload·payload·worker matrix 순서와 반복별 엔진 시작 순환 유지 |
| 평가 방식 | 기존 수집기·자동 저장 검증. 사람 평가는 해당 없음 |

부하별 생산자·소비자 수, worker별 연결, 요청 처리 시간 경계, payload 생성·사전 적재·audit 제외, 재시도·고정 시계·lease 조건은 [기존 실험 계획](design.md)을 그대로 적용한다. 공식 YCSB와 별도인 합성 큐 부하다. 공개 API 사용 경로 비교로 한정한다.

## 측정 지표

[기존 실험 계획](design.md)의 처리량·연산 지연·BUSY·대기·EMPTY·오류·CPU·RSS·파일·저장 검증·엔진별 비교 지표를 같은 정의와 단위로 사용한다.

## 실험 환경

| 항목 | 값 |
|---|---|
| 운영체제 | GitHub Actions `ubuntu-24.04`. 실제 kernel·runner 이미지 버전은 `machine.json`에 기록한다. |
| 실행 방식 | TEXT 비교와 같은 독립 VM에서 TEXT 수집이 끝난 뒤 직렬 실행한다. |
| 하드웨어 | runner CPU 모델·코어 수·RAM은 사전에 확정하지 못한다. `machine.json`에 `lscpu`, `cpu_count`, `/proc/meminfo`를 수집한다. GPU는 사용하지 않는다. |
| 도구 | 시스템 Python 3·C compiler·SQLite. 실제 버전·소스·native library 해시는 기존 `env.json`에 보존한다. |
| 대상 | `f7dd4a57c2d6455036f8047d2423bd03d0e9d6b8`의 제품과 큐 도구 |
| 모델 | 해당 없음 |
| 공통 값 | 기존 8,192바이트 페이지·cache 256페이지·재시도·256개 작업·내구성 설정 유지 |
| 입력 | 기존 결정적 합성 payload 256B·4,096B와 부하 네 종류. 수집 소스 SHA-256은 기존 env에 보존 |
| 시드 | 기존 결정적 작업 ID·payload와 bootstrap seed 226 유지 |
| 실행 명령 | PR의 `benchmark-comparison` label로 CI job을 실행한다. 아래 명령은 모든 빌드와 TEXT 수집이 끝난 뒤 실행한다. |

```sh
export QUEUE_ROOT="${GITHUB_WORKSPACE}.wt/queue-contention"
export RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-$(git rev-parse --short=7 HEAD)"
bash "$QUEUE_ROOT/docs/experiments/queue-contention/run.sh" all
```

큐 worktree는 CI가 위 고정 커밋으로 준비한다. 빌드 `make SANITIZE= all`은 TEXT 수집 전에 완료한다. 측정 중 다른 빌드·테스트·프로파일링을 실행하지 않는다. 공통 `RUN_ID`는 UTC와 checkout한 PR SHA로 정하고 큐 실제 소스 SHA를 별도로 남긴다. `machine.json`은 CPU·RAM·부하·runner 이미지·UTC·세 소스 SHA를 보존한다.

### 조건별 값

[기존 실험 계획](design.md)의 엔진별 API·commit·cache·색인·checkpoint 설정을 모두 유지한다. 변경하는 조건은 Linux VM 환경뿐이다.

## 표본

| 항목 | 값 |
|---|---|
| 출처 | 기존 결정적 합성 작업과 상태 변화 |
| 크기 | 4부하×2payload×3worker×3엔진×5반복의 정식 360회와 사전 72회, 총 432회 |
| 크기 근거 | 기존 수집 한도 유지. 검정력 기반 표본 수 산정 아님 |
| 중단 규칙 | child당 180초. 실패·timeout·부분 로그와 DB를 보존하며 다음 조건 계속. 표본 대체·속도 기준 조기 중단 없음. job 전체 상한 45분 |
| 반복과 예열 | 정식 5회·조건당 사전 1회. 모두 새 프로세스·DB. 사전 실행은 정식 통계에서 제외 |

## 분석 방법

[기존 실험 계획](design.md)의 분석·제외·실패·다중 비교·유효 숫자 규칙을 유지한다. H1은 정식 360회 전수 저장 검증으로 판단한다. H2는 payload·반복별 worker 8/1 비율 중앙값과 seed 226·10,000회 percentile bootstrap 95% 구간으로 판단한다. 상한 <2이면 채택, 하한 ≥2이면 기각, 나머지는 보류다. 실패 조건은 비교 불가로 처리하며 제거하지 않는다. 엔진 간 비교는 가설 판정과 구분한다.

## 판정

Linux 자료만으로 H1·H2를 판정하고 기존 계획의 반영 범위를 유지한다. Mac 관측과 합산하거나 플랫폼 간 우위를 주장하지 않는다. FULL·DURABLE 설정의 전원 장애 동등성은 검증하지 않는다.

## 추가 분석

정식 두 실험이 성공한 뒤 기존 `run.sh profile`을 별도 `profile/`에 실행한다. mixed worker 4·payload 256B·256개 작업·3엔진·사전 포함 6회 조건을 유지한다. 실행 ID에는 UTC와 큐 소스 SHA를 쓴다. CPU·RSS·phase 원자료를 보존하고 정식 통계에서 제외한다. profile 실패는 별도 step 결과에 남기며 formal 비교 성공을 실패로 바꾸지 않는다. profile 뒤 두 formal 원자료를 다시 verify한다. Linux에서 macOS sample을 사용할 수 없어 기존 cProfile만으로 native stack의 병목을 확정할 수 없다. native profile은 별도 진단 계획과 실행 자료로 분리한다.

## 위험 요소와 대응

| 위험 요소 | 대응 |
|---|---|
| VM·공유 host 부하 변동 | CPU·RAM·부하·runner 이미지를 보존하고 엔진 시작 순환 유지 |
| Python GIL·바인딩·설정 차이 | 공개 API 사용 경로로 해석하고 native 엔진 전체 성능으로 해석 금지 |
| 작은 backlog·짧은 실행·플랫폼 차이 | 256개 규모를 명시하고 Linux와 Mac 자료를 별도 분석 |
| 실패·부분 완료를 정상 처리량으로 셈 | 기존 전수 저장 검증을 유지하고 실패 조건 비교 금지 |
| 원자료 소실 | `always()` artifact `queue-contention-linux-comparison`에 실험 폴더·환경·계획을 보존 |

## Issue #232 후속 비용 진단과 전후 비교

이 절은 위 실험과 기존 보고서를 바꾸지 않는 별도 사전 계획이다. 첫 후보 변경은 catalog clone 비용 축소다. 측정 전에는 CPU, 잠금, fsync 중 어느 쪽이 주병목인지 정하지 않는다. 프로덕션 코어 계측 API나 커스텀 profiler를 추가하지 않고 기존 `Trial`·`Condition`·`run_trial`과 `run.py _trial` 프로세스를 재사용한다.

### 질문과 고정 조건

| 가설 | 확인할 근거 | 해석 제한 |
|---|---|---|
| H1 SQL 반복 파싱 | 별도 perf CPU stack의 parser·lexer·문장 실행 심볼 | 공개 API·Python 생성기 비용과 구분하며 샘플만으로 호출 횟수를 추정하지 않는다 |
| H2 catalog 복제·재저장 | catalog clone·save 관련 CPU 심볼과 쓰기 syscall | 심볼을 분류하지 못하면 원본 top functions만 보고한다. 미샘플은 비용 0이 아니다 |
| H3 generation reload·cache | generation 검사·reload·cache 관련 CPU stack | OS page cache와 엔진 cache를 혼동하지 않는다 |
| H4 flock·fsync | strace의 flock·fsync·fdatasync·pwrite64 호출 수와 누적 시간 | thread별 누적 syscall 시간을 elapsed로 나눠 대기 비율이라고 주장하지 않는다 |

기준선은 `origin/main`에 포함된 `9b212d68542ed24c6ead62eb9fdad62a174e2f2b`다. CI는 형제 `<repo>.wt/queue-cost-baseline`에만 detach worktree를 만든다. candidate는 merge ref가 아닌 PR head SHA를 명시해 checkout한다. 두 소스의 `make SANITIZE= all`을 모두 완료한 뒤 수집한다. 양쪽 공유 라이브러리는 recipe의 `-O2 -g -fPIC`와 `CC='cc -fno-omit-frame-pointer'`를 함께 사용한다. 실제 명령·Git HEAD와 library hash를 보존하며 수집 중 빌드하지 않는다.

공통 workload는 mixed, payload 256B, 작업 4,096개, workers 1·8이다. mixed의 workers는 기존처럼 생산자와 소비자 각각의 수다. baseline·candidate는 `totedb`, control은 `sqlite-wal` engine이다. variant 이름을 engine으로 사용하지 않는다. 프로세스마다 정확한 prebuilt `TOTEDB_LIB`를 넘기고 모든 trial은 새 프로세스와 새 DB를 사용한다. 공개 API, 파일 형식, DURABLE/FULL 내구성, lease·fence·재시도 설정과 측정 구간은 기존 정의를 유지한다.

### 표본과 보존 계약

각 worker 조건에서 variant 세 개를 최대 12개 작업으로 각각 사전 실행한다. 사전 실행 하나라도 실패하면 즉시 종료하고 비교를 거절한다. 통과하면 정식 5회를 실행한다. 반복마다 baseline·candidate·control의 시작 순서를 순환한다. 총 사전 6회, 정식 30회다. 사전·정식·trace의 phase를 구분하고 계측 자료를 정식 통계에 섞지 않는다. 기존 전체 360회를 다시 실행하지 않는다.

`compare`는 기존 output을 실행 전에 거절한다. 실패·timeout·잘린 stdout·stderr·spec·worker evidence와 DB를 모두 보존한다. timeout은 하위 프로세스 그룹을 종료한다. 각 원본 JSON은 재개방 audit, ID와 payload 대응, 완료 작업 수, 오류, BUSY, retry sleep, 요청별 latency, CPU·RSS, after-close 파일 크기와 settings를 유지한다. manifest에는 라이브러리 전후 SHA-256, 수집기 소스 해시, Git HEAD·status, Python·OS·CPU·부하와 명령을 기록한다. 한 조건의 어떤 trial이라도 실패하면 해당 조건 전체가 비교 불가다. 실패를 제외하거나 대체 실행하지 않는다.

### 별도 Linux 진단

정식 수집 뒤 ToteDB와 SQLite WAL을 직렬로 trace한다. 각 engine의 strace와 perf도 서로 다른 DB에서 직렬 실행한다. strace는 `-f -c -e trace=fsync,fdatasync,pwrite64,flock`, perf는 `record -e cpu-clock:u -g --call-graph fp`를 사용한다. 추적 범위는 요청 구간만이 아니라 설정·연결 종료·audit를 포함한 전체 trial 프로세스다. manifest와 tool metadata에 `scope='whole-trial-process-including-setup-close-audit'`를 명시한다. 추적 실행의 처리량으로 전후 성능을 비교하지 않는다.

추적 뒤 `perf report -i perf.data --stdio --no-children --sort symbol`과 `perf script -i perf.data`를 별도 명령으로 각각 실행한다. 원본 perf.data·report·syscall 표와 실제 callstack 텍스트 `script-stdout.txt`를 보존한다. 각 명령의 stdout·stderr, 종료 코드와 빈 stdout 여부를 `report-process.json`·`script-process.json`에 기록한다. report 실패도 script 실행을 생략할 이유가 아니며, 빈 자료는 성공한 비용 분석으로 해석하지 않는다. 로컬 macOS에서 perf.data를 해석할 수 없어도 원격에서 저장한 텍스트로 후속 검토할 수 있다.

도구 미설치, kernel 지원 부재, 권한 거절, 비Linux는 `unavailable`이다. 비용 0으로 바꾸지 않는다. 실제 trial이 시작해 실패한 경우는 도구 지원 실패와 구분한다. CI는 설치된 실제 perf 실행 파일을 선택하며 `/usr/bin/perf` wrapper만 존재하는 상태를 성공으로 보지 않는다. 권한 조정은 CI에서 `kernel.perf_event_paranoid=1`에 한정하며 collector를 root로 실행하지 않는다.

### 첫 진단 뒤 도구 보완

첫 실행 `39206644421`은 정식 비교와 저장 검사를 완료했지만 perf 실행 파일을 찾지 못해 CPU stack을 수집하지 못했다. 후속 실행은 `/usr/lib/linux-tools-*/perf`와 `/usr/lib/linux-tools/*/perf` 두 설치 위치 및 심볼릭 링크를 확인하고 실제 경로·버전·software event 접근 결과를 보존한다. 기존 실행의 `unavailable`을 성공으로 바꾸지 않는다.

첫 strace의 기본 `-c` 시간은 syscall의 system time이다. 후속 진단은 `-w`로 시작부터 종료까지의 누적 thread wall-clock 시간을 구한다. 두 시간 종류를 합치지 않고, 어느 쪽도 전체 elapsed의 비중으로 해석하지 않는다. 계측 변경은 정식 비교의 조건·표본·채택 기준을 바꾸지 않는다.

Linux에서 새 실행 이미지의 `resources_before.peak_rss_bytes`가 이미 높은 이전 프로세스의 최고치를 물려받을 수 있다. 첫 실행에서는 대부분의 before/after 최고치가 같았으므로 해당 RSS로 엔진 메모리 우위를 판정하지 않는다. CPU는 user/system 누적 차이와 실제 기록 경계를 함께 확인한다.

### 분석과 채택 기준

도구 자체의 합격 조건은 정확성 검사와 증거 보존 계약이다. 속도 합격선은 없다. elapsed와 연산별 request p50·p95·p99를 보존하고 각 variant의 elapsed median·min·max·n을 보고한다. 반복 번호로 짝지은 `baseline elapsed / candidate elapsed`가 동일 작업량의 candidate/baseline 처리량 비다. 이 비들의 median·min·max·n과 seed 226·10,000회 percentile bootstrap 95% 구간을 계산한다. 비의 중앙값과 두 중앙값의 비를 혼동하지 않는다.

최적화 채택은 별도 correctness pass가 전제다. paired bootstrap 하한이 1보다 크고 반복 범위·실행 간 변동을 검토해 같은 방향의 효과를 지지하며, native 근거가 변경한 비용과 부합할 때만 개선으로 판단한다. 구간이 1을 포함하거나 반복 변동보다 차이가 작으면 미확정이다. 상한이 1 이하이거나 기대한 비용 축소가 관측되지 않으면 개선 가설을 기각하거나 추가 근거가 필요하다고 기록한다. 도구가 자동으로 최적화 채택을 선언하지 않는다.

정규 `queue-contention` CI는 label 없이 `test_queue*.py`를 실행해 진단 회귀와 jobs 12 정확성 smoke를 포함한다. 수집 job은 PR의 `queue-cost-experiment` label이 있을 때만 Ubuntu 24.04에서 최대 30분 실행한다. 최종 라이브러리 해시를 재확인한 뒤에만 정상 summary를 게시한다. library 누락·읽기 실패·변경은 이유와 해시 가능한 항목을 남기고 비교 불가로 종료한다. 수집 중 파일·분석 예외는 이 실행이 새로 만든 output만 실패 terminal manifest로 마무리한다. 기존 output을 거절할 때는 원래 manifest를 수정하지 않는다. 원자료·DB와 단일 `SHA256SUMS`를 보존한다. 성공 여부와 무관하게 `queue-cost-hypotheses` artifact에 출력과 실패 DB를 보존한다. 기존 YCSB 및 큐 원자료는 수정하지 않는다. 부하가 높은 로컬 환경에서는 작은 정확성 smoke만 실행하고 성능 결론을 내리지 않는다.
