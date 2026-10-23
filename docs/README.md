# 문서

ToteDB를 사용하거나 내부 구현을 수정할 때 참고하는 공개 문서다. 문서는 한국어로 쓴다. 처음 사용한다면 메인 README에서 설치와 사용 예를 보고, 구현을 바꾸려면 아키텍처에서 전체 구조를 먼저 읽는다.

## 사용과 API

- [메인 README](../README.md): 제품 소개, 설치, SQL·큐 사용 예와 대안 비교
- [데이터](data.md): SQL 값·바인딩, 파일 형식, 큐 테이블과 보관 규칙
- [설정](configuration.md): API 옵션의 기본값·한도와 C·Python의 차이
- [오류](errors.md): 반환 상태·예외의 구분과 재시도·rollback·재개방 판단
- [프로토콜](protocol.md): C 결과 버퍼의 바이트 형식과 소유권

## 내부 구조와 큐 설계

- [아키텍처](architecture.md): 구성 요소, 저장 참조, 트랜잭션 게시·복구와 잠금·회수 규칙
- [주문 저장과 작업 등록을 한 트랜잭션으로 처리하기](design/transaction-queue.md): 공동 commit·rollback, lease·fence와 DB 밖 작업의 보장 경계

## 빌드와 검증 근거

- [빌드와 시험](build-and-test.md): 소스 빌드, 시험 대상·CI 로그, Outbox 예제와 그림 재생
- [안전성 시험 결과](safety.md): 프로세스 종료·I/O 실패의 관측 결과와 미검증 범위
- [실험](experiments/README.md): 성능 비교·개선 검증의 결론, 계획·조건·원자료와 재현 방법

## 기여와 출처

- [기여 안내](../.github/CONTRIBUTING.md): 변경 제안과 PR 제출 전 검사
- [C 코딩 규칙](convention/c-style.md): C 코어·헤더의 구현 규칙
- [Python 코딩 규칙](convention/python-style.md): 바인딩과 Python 도구의 구현 규칙
- [폴더 구조 규칙](convention/project-structure.md): 구현·시험·문서·산출물의 배치
- [출처와 기여 경계](attribution.md): 현재 코드·외부 구성 요소와 옛 팀 기여의 구분, 참고·재사용 이력의 미확인 범위
- [개발 선택과 검증의 기록](development-report.md): 초기 원형부터 제품·저장·SQL·큐·실험 선택의 이유와 검증·철회·미해결
