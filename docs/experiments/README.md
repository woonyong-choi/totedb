# 실험

각 보고서에서 비교 버전·내구성·부하·표본 조건, 원자료와 재현 방법을 확인한다. 가설 번호는 보고서의 판정과 대응하며, 가설 채택이 항상 성능 개선을 뜻하지는 않는다. 엔진의 저장·동시성 구조는 [아키텍처](../architecture.md)에 있다.

| 실험 | 확인할 질문 | 실제 결론 |
|---|---|---|
| [ycsb-sqlite](ycsb-sqlite/report.md) | 공식 YCSB에서 ToteDB와 SQLite의 사용 경로를 비교하면 어떤 차이가 있는가 | E에서는 ToteDB가 느리다는 예측을 지지했다(H2 채택). 네 조건에서 처리량 비율(ToteDB/SQLite)의 구간 상한이 <0.8이었다. 정상 실행은 240/240회 통과했다(H1 채택). |
| [queue-contention](queue-contention/report.md) | 독립 연결의 동시 생산·소비와 업무 행·큐 공동 commit은 worker 증가에 따라 어떻게 달라지는가 | ToteDB의 mixed worker 8 처리량이 worker 1의 두 배 미만이라는 예측을 지지했다(H2 채택). 관측된 worker 8/1 비율은 0.812·0.761로 모두 1 미만이었다. Linux 정상 실행은 360회 통과했다(H1 채택). |
| [text-range](text-range/report.md) | TEXT 기본 키 구간 순회 경로를 바꾸면 기존 경로보다 개선되는가 | Linux 전후 비교에서 처리량 개선을 확인했다. 대응 개선 비율은 47.867 [46.313, 49.043]이며 20회 통과했다(H1·H2 채택). Mac은 부분 실패로 별도 구분한다. |
| [release-cost](release-cost/report.md) | native·YCSB·큐 비용과 원인 가설을 계측·비계측으로 나눠 어떻게 판단하는가 | 실행·계측·배포 경계의 검증을 통과했다(H1~H3 채택). 비계측 390회·별도 진단 30회이며, 상대 성능은 업무·저장 설정에 따라 달랐다. 전체 성능 우위 판정은 아니다. |
| [local-db-comparison](local-db-comparison/report.md) | 대표 로컬 DB와 실제 ACK 큐에 비해 어떤 이점과 한계가 있는가 | DB 180회·큐 100회의 정상 실행·재개방·설정 검증을 통과했다(H1~H3 채택). 이 조건에서 실제 ACK 큐 대비 ToteDB의 속도 우위는 확인하지 못했다. |
