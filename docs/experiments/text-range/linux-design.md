# TEXT 색인 구간 순회 Linux 재측정 계획

| 항목 | 값 |
|---|---|
| 추적 | [TEXT 범위 조회 개선 #228](https://github.com/woonyong-choi/totedb/issues/228) |
| 관련 설계 | [기존 실험 계획](design.md), [CI](../../../.github/workflows/ci.yml) |
| 사전 데이터 | macOS 첫 before collect에서 ToteDB preflight가 180초 timeout으로 실패했다. 같은 child의 정식 ToteDB·SQLite 실행은 완료했으나 10개 child 중 9개가 미실행되어 전체 비교는 불가능하다. |

## 목적

Ubuntu VM에서 TEXT 범위 조회 변경의 정확성과 2배 개선 여부를 독립적으로 판단한다. 이 계획을 수집 전에 커밋으로 고정한다. Mac 실패 원인 분석과 Linux 가설 검증을 구분하며 기존 실패 원자료를 대체하거나 새 표본과 합치지 않는다.

## 가설

| 가설 | 예측 | 적용 범위 |
|---|---|---|
| H1 | 정식 실행 20회가 모두 논리 연산 1,000회를 완료하고 연산 오류와 재개방 audit 실패가 0건이다. | E·1만 행·worker 1의 양쪽 엔진·전후 조건 |
| H2 | ToteDB의 대응 after/before 처리량 비율 중앙값의 95% 신뢰구간 하한이 2.0보다 크다. | 같은 반복 번호의 전후 5쌍 |

## 비교 조건

| 항목 | 값 |
|---|---|
| 조건 | before는 `04f9f2d14d94da7284b90b60370ded5ebefab65c`, after는 이 계획을 포함한 PR head SHA다. |
| 실행 순서 | 반복 1·3·5는 before 다음 after, 반복 2·4는 after 다음 before다. child의 기존 순서를 유지한다. |
| 평가 방식 | 기존 공식 YCSB runner·자동 audit·검증을 사용한다. 사람 평가는 해당 없다. |

공식 YCSB 소스·워크로드·잠금 파일의 바이트는 전후 동일하다. 빌드와 측정 중 소스 변경을 금지한다. 기존 수집기의 HEAD·소스·빌드 산출물 해시 고정을 유지한다. SQLite 비율은 시점 변동의 대조군으로만 보고한다.

## 측정 지표

[기존 실험 계획](design.md)의 측정 지표 전체를 같은 정의와 단위로 사용한다. 공식 처리량·시간·지연·연산 구성·오류·audit와 전후 대응 비율을 보존한다.

## 실험 환경

| 항목 | 값 |
|---|---|
| 운영체제 | GitHub Actions `ubuntu-24.04`. 실제 kernel·runner 이미지 버전은 `machine.json`에 기록한다. |
| 실행 방식 | 다른 CI job과 분리된 단일 VM에서 두 Git worktree를 직렬 실행한다. |
| 하드웨어 | runner가 할당한 CPU 모델·코어 수·RAM은 사전에 확정하지 못한다. `machine.json`의 `lscpu`, `cpu_count`, `/proc/meminfo`로 실물을 기록한다. GPU는 사용하지 않는다. |
| 도구 | Temurin JDK 21, 시스템 C compiler·SQLite·Python 3. 실제 버전·의존성 해시는 기존 child env·build manifest에 보존한다. |
| 대상 | before 고정 SHA와 checkout한 PR head SHA를 `machine.json`과 상위 manifest에 보존한다. |
| 모델 | 해당 없음 |
| 공통 값 | 기존 계획의 10필드×100바이트, 8,192바이트 페이지, cache 256페이지, 자동 commit, JVM heap·histogram 설정을 유지한다. |
| 입력 | `bench/ycsb/dependencies.lock.json`과 공식 E의 기존 SHA-256 고정 입력 |
| 시드 | 공식 요청열의 ThreadLocalRandom은 고정하지 못한다. bootstrap seed 228을 유지한다. |
| 실행 명령 | PR에 `benchmark-comparison` label을 붙여 CI의 `benchmark-comparison` job을 실행한다. 내부 명령은 아래와 같다. |

```sh
export BASELINE_ROOT="${GITHUB_WORKSPACE}.wt/text-range-before"
export CHANGED_ROOT="$GITHUB_WORKSPACE"
export RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-$(git rev-parse --short=7 HEAD)"
python3 "$BASELINE_ROOT/bench/ycsb/run.py" build
python3 "$CHANGED_ROOT/bench/ycsb/run.py" build
make -C "${GITHUB_WORKSPACE}.wt/queue-contention" SANITIZE= all
bash docs/experiments/text-range/run.sh all
```

모든 빌드를 먼저 끝내고 TEXT 수집 다음에 큐 수집을 실행한다. 같은 VM에서 빌드·테스트·프로파일링을 측정과 겹치지 않는다. `machine.json`에 UTC·runner 이미지·CPU·RAM·부하 평균·세 소스 SHA를 기록한다. 기존 child env와 별도로 이 파일을 artifact에 보존한다.

### 조건별 값

| 설정 | before | after |
|---|---|---|
| 소스 | `04f9f2d` | 계획을 포함한 PR head |
| 위치 | `BASELINE_ROOT` | `CHANGED_ROOT` |

## 표본

| 항목 | 값 |
|---|---|
| 출처 | 공식 YCSB E의 합성 데이터와 독립 요청열 |
| 크기 | 정식 20회, preflight 20회, baseline load 20회 |
| 크기 근거 | 기존과 같은 수집 한도인 전후 5쌍. 모집단 정밀도 보장 없음 |
| 중단 규칙 | 10개 collect 완료 시 중단. child 실패 시 TEXT 수집을 중단하고 부분 원자료를 보존한다. 표본 교체 없음. job 전체 상한 45분 |
| 반복과 예열 | collect별 엔진당 정식 1회·preflight 1회·baseline load 1회. preflight와 load는 정식 통계에서 제외. JIT 예열 없음 |

## 분석 방법

[기존 실험 계획](design.md)의 분석·제외·실패·다중 비교·유효 숫자 규칙을 그대로 적용한다. H1은 전수 완료·오류·audit 검사로 판단한다. H2는 5개 대응 비율 중앙값과 seed 228·10,000회 percentile bootstrap 95% 구간으로 판단한다. H2 하한 >2.0이면 채택, 상한 ≤2.0이면 기각, 그 밖은 보류다. 모든 수집·검증이 정상일 때만 H2를 판단한다.

## 판정

Linux 관측만으로 H1·H2를 판정한다. 기존 계획의 판정별 문서 반영 범위를 유지한다. 결과는 Mac 자료와 별도 보고하며 플랫폼 간 효과가 같다고 일반화하지 않는다.

## 추가 분석

정식 두 실험이 성공한 뒤 changed YCSB의 E·1만 행·worker 1·1,000연산·반복 1회 profile을 별도 `profile/`에 수집한다. 실행 ID에는 UTC와 changed 소스 SHA를 쓴다. CPU·RSS·phase 원자료를 보존하며 정식 통계에 포함하지 않는다. profile 실패는 별도 step 결과에 남기며 formal 비교 성공을 실패로 바꾸지 않는다.

기존 runner의 macOS sample 분기는 Linux에서 native stack을 제공하지 않으므로 이 실행만으로 native 병목을 확정하지 않는다. profile이 끝난 뒤 두 formal 원자료를 다시 verify한다.

## 위험 요소와 대응

| 위험 요소 | 대응 |
|---|---|
| 공유 runner host의 부하와 VM 자원 변동 | 실제 CPU·RAM·부하·이미지 정보를 보존하고 단일 VM 내 전후 순서를 번갈아 실행 |
| 무작위 scan·insert 구성과 audit의 cache 영향 | 실제 연산·반환 행·바이트를 보존하고 동일 요청열·cold cache 해석 금지 |
| 작은 대응 표본과 플랫폼 차이 | 불확실성 구간을 보고하고 Mac 실패 자료를 별도로 보존 |
| 실패 시 원자료 소실 | `always()` artifact로 `text-range-linux-comparison`을 보존하고 실패 표본 교체 금지 |
