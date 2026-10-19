# 실험

| 실험 | 확인할 것 | 관련 설계 | 결론 |
|---|---|---|---|
| [ycsb-sqlite](ycsb-sqlite/report.md) | 공식 YCSB에서 ToteDB와 SQLite의 동등 조건 실행 | [아키텍처](../architecture.md) | H1 채택: 240/240회 통과. H2 채택: E 네 조건의 비율 구간 상한 <0.8 |
| [queue-contention](queue-contention/report.md) | 독립 연결의 동시 생산·소비와 업무 행·큐의 함께 commit | [아키텍처](../architecture.md) | Linux H1 채택: 360회 통과. H2 채택: mixed worker 8/1 비율 0.812·0.761 |
| [text-range](text-range/report.md) | TEXT 기본 키 구간 순회의 공식 E 전후 변화 | [아키텍처](../architecture.md) | Linux H1·H2 채택: 20회 통과, 대응 개선 비율 47.867 [46.313, 49.043]. Mac은 부분 실패 |
| [release-cost](release-cost/report.md) | 배포판의 native·YCSB·큐 비용과 원인 가설의 판단 흐름 | [아키텍처](../architecture.md) | H1~H3 채택: 비계측 390회·별도 진단 30회 통과, 상대 성능은 업무·저장 설정에 따라 다름 |
| [local-db-comparison](local-db-comparison/report.md) | 대표 로컬 DB와 실제 SQLite 기반 Python 큐 비교 | [아키텍처](../architecture.md) | H1~H3 채택: DB 180회·큐 100회 통과, 실제 ACK 큐 대비 ToteDB 속도 우위 없음 |
