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
