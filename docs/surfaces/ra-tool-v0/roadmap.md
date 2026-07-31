> ⚠️ **보류 문서 (v0).** 2026-07-30 작성. 전제가 **"증권사 RA용 유료 B2B SaaS"**입니다.
>
> 2026-08-01 결정 D8에 따라 제품은 **공통 엔진 + 표면 2개**로 재정의됐고, **발간 표면을 먼저** 만듭니다.
> 이 문서군은 RA 도구 표면의 v0 설계로, [research/04-reshape-interview.md](../../research/04-reshape-interview.md)
> §7의 가설 H1이 RA 인터뷰로 검증된 뒤에 되살립니다.
>
> 이 문서의 전제 중 이미 반박된 것: 해외 60%가 SEC 밖 · 리포트는 재무제표가 아닌 산업/정책 서사에서 출발
> · 추정이 초안보다 우선. 자세한 내용은 [decisions.md](../../decisions.md) 참조.

# 실행 로드맵 — AI Research Center

각 Phase는 **종료 조건(무엇이 되면 다음으로 가는가)**과 **중단 조건(무엇이면 멈추고 재검토하는가)**을 가집니다. 기간은 명시하지 않습니다 — 1인 개발 초기에 기간 약속은 의미가 없고, 게이트만 의미가 있습니다.

---

## Phase 0 — 골격과 결정론 계약 보존

**목표**: 기존 4개 도구를 웹 서비스 코드 경로로 옮기면서 **계산 결과가 한 바이트도 바뀌지 않았음**을 증명한다.

UI는 없습니다. 이 Phase의 산출물은 "믿을 수 있는 계산 코어"입니다.

### 작업

1. `src/tools/*.py`를 패키지로 재구성 ([01-ARCHITECTURE.md](../../surfaces/ra-tool-v0/architecture.md) §2 매핑표대로)
   - `core/metrics.py` · `core/packet.py` · `core/board.py`
   - `adapters/sec/` — **fetch와 build 분리** (유일한 구조적 리팩터)
   - `compliance/precheck.py`
2. `CanonicalFacts` 스키마 v2.0.0 — `source` discriminator + `source_ref` 일반화
3. FastAPI 라우터 최소 세트: `/facts/resolve`, `/facts/snapshots`, `/precheck/inline`
4. 회귀 테스트를 새 코드 경로로 이관
5. Postgres 스키마 + RLS 정책. Redis 토큰 버킷 (SEC 10 req/s)

### 종료 조건

| # | 조건 | 검증 방법 |
|---|---|---|
| 0-1 | 회귀 테스트 10개가 새 코드 경로에서 통과 | `python3 -m unittest discover -s tests` |
| 0-2 | fixture 8종목이 리팩터 전과 **동일 JSON** 산출 | 기존 `src/examples/outputs/`와 바이트 비교 |
| 0-3 | `generated_at` 주입이 유지됨 | 동일 입력 + 동일 `generated_at` → 동일 출력 |
| 0-4 | SEC live fetch가 rate limit 위반 없이 동작 | 동시 요청 20건에서 429 없음 |
| 0-5 | RLS가 테넌트 격리를 강제 | 테넌트 A 세션으로 B 데이터 조회 시도 → 0행 |

**0-2가 이 Phase의 핵심입니다.** 웹으로 옮기면서 계산이 미묘하게 달라지면 그 뒤 모든 신뢰가 무너집니다.

### 중단 조건

- 0-2가 통과하지 않고 원인이 리팩터 설계 문제라면 → 매핑표 재검토

---

## Phase 1 — MVP: 초안 에디터

**목표**: RA 1명이 실제 리포트 초안 1건을 이 도구만으로 완주한다.

### 작업

**Fact Layer (SEC)**
- 스냅샷 생성·조회·엑셀 내보내기
- fact ID 부여 (원시 9 + 계산 11 + 브리지)
- fact 카탈로그 API (LLM 프롬프트용, 값 제외)

**Numeric Token Binding** ← 최우선
- 토큰 문법 계약 + 시스템 프롬프트
- 4단 검증 게이트 (ID 검증 / 리터럴 스캔 / 의견 스캔 / 렌더링)
- 리터럴 화이트리스트
- `fact_binding` 기록
- 최대 2회 재생성 + 실패 시 사용자 노출

**LLM 서술 레이어**
- `claude-opus-5` + adaptive thinking + effort high
- 2단 스키마 (LLM 출력용 단순 / 정식 검증용 엄격)
- 프롬프트 캐싱 (안정 프리픽스 배치 + `cache_read_input_tokens` 관측)
- SSE 스트리밍

**화면**
- 워크스페이스 홈 · 종목 팩트 뷰 · 초안 에디터 · 의견 입력 모달
- Fact Chip + Source Popover
- 우측 인라인 프리체크 (6개 룰, 문장 단위)

**의견 경계**
- `POST /drafts/{id}/opinion` — `role in (analyst, admin)`
- 스키마 + API 이중 차단
- 감사 로그 기록

### 종료 조건

| # | 조건 | 검증 방법 |
|---|---|---|
| 1-1 | **미바인딩 숫자 리터럴 렌더링 0건** | 초안 50건 생성 후 렌더링 결과 전수 스캔 |
| 1-2 | LLM 경로의 의견 필드 쓰기 시도 0건 | 스키마 거부 + API 로그 |
| 1-3 | 게이트 1차 통과율 > 90% | 초안 50건 통계 |
| 1-4 | 프롬프트 캐시 히트율 > 50% | `cache_read_input_tokens > 0` 비율 |
| 1-5 | Design partner RA가 리포트 초안 1건 완주 | 관찰 세션 + 인터뷰 |
| 1-6 | 초안 1건 소요 시간 측정 완료 | **베이스라인과 함께 측정** (O3) |
| 1-7 | fixture 8종목 + live fetch 종목 모두 정상 동작 | JPM `limited` 케이스 포함 |

**1-1이 P0입니다.** 1건이라도 유출되면 제품의 유일한 차별점이 무너집니다. 종료 조건을 완화하지 않습니다.

**1-5와 1-6이 상업적 게이트입니다.** RA가 완주하지 못하거나 시간이 단축되지 않으면 기능을 더 만들 게 아니라 문제 정의를 다시 봐야 합니다.

### 중단 조건

- 1-3이 70% 미만이고 프롬프트 튜닝으로 개선되지 않으면 → 토큰 바인딩 방식 재설계 (예: 값을 프롬프트에서 완전히 제거)
- 1-6에서 시간 단축이 20% 미만이면 → **Phase 2로 진행하지 말고** 문제 정의 재검토. 기능 추가로 해결되지 않는 문제

### 선행 필요 (O1~O3)

- O1 제품명 확정
- O2 Design partner 3곳 접촉 — Phase 1 착수와 병행
- O3 베이스라인 측정 방법 합의 — 1-6의 전제

---

## Phase 2 — DART + 컴플라이언스 전체 + 감사

**목표**: 국내 커버리지를 열고, 팀 계약이 가능한 상태를 만든다.

Phase 1의 1-5·1-6이 통과한 뒤에만 착수합니다. 제품 검증 없이 커버리지를 넓히면 매핑 지옥에 시간을 쓰고 아무것도 검증하지 못합니다.

### 작업

**DART 어댑터** ← 최대 리스크
- corp_code 매핑 · `fnlttSinglAcntAll` 파싱
- `dart_account_mapping` 테이블 (코드가 아닌 데이터로 관리)
- 2단 매핑: `account_id` 우선 → `account_nm` 정규화 폴백
- 미매핑은 `omitted[]` + `coverage: limited` (추정 금지)

**프리체크 전체**
- 전체 리포트 화면 (법령 매핑 상세 + 심각도)
- `precheck_rule_version` 테이블 + 테넌트별 오버라이드
- 심사 요청 문서 내보내기

**감사 로그**
- `/admin/audit` 화면
- **초안 숫자 역추적 뷰** (`fact_binding` 기반) ← 조달 심사의 핵심 카드
- CSV 내보내기

**초안 버전 비교**

### 종료 조건

| # | 조건 | 검증 방법 |
|---|---|---|
| 2-1 | KOSPI200 중 `coverage: full` 비율 > 80% | 200종목 배치 실행 |
| 2-2 | DART 매핑이 원시 지표 9개를 커버 | 표본 30종목 수동 대조 |
| 2-3 | 국내 종목 초안에서 미바인딩 숫자 0건 | 초안 30건 전수 스캔 |
| 2-4 | 감사 로그에서 임의 초안의 모든 숫자 역추적 가능 | 무작위 5건 추적 시연 |
| 2-5 | 준법감시인 1명이 심사 요청 문서로 실제 심사 진행 | 관찰 |

**2-1이 게이트입니다.** 80% 미만이면 국내 시장에 "쓸 수 있는 도구"가 아닙니다.

### 중단 조건

- 2-1이 60% 미만이고 매핑 테이블 확장으로 개선 속도가 나지 않으면 → DART를 **부가 기능으로 격하**하고 해외주식 전문 도구로 포지셔닝 재조정. 이건 실패가 아니라 wedge 재확인

---

## Phase 3 — 팀·엔터프라이즈 확장

**목표**: 증권사 전사 계약을 받을 수 있는 상태.

### 작업

- 팀 워크스페이스 (공유 커버리지, 리뷰 플로우)
- 테마 보드 (`core/board.py` 화면화 — 거의 무수정 재사용)
- 차트 자동 생성 (`chart_brief` → 실제 렌더링)
- SSO (SAML/OIDC)
- VPC 리사이징 — single-tenant 배포
- 사내 문체 학습 (테넌트별 few-shot)

### 종료 조건

| # | 조건 |
|---|---|
| 3-1 | 동일 코드베이스로 single-tenant 배포 성공 (코드 변경 0) |
| 3-2 | 증권사 보안 심사 통과 1건 |
| 3-3 | Enterprise 계약 1건 |

### VPC 전환의 사전 제약 (Phase 1부터 지킬 것)

Phase 3 비용을 없애기 위해 처음부터 지키는 규칙:

- **Managed Agents 사용 금지** — Bedrock/Vertex 미지원
- **프롬프트 자동 캐싱(top-level `cache_control`) 사용 금지** — Bedrock/Vertex 미지원. 수동 블록 단위 `cache_control`만 사용
- **Batches API 사용 금지** — Bedrock/Vertex 미지원
- Messages API + 수동 캐싱만으로 구현

이 제약을 Phase 1에서 어기면 Phase 3에서 LLM 레이어를 재작성해야 합니다.

---

## 리스크 게이트 요약

| Phase | 최대 리스크 | 게이트 | 실패 시 |
|---|---|---|---|
| 0 | 리팩터가 계산을 바꿈 | 0-2 바이트 비교 | 매핑표 재검토 |
| 1 | 숫자 환각 유출 | 1-1 **0건** | 토큰 바인딩 재설계 |
| 1 | 시간 단축이 없음 | 1-6 > 20% | **문제 정의 재검토** (기능 추가 금지) |
| 2 | DART 매핑 커버리지 | 2-1 > 80% | 해외주식 전문으로 재포지셔닝 |
| 3 | 조달 사이클 | 3-2 보안 심사 | seat 기반 bottom-up 유지 |

---

## 오픈 아이템과 해소 시점

[00-PRD.md](../../surfaces/ra-tool-v0/prd.md) §11의 항목들:

| # | 항목 | 해소 시점 | 미해소 시 영향 |
|---|---|---|---|
| O1 | 제품명 확정 | Phase 1 착수 전 | 브랜딩·도메인 |
| O2 | Design partner 3곳 | Phase 1 착수와 병행 | 1-5, 1-6 검증 불가 |
| O3 | 베이스라인 측정 방법 | Phase 1 초 | 1-6 판정 불가 → **가치 증명 불가** |
| O4 | 데이터 보존 정책 정합성 | Phase 1 중 | Enterprise 논의 차단 |
| O5 | 국내 RA 인원 실측 | Phase 2 전 | TAM·가격 근거 부재 |
| O6 | DART 커버리지 목표치 | Phase 2 착수 전 | 2-1 게이트 설정 불가 |

**O3가 가장 급합니다.** 베이스라인 없이는 "50% 단축"을 주장할 수 없고, 그러면 가격 근거([00-PRD.md](../../surfaces/ra-tool-v0/prd.md) §9)가 무너집니다.

---

## 다음 액션

1. O1 제품명 결정
2. O2 design partner 후보 리스트 작성 + 접촉
3. Phase 0 착수 — `src/tools/*.py` 리팩터 (매핑표는 [01-ARCHITECTURE.md](../../surfaces/ra-tool-v0/architecture.md) §2)

---

## 참고 문서

- [00-PRD.md](../../surfaces/ra-tool-v0/prd.md) — 제품 기획
- [01-ARCHITECTURE.md](../../surfaces/ra-tool-v0/architecture.md) — 기술 설계
- [02-SCREENS.md](../../surfaces/ra-tool-v0/screens.md) — 화면 설계
