# 빌드와 시험

README의 빠른 시작에 담지 않은 빌드 변수, 시험 대상, 외부 엔진과의 비교 방법, 플랫폼 문제를 적는다.

## 빌드

| 항목 | 내용 |
|---|---|
| 언어 | C11, `-Wall -Wextra -Werror` |
| 도구 | GNU Make, C11을 지원하는 GCC 또는 Clang. `CC` 기본값은 `gcc`, Clang은 `make CC=clang`으로 선택 |
| 런타임 의존성 | POSIX 파일 I/O와 pthread, libm |
| Python 바인딩 | 표준 라이브러리 ctypes |
| 시험 의존성 | `libsqlite3-dev`, `sqlite3` CLI, pytest |

`make all`은 DB와 큐를 묶은 공유 라이브러리 `build/lib/libtotedb.so`(macOS에서는 `.dylib`)를 `-O2`로 만든다. 이 라이브러리는 sanitizer를 켜지 않는다. Python 바인딩의 라이브러리 탐색 순서와 존재하지만 로딩에 실패한 후보의 처리는 [설정](configuration.md#우선순위)에 있다.

시험 바이너리는 `SANITIZE` 변수로 sanitizer를 고른다. 기본값은 `address,undefined`이고 Darwin 25 이상에서는 `undefined`다. `BUILD_DIR`를 바꾸면 sanitizer 설정이 다른 빌드를 따로 둘 수 있다.

```sh
make test-all
make SANITIZE=thread BUILD_DIR=build-tsan test-transaction test-session test-ack-queue
make SANITIZE= BUILD_DIR=build-nosan test-database
```

## 시험 대상

| 대상 | 검사하는 것 |
|---|---|
| `make test` | 행 인코딩, overflow, 값, 카탈로그, lexer, 페이지 파일, 카탈로그 저장, 기본 키·TEXT 색인 트리 |
| `make test-overflow-file` | overflow 체인의 파일 왕복 |
| `make test-value-sqlite` | 값 비교·변환을 시스템 SQLite와 대조 |
| `make test-value-varchar` | VARCHAR 경계 값과 UTF-8 판정 |
| `make test-database` | SQL 문장 parser, heap 저장소, 문장 실행, SQLite 대조, I/O·할당 실패 주입, 메모리 한도 열기, 기본 키 색인 |
| `make test-transaction` | 트랜잭션 상태 기계, writer 경합, commit 결과, 실패 주입 |
| `make test-session` | 세션 메모리 예산과 결과 수명 |
| `make test-ack-queue` | ACK 큐의 C API와 실패 주입 |
| `make test-ack-queue-python` | Python 바인딩의 DB·트랜잭션·큐 계약 |
| `make test-all` | 위 대상 전체 |
| `make test-runner` | 시험 실행기와 실패 판정기 |
| `make test-fault` | 실패 주입 런타임 |
| `make test-result` | 결과 버퍼 인코딩과 할당 실패 |
| `make test-benchmark` | 별도 계측 ON/OFF 빌드와 C API 계측 회귀. `test-all`에 포함되지 않는다. |

시험 실행기 [fault_runner.py](../tests/fault_runner.py)로 감싼 대상은 실행 결과와 시드 등을 `build/fault/`의 JSON으로 남긴다. `BUILD_DIR`를 바꾸면 이 경로도 해당 디렉터리 아래의 `fault/`로 바뀐다. CI는 기본 `build/fault/`를 sanitizer별 산출물로 올린다.

모든 시험이 이 실행기를 거치는 것은 아니다. `make test-ack-queue-python`은 pytest를 직접 호출하므로 결과를 콘솔과 CI 작업 로그에서 확인한다. `make test-benchmark`의 직접 실행 항목도 콘솔에 결과를 남긴다. 이 실행들의 결과와 시드가 모두 `build/fault/`에 JSON으로 모인다고 가정하지 않는다.

### 요청 단위 내부 계측

정상 빌드는 `BENCHMARK=0`이며 계측 매크로의 인자와 선언은 전처리 때 사라진다. 런타임 환경 변수로 켤 수 없다. `BENCHMARK=1`만 별도 `build-benchmark`에서 계측 소스를 링크한다. 이 모드에 `BUILD_DIR=build`를 주면 실패한다. 사용자 지정 디렉터리를 재사용해도 모드·컴파일러·플래그 fingerprint가 바뀌면 객체와 공유 라이브러리를 다시 만든다. 실제 플래그는 해당 디렉터리의 `.build-fingerprint`와 Make 출력에 남는다.

```sh
make test-benchmark
make BENCHMARK=0 SANITIZE= counter-driver
python3 bench/counter_run.py --build-dir build --output .local/runs/counter-off --n 12
make BENCHMARK=1 SANITIZE= counter-driver
python3 bench/counter_run.py --build-dir build-benchmark --output .local/runs/counter-on --n 12
python3 tools/verify_benchmark.py build/lib/libtotedb.so
python3 tools/verify_benchmark.py build-benchmark/lib/libtotedb.so --enabled
```

macOS에서는 검사할 라이브러리의 확장자를 `.dylib`로 바꾼다. output 디렉터리는 새 경로여야 한다. 실패한 DB도 보존한다. 이 작은 실행은 정확성 검사이며 성능 결론의 근거가 아니다. 두 엔진 모두 같은 단일 행 스키마와 단일 클라이언트로 매 증가마다 명시적 begin/update/commit을 실행하고 영향 행 수, 최종 값, 닫았다 다시 연 영속 값을 검사한다. SQLite prepare-each와 prepared-reuse는 별도 구성이다. 입력 생성·DB 생성·JSON 출력은 측정 밖이다. Tote의 결과 wire 인코딩은 exec에 포함되며 결과 해석·해제와 SQLite changes/reset/finalize 비용은 별도로 남긴다.

계측 범위는 C API exec·begin·commit·rollback·queue put/get/ack 전체이며 검증 실패와 EMPTY 반환도 포함한다. 내부에는 어휘 분석·구문 분석, 실행 부모, 카탈로그 복제·save/load, 게시·commit, 정수 키 lookup·heap page 수집·batch finish, 핸들 mutex 획득, 파일 lock 획득과 개별 pread/pwrite/fsync/flock 호출이 있다. ROW_INDEX는 이 coarse 범위만 포함하며 전체 행 처리 비용을 뜻하지 않는다. 결과는 스레드별 고정 버퍼에 쌓이고 요청 밖에서 drain한다. 불완전한 시계·스택·overflow·drop은 카운터로 남는다.

inclusive 단계 시간은 자식을 포함한다. exclusive만 자식 시간을 제외하므로 부모와 자식을 더하면 안 된다. CPU 시간과 I/O wall 시간을 합쳐 100%로 만들지 않는다. wall−CPU는 미분류 잔여이며 OS off-CPU나 잠금 시간으로 단정하지 않는다. I/O bytes는 커널 호출에 수락된 양이지 물리 장치 전송량이 아니다. SQLite 내부 단계는 측정하지 않았으므로 unknown이며 0비용이 아니다.

wheel과 sdist 재빌드는 환경의 Make 모드 변수를 차단하고 패키징 전용 경로에서 `BENCHMARK=0 SANITIZE=`로 강제 재빌드한다. 복사 전후와 설치 smoke에서 `tb_bench` 정의·참조·TLS 심볼을 검사하여 계측 라이브러리의 배포를 거절한다.

### 여러 프로세스 outbox 예제

[run.py](../examples/outbox/run.py)는 Python 생산자가 주문과 작업을 함께 저장하고 C worker가 출고 기록과 ACK를 함께 commit하는 예제다. 외부 출고 API를 호출하지 않고 DB의 출고 행으로 처리 결과를 확인한다. Python 3.9 이상과 위 C 빌드 도구를 준비하고 저장소 루트에서 실행한다.

```sh
make BENCHMARK=0 SANITIZE= all build/outbox_consumer
TOTEDB_LIB=build/lib/libtotedb.so PYTHONPATH=python python3 examples/outbox/run.py --consumer build/outbox_consumer --dir build/outbox-example --orders 30 --workers 3 --lease-ms 800
```

macOS에서는 `TOTEDB_LIB`의 확장자를 `.dylib`로 바꾼다. `--dir`에는 예제 전용 경로만 준다. 스크립트는 그 안의 기존 `shop.totedb`를 삭제하므로 보관할 DB 경로를 주지 않는다. 기본 입력은 주문 30건·worker 3개·lease 800밀리초다. 위 명령은 기본값을 명시하며 DB를 `build/outbox-example`에 남긴다.

실행 중 worker 하나를 claim 직후 SIGKILL한다. 나머지 worker가 lease 만료 뒤 해당 주문을 재전달받아 처리한다. 출력 끝의 `[검증]` 행에서 출고 30건·중복 0·누락 0·큐 acked 30과 재전달을 확인하고, 마지막 `검증 통과` 및 종료 코드 0을 확인한다. worker별 처리 건수와 재전달 대상은 실행마다 다를 수 있다.

스크립트는 강제 종료한 worker를 회수하고 나머지 worker가 끝날 때까지 기다린 뒤 DB를 다시 열어 검증하고 종료한다. 정상 완료 뒤 별도 서버 종료 명령은 없다. `make example-outbox`도 같은 스크립트를 실행하지만 `--dir`을 주지 않아 OS 임시 경로를 만든다. 경로를 지정하려면 위 직접 실행 명령을 쓴다. 자동 회귀시험은 `make test-ack-queue-python`에 포함된 [test_outbox_example.py](../tests/test_outbox_example.py)다. README의 ACK 생략 재생과 달리 실제 프로세스를 종료하는 시험이다.

## 다른 엔진과 비교

같은 SQL을 이 엔진, SQLite, PostgreSQL에 실행해 행과 오류 종류를 비교한다. 사례는 `tests/sql_diff_cases.py`에 있다. 엔진마다 의미가 다른 사례는 사례 안에 차이를 적고 `DIFF`로 보고한다.

```sh
make test-sql-diff
make test-sql-diff SQLDIFF_DSN=postgresql://user:password@localhost:5432/sqldiff
```

`SQLDIFF_DSN`이 없으면 PostgreSQL 비교를 건너뛴다. `SQLDIFF_DSN`을 주면 PostgreSQL에 연결하지 못할 때 실패로 끝난다. PostgreSQL 비교에는 Python 패키지 `psycopg`와 `C` 정렬 규칙으로 만든 데이터베이스가 필요하다.

두 연결이 같은 파일을 번갈아 쓰는 동작은 SQLite 3.51.0과 비교한다. 이 대상은 SQLite 소스를 내려받아 sha256을 확인한 뒤 공유 라이브러리로 빌드하므로 네트워크가 필요하다.

```sh
make SANITIZE= BUILD_DIR=build-sqlite test-sqlite-session-diff
```

### 성능 측정

`bench/sqlite_workload.py`는 같은 SQL을 이 엔진과 시스템 SQLite에 주고, 문장마다 결과를 대조하면서 시간과 메모리를 잰다.

측정 항목은 자동 commit 삽입, 기본 키·구간·조건·전체 스캔 조회, 혼합 부하, 강제 종료 뒤 다시 열기다. 측정 조건(seed, 행 수, 반복 수, 내구성 설정)은 `bench/sqlite_workload.json`에 있다. 결과가 하나라도 다르면 실패로 끝난다. 시간 수치는 통과·실패 판정에 쓰지 않는다.

```sh
python3 bench/sqlite_workload.py --smoke
python3 bench/sqlite_workload.py --output build/bench/sqlite_workload.json
python3 bench/sqlite_workload.py --output build/bench/sqlite_workload.json --analyze
```

공식 YCSB 0.17.0은 같은 Java/JNI SQL 바인딩으로 ToteDB와 SQLite에 A~F를 실행한다(2026-10-18 확인). Java 21, Python 3.9 이상, C 컴파일러와 SQLite 개발 헤더가 필요하다. 고정 JAR 다운로드에 네트워크를 사용한다.

```sh
make ycsb-build
make ycsb-test
```

이 시험은 양쪽의 결정적 연결 검사와 공식 Client의 축소 matrix를 검사한다. 성능 합격선은 두지 않는다. 별도 성능 수집과 원자료 재분석은 [바인딩 안내](../bench/ycsb/README.md)에 있다. [YCSB 동등 조건 측정 결과](experiments/ycsb-sqlite/report.md)는 고정한 자동 commit 설정의 240회 비교이며, WAL·준비 문장 캐시를 튜닝한 DB 전체 성능을 뜻하지 않는다.

독립 연결의 큐 쓰기 경합은 [큐 쓰기 경합 결과](experiments/queue-contention/report.md)에 있다. 공식 YCSB와 별개인 enqueue·drain·mixed·outbox 부하를 SQLite DELETE/FULL·WAL/FULL과 비교한다. 연결마다 worker를 하나씩 두며 payload·lease·fence·저장 결과를 대조한다.

```sh
make SANITIZE= all
python3 -m unittest discover -s tests -p test_queue_contention.py -v
python3 bench/queue_contention/run.py verify --output docs/experiments/queue-contention
```

새 실측은 [Linux 계획](experiments/queue-contention/linux-design.md)의 소스와 환경에서 실행한다. 실행 ID는 새로 정하고 기존 자료를 덮어쓰지 않는다. 분석·검증에는 native library가 필요하므로 먼저 빌드한다.

Issue #232의 비용 가설 진단은 이미 빌드한 두 라이브러리를 사용한다. 아래 output은 존재하지 않아야 한다. 빌드를 모두 끝낸 뒤 실행하며 기존 자료를 덮어쓰지 않는다. 정식 비교와 native trace는 별개이며, 비Linux·도구 및 권한 부재는 `unavailable`로 보존한다.

```sh
python3 -m unittest discover -s tests -p test_queue_diagnose.py -v
python3 bench/queue_contention/diagnose.py compare \
  --baseline-lib "$BASELINE_LIB" --candidate-lib "$CANDIDATE_LIB" \
  --output .local/runs/queue-cost-compare --jobs 4096 --repetitions 5 --workers 1,8
python3 bench/queue_contention/diagnose.py trace \
  --library "$CANDIDATE_LIB" --engine totedb \
  --output .local/runs/queue-cost-trace --jobs 4096 --workers 8
```

작은 정확성 smoke는 compare의 `--jobs 12 --repetitions 1 --workers 1`로 실행한다. 성능 비교에 사용하지 않는다. PR에 `queue-cost-experiment` label을 붙이면 선택형 CI가 고정 기준선과 candidate를 먼저 빌드하고 비교·ToteDB/SQLite trace를 직렬 수행한다. 원본 JSON·로그·DB와 라이브러리 해시는 `queue-cost-hypotheses` artifact에 남는다. 조건과 채택 기준은 [Linux 후속 계획](experiments/queue-contention/linux-design.md#issue-232-후속-비용-진단과-전후-비교)에 있다.

TEXT 기본 키 범위 개선의 전후 비교는 [TEXT 색인 구간 순회 결과](experiments/text-range/report.md)에 있다. 고정 소스의 기존 공식 E를 같은 VM에서 전후로 실행하며 [Linux 계획](experiments/text-range/linux-design.md)에 조건과 순서를 둔다.

```sh
RUN_ID=20261019T031416Z-b590d61-check ./docs/experiments/text-range/run.sh verify
RUN_ID=20261019T031416Z-b590d61-check ./docs/experiments/text-range/run.sh analyze
```

## CI

`.github/workflows/ci.yml`은 Ubuntu에서 아래 작업을 실행한다.

| 작업 | 명령 |
|---|---|
| `test` (address, undefined) | `make SANITIZE=<sanitizer> test-all test-runner test-fault test-result` 뒤 별도 `make SANITIZE=<sanitizer> test-benchmark` |
| `test` (thread) | `make SANITIZE=thread test-transaction test-session test-ack-queue` |
| `pg-compat` | PostgreSQL 18 서비스에 `make test-sql-diff`를 실행한 뒤 `make test-sqlite-session-diff` |
| `actionlint` | 워크플로 파일 검사 |
| `queue-contention` | Ubuntu 24.04에서 공개 Python 큐 API·동시 실행·오류 보존·원자료 재현 검사 |
| `ycsb` | Ubuntu 24.04·Java 21에서 `make ycsb-build ycsb-test`, 양쪽 바인딩·공식 Client·원자료 재분석 검사 |

CI의 SQL 비교·워크플로 검사와 릴리스의 Linux 보조 job은 Ubuntu 24.04로 고정한다. sanitizer 시험은 Ubuntu 22.04를 유지한다. 결과 파일 업로드·다운로드 액션은 Node.js 24 런타임을 사용한다.

태그 push와 패키징 파일을 바꾼 PR에서는 `.github/workflows/release.yml`이 실행된다([패키지 배포](#패키지-배포)).

## 패키지 배포

Python 패키지 `totedb`는 PyPI에 올린다. 공유 라이브러리를 ctypes로 열기 때문에 Python 버전과 무관한 `py3-none-{플랫폼}` wheel을 플랫폼마다 하나씩 만든다.

| 파일 | 역할 |
|---|---|
| `pyproject.toml` | 패키지 정보, 버전 출처(`python/totedb/__init__.py`), cibuildwheel 설정 |
| `setup.py` | `make SANITIZE= all`로 만든 `libtotedb`를 패키지 폴더에 넣고 wheel 태그 결정 |
| `MANIFEST.in` | sdist에 C 소스와 `Makefile`을 넣어 설치 때 다시 빌드 |
| `tests/package_smoke.py` | 별도 설치 환경에서 패키지 안의 라이브러리로 DB·큐 사용 확인 |

Python 바인딩의 라이브러리 탐색은 [설정](configuration.md#우선순위)을 따른다.

로컬에서 확인하려면 아래처럼 빌드해 설치한다.

```sh
python3 -m build
python3 -m venv build/package-env
build/package-env/bin/pip install dist/*.whl
TOTEDB_ROOT="$PWD"
cd build
package-env/bin/python "$TOTEDB_ROOT/tests/package_smoke.py"
cd "$TOTEDB_ROOT"
```

### 릴리스 순서

1. `python/totedb/__init__.py`의 `__version__`, `include/totedb.h`의 `TOTE_VERSION`, `CHANGELOG.md`의 `## {버전} - {날짜}` 절을 같은 버전으로 맞춘다.
2. 기본 브랜치에 머지한 뒤 `v{버전}` 태그를 push한다.
3. `.github/workflows/release.yml`이 버전 일치를 검사하고 sdist와 wheel(Linux x86_64·aarch64 manylinux_2_28, macOS arm64 11.0 이상)을 빌드·설치·검사한다.
4. PyPI Trusted Publishing(`pypi` 환경)으로 게시한 뒤 변경 기록 절을 본문으로 GitHub Release를 만들고 배포 파일을 첨부한다.

패키징 파일을 바꾼 PR에서는 같은 워크플로가 3단계까지만 실행한다. 업로드한 배포 파일을 다시 내려받아 `twine check`로 검사한 뒤에만 게시 job을 시작한다. Windows와 macOS x86_64 wheel은 만들지 않는다. macOS x86_64는 sdist로 설치한다.

## macOS에서 ASan 바이너리가 멈추는 문제

macOS 26(Darwin 25)과 Apple clang 17에서는 ASan 바이너리가 `main()`에 닿기 전에 멈춘다. ASan 런타임 초기화가 자기 자신을 다시 부르는 문제이며 빈 `main()`을 `cc -fsanitize=address`로 빌드해도 같은 증상이 나온다. 스택은 `AsanInitInternal`, `InitializeShadowMemory`, `get_dyld_hdr`, `dyld_shared_cache_iterate_text_swift`, `malloc`, `__sanitizer_mz_malloc`를 거쳐 다시 `AsanInitFromRtl`로 들어가 spin lock에서 멈춘다. UBSan만 켠 빌드는 정상 동작한다.

`Makefile`은 Darwin 25 이상에서 `SANITIZE` 기본값을 `undefined`로 낮춘다. ASan을 켠 시험은 Linux나 저장소의 Dev Container(`.devcontainer/`)에서 실행한다.

## README 그림

[README 그림 원본](assets/transaction-queue.diagramplay)과 [DB 실행 관측값](assets/transaction-queue.trace.json)이 현재 입력이다. 도구 기준은 [DiagramPlay `0f35b9b`](https://github.com/woonyong-choi/diagramplay/tree/0f35b9b1c400355e769f8302a3f3f348a07d69f5)과 Node.js 26.9.0이다. `DIAGRAMPLAY_DIR`은 해당 커밋의 준비된 도구 작업본을 가리킨다. 원본 검사만 하려면 ToteDB 루트에서 아래 명령을 실행한다.

```sh
node "$DIAGRAMPLAY_DIR/src/cli.js" check docs/assets/transaction-queue.diagramplay --strict
```

README는 화면 폭에 따라 움직이는 [기본 SVG](assets/transaction-queue.svg)와 [좁은 SVG](assets/transaction-queue-narrow.svg)를 고른다. 시나리오는 [넓은 HTML 재생 화면](assets/transaction-queue.html)과 [좁은 HTML 재생 화면](assets/transaction-queue-narrow.html)에서 선택한다. SVG와 HTML은 서로 다른 생성물이며, 원본의 장면 값과 trace 관측값을 함께 대조한다. `.torso`와 Torso `14e57b7`의 `--static` 명령은 현재 움직이는 SVG의 재생성 절차가 아니므로 사용하지 않는다.

GitHub의 HTML 링크는 재생 화면이 아니라 소스보기다. 장면을 선택하려면 저장소를 내려받은 뒤 `docs/assets/transaction-queue.html` 또는 `transaction-queue-narrow.html`을 로컬 브라우저에서 연다. `storage-publication`과 `queue-lifecycle`의 기본·좁은 HTML도 같은 방식으로 연다. GitHub에서 움직이는 SVG가 보이는 것과 HTML의 장면 선택 기능은 구분한다.

재생성은 ToteDB 루트에서 `node tools/render-figures.mjs`로 한다. 큐 2개는 `buildFigure(source, {strict: true, baseDir})`의 기본 폭으로 빌드하고 `toSvg(result, {name})`·`toHtml(result, name)`으로 내보낸다. 좁은 배치는 고정 도구의 내부 `src/build.js`에 있는 `reflowFigure`로 같은 기준 시간표를 유지한다. 원본의 장면 값을 trace와 대조하고 정지 출력으로 덮어쓰지 않는다. 7개 원본과 22개 생성물의 정확한 옵션·환경·바이트 대조는 [아키텍처의 재생성](architecture.md#그림-원본과-재생성)을 따른다.
