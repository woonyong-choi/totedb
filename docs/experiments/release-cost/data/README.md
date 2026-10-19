# 데이터

## 출처

| 항목 | 값 |
|---|---|
| 수집 방법 | 고정 제품 소스의 native driver·공식 YCSB Client·기존 큐 수집기, 독립 Linux VM 직렬 실행 |
| 수집 기간 | 2026-10-19~2026-10-19 |
| 개수 | 수정 수집 정식 420회·사전 84회, 초기 중단 native 정식 60회·사전 12회는 별도 |
| 표본 여부 | 계획 matrix 전수, 조건별 반복 5회 |
| 라벨 | workload·engine·mode·phase·repetition을 수집기가 기록 |
| 알려진 문제 | 첫 실행 중단·작은 표본·공유 VM·계측 교란, 보고서 참조 |
| 개인정보 | 합성 데이터, runner 환경·경로·프로세스 식별자는 원자료에 포함 |
| 라이선스 | 저장소 LICENSE, 공식 YCSB의 라이선스·출처 고지 유지 |

## 파일

| 파일 | 내용 | 만드는 도구 |
|---|---|---|
| results/summary.json | 원자료 재계산 native와 기존 YCSB·큐 요약 전체 | suite.py analyze |
| results/provenance.json | CI·소스·artifact ID와 digest·초기 중단의 분모 | GitHub API와 수집 manifest 대조 |
| env.json | 수집 전후 환경 원본 | suite.py collect |
| data/build.json | 실제 빌드 명령·심볼 대조·binary 해시·flag | suite.py collect |
| data/external-files.json | 별도 DB root의 존재·파일 목록·SHA-256 | suite.py collect |
| data/SHA256SUMS | artifact 원자료의 해시 목록 | suite.py collect |
| artifact의 data/native-db | 성공·실패 native DB | counter_driver |
| artifact의 logs | native 원본 JSON stdout·stderr·spec·명령·하위 도구 로그 | suite.py collect |
| artifact의 ycsb·queue | 하위 수집기의 원본·audit·환경·해시 | 기존 수집기 |

표의 공개 파일 경로는 실험 폴더 기준이다. 공개 요약·환경·해시 파일은 원본에서 옮긴 생성물이며 직접 수치 교정하지 않는다. 처리량·지연·IO 원값과 모든 반복은 summary에 남는다. 원자료를 바꾸지 않고 재생성하려면 보고서의 verify/analyze 명령을 사용한다.

## 원자료 보관

수정 수집 [CI 39236890565](https://github.com/woonyong-choi/totedb/actions/runs/39236890565)의 `release-cost-evidence` artifact를 사용한다. ID는 12179063130이고 digest는 `sha256:0613bc26697e994b71e9883f0252ddb7c3e6cab2459d7b0d3dfed04993efe0c0`이다. GitHub artifact의 만료 예정일은 2027-01-17이다. 정본 작업 환경에도 원본을 별도 보존했으며 공개 Git의 요약만으로 원본 DB를 대신하지 않는다.

초기 중단은 [CI 39236213098](https://github.com/woonyong-choi/totedb/actions/runs/39236213098)의 별도 artifact에 있다. 수정 수집과 합산하지 않는다. 성공한 YCSB trial DB의 삭제는 기존 도구 정책이고, 보존된 audit와 baseline·실패 DB의 존재 여부를 구분한다.

## 필드

| 필드 | 타입 | 단위 | 제약 | 뜻 | 예 |
|---|---|---|---|---|---|
| native runs의 n·mode·engine·repetition | integer·string | 회·없음 | 조건·반복 고유 | 실행 조건과 대응 관계 | 1024·off·tote·1 |
| native ops_s | number | UPDATE/s | 양수 | 실제 완료량/타이머 합 | raw에서 재계산 |
| native phases | object | ns·byte·회 | ON ToteDB만 존재 | 단계별 시간·호출량·성공 바이트 | sync·write |
| native comparisons values | array | 비율 | 대응 5개 | 중앙값과 bootstrap의 원래 비율 | 같은 n·반복 |
| axes ycsb·queue original_summary | object | 지표별 상이 | 기존 검증기 통과 | 기존 부하별 전체 집계 | 조건·처리량·지연·audit |
| incomplete_or_failed | array | 없음 | 정상은 빈 배열 | 누락·비교 실패한 축 | 원인 목록 |

시간이 없는 단계·SQLite 내부 비용은 결측 또는 관측 범위 밖이지 비용 0이라는 주장이 아니다. ON/OFF와 서로 다른 부하의 결과를 합산한 종합 점수를 만들지 않는다.
