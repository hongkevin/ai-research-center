"""pipeline — S1~S6 오케스트레이션 (ARCHITECTURE.md §4.1).

S1 수집·정규화(코드+Haiku급) → S2 재무분석(Sonnet급) → S3 추정·밸류에이션
(결정적 finmodel) → S4 섹션 병렬 작성(Sonnet급×N, thesis.json 주입) →
S5 검증 게이트(G0/G1/G2) → S6 렌더링(Jinja2→HTML/PDF) → 인간 검토·발간.

원칙:
  - 비실시간 생성은 Batch API(50% 할인) 기본, 반복 컨텍스트는 prompt caching.
  - S5 실패 시 S4로 반려. 재현성·감사 가능성 우선 — 각 단계 입출력을 기록한다.

TODO(3~6주차): s1_ingest.py ~ s6_render.py 단계 모듈 + orchestrator.py
"""
