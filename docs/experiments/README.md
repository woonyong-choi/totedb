# 실험

| 실험 | 확인할 것 | 관련 설계 | 결론 |
|---|---|---|---|
| [ycsb-sqlite](ycsb-sqlite/report.md) | 공식 YCSB에서 ToteDB와 SQLite의 동등 조건 실행 | [아키텍처](../architecture.md) | H1 채택: 240/240회 통과. H2 채택: E 네 조건의 비율 구간 상한 <0.8 |
| [queue-contention](queue-contention/report.md) | 독립 연결의 동시 생산·소비와 업무 행·큐의 함께 commit | [아키텍처](../architecture.md) | Linux H1 채택: 360회 통과. H2 채택: mixed worker 8/1 비율 0.812·0.761 |
| [text-range](text-range/report.md) | TEXT 기본 키 구간 순회의 공식 E 전후 변화 | [아키텍처](../architecture.md) | Linux H1·H2 채택: 20회 통과, 대응 개선 비율 47.867 [46.313, 49.043]. Mac은 부분 실패 |
