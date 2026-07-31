# 문서 지도

코스닥 미커버 종목의 **실적 리뷰 노트를 자동 생성**하는 세미오토 시스템. AI가 초안을 만들고 사람이 검토한 뒤 발간한다. 모든 수치는 결정적 코드가 계산하고 산식·출처를 전면 공개한다.

## 읽는 순서

| # | 문서 | 내용 |
|---|---|---|
| 1 | **[decisions.md](decisions.md)** | **현재 유효한 결정의 단일 원천.** 여기서 시작 |
| 2 | [ARCHITECTURE.md](ARCHITECTURE.md) | 시스템 설계 — S1~S6 파이프라인, Number Registry, G0 게이트, 데이터 레이어 |
| 3 | [design-brief-v1.md](design-brief-v1.md) | 근거 조사 종합 (2026-07-25) — 규제·경쟁·데이터 라이선스·아키텍처 대안 비교 |
| 4 | [competitive-landscape.md](competitive-landscape.md) | 경쟁 실사 원문 — KOSAI·한국IR협의회 |

## 분석 자산 — [research/](research/)

2026-07-30~31 작성. **제품 방향과 무관하게 유효**하며, 결정 D11~D13의 근거다.

| 문서 | 핵심 발견 |
|---|---|
| [01-benchmark-smic.md](research/01-benchmark-smic.md) | SMIC 리포트 15건 역설계. 추정 15/15건 · 세그먼트 15/15건 · 해외 60%가 SEC 밖 · 리포트는 재무제표에서 출발하지 않음 |
| [02-report-quality.md](research/02-report-quality.md) | FnGuide 채점 루브릭(40점=추정 정확도) × 구조 분석 교차. 매수 편향 대응 전략(C안) |
| [03-valuation-boundary.md](research/03-valuation-boundary.md) | 계산과 판단의 경계. PBR 분해 항등식. 밴드는 가격 시계열에 블로킹 |
| [04-reshape-interview.md](research/04-reshape-interview.md) | 처리량 제약 분석 + RA 인터뷰 설계(가설 H1~H5) |

## 보류 — [surfaces/ra-tool-v0/](surfaces/ra-tool-v0/)

증권사 RA용 유료 B2B SaaS 설계 v0 (2026-07-30). 결정 [D8](decisions.md#d8)에 따라 **발간 표면을 먼저** 만들기로 하면서 보류됐다. RA 인터뷰로 가설 H1이 검증되면 되살린다.

전제 중 이미 반박된 것이 있으므로 그대로 읽지 말 것 — [decisions.md](decisions.md) 참조.

## 현재 상태

| 영역 | 상태 |
|---|---|
| 데이터 레이어 (DART·금융위시세·네이버뉴스·DuckDB point-in-time) | ✅ 구현, 26 테스트 통과 |
| Number Registry · G0 게이트 | ⏳ 인터페이스만. **구현은 kakaopay-hackathon/mvp/에 있고 이관 대기** ([D12](decisions.md#d12)) |
| finmodel (추정·멀티플·시나리오) | ⏳ 스텁 |
| pipeline S1~S6 · render | ⏳ 스텁 |

다음 작업은 [decisions.md](decisions.md) §D12의 "첫 구현 태스크".
