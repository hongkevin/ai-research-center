> ⚠️ **보류 문서 (v0).** 2026-07-30 작성. 전제가 **"증권사 RA용 유료 B2B SaaS"**입니다.
>
> 2026-08-01 결정 D8에 따라 제품은 **공통 엔진 + 표면 2개**로 재정의됐고, **발간 표면을 먼저** 만듭니다.
> 이 문서군은 RA 도구 표면의 v0 설계로, [research/04-reshape-interview.md](../../research/04-reshape-interview.md)
> §7의 가설 H1이 RA 인터뷰로 검증된 뒤에 되살립니다.
>
> 이 문서의 전제 중 이미 반박된 것: 해외 60%가 SEC 밖 · 리포트는 재무제표가 아닌 산업/정책 서사에서 출발
> · 추정이 초안보다 우선. 자세한 내용은 [decisions.md](../../decisions.md) 참조.

# 기술 설계서 — AI Research Center

전제: [00-PRD.md](../../surfaces/ra-tool-v0/prd.md)의 제품 원칙 4개를 코드 수준 불변식으로 옮기는 것이 이 설계의 목표입니다.

## 1. 시스템 구성

```
┌──────────────────────────────────────────────────────────┐
│  Next.js 15 (App Router, TypeScript, Tailwind)           │
│  워크스페이스 · 팩트 뷰 · 초안 에디터 · 프리체크 · 감사   │
└────────────────────────┬─────────────────────────────────┘
                         │ REST + SSE (초안 스트리밍)
┌────────────────────────┴─────────────────────────────────┐
│  FastAPI (Python 3.12)                                   │
│                                                          │
│  api/         라우터 · 인증 · 테넌트 컨텍스트 주입        │
│  adapters/    sec/ · dart/     ← 소스별 fetch + 정규화    │
│  core/        metrics · packet · board                    │
│               ↑ 소스 무관 결정론 계산 레이어 (기존 코드)   │
│  narrative/   Claude 호출 · 토큰 바인딩 · 검증 게이트     │
│  compliance/  프리체크 · 룰 버저닝                        │
└──┬──────────────┬──────────────┬────────────────────────┘
   │              │              │
┌──┴────────┐ ┌───┴──────┐ ┌────┴──────────┐
│ Postgres  │ │  Redis   │ │ S3 호환        │
│ RLS 격리  │ │ fetch캐시│ │ 스냅샷·문서    │
│           │ │ ratelimit│ │                │
└───────────┘ └──────────┘ └───────────────┘
                         │
                  ┌──────┴──────┐
                  │ Anthropic   │  claude-opus-5
                  │ Claude API  │
                  └─────────────┘
```

### 레이어 경계 규칙

| 레이어 | 결정론 | LLM 접근 | 비고 |
|---|---|---|---|
| `adapters/` | ✅ (동일 공시 → 동일 결과) | ❌ | 공시는 불변이므로 캐시 영구 |
| `core/` | ✅ | ❌ | **LLM이 이 레이어를 호출하는 경로가 없어야 함** |
| `narrative/` | ❌ | ✅ | 유일한 LLM 경로. 출력은 반드시 검증 게이트 통과 |
| `compliance/` | ✅ | ❌ | 정규식 룰. LLM 판정 없음 |

`core/`가 결정론적이라는 것이 원칙 1의 기술적 기반입니다. 숫자는 항상 이 레이어에서만 나옵니다.

---

## 2. 기존 코드 재사용 매핑

해커톤 자산 4개 도구는 이미 순수 함수 + CLI 껍데기 구조입니다. `main(argv)`만 버리고 함수를 그대로 import합니다.

### `core/metrics.py` ← `edgar_financials.py`

소스 무관 계산 레이어. **거의 무수정 이관.**

| 함수 | 위치 | 역할 |
|---|---|---|
| `build_calculations` | :403 | 마진·YoY·원가율·R&D 강도 계산 + `formula` 생성 |
| `build_margin_bridges` | :816 | 연도별 마진 브리지 |
| `build_margin_bridge_for_year` | :643 | 영업이익률 변화를 원가율/implied 영업비용률로 분해 |
| `bridge_component` | :552 | 브리지 구성요소 + 기여도 |
| `supporting_driver` | :577 | R&D·SG&A 보조 지표 |
| `implied_operating_expense_ratio` | :540 | 역산 영업비용률 |
| `build_ratio_calculation` / `build_yoy_calculation` / `build_formula_calculation` | :331 / :356 / :380 | 계산 빌더 |
| `format_value` / `format_pp` | :279 / :295 | 표시값 포맷 |
| `ratio_value` / `fact_for_year` | :534 / :526 | 조회 유틸 |

### `adapters/sec/` ← `edgar_financials.py`

fetch와 build를 분리합니다. **이게 유일한 구조적 리팩터**입니다 — DART 어댑터를 붙이려면 필요합니다.

| 함수 | 위치 | 역할 |
|---|---|---|
| `fetch_json` | :145 | SEC HTTP 호출 (`pause_seconds=0.12`로 rate limit 준수) |
| `lookup_company` | :172 | 티커 → CIK |
| `collect_metric_facts` | :251 | Company Facts → 지표별 fact 추출 |
| `source_for` | :219 | accession / tag / filing URL 부착 |
| `is_supported_fact` / `sort_fact_candidates` | :195 / :207 | 폼 필터 + 후보 우선순위 |
| `MetricSpec` (dataclass) | :33 | 지표 → XBRL 태그 매핑 |
| `cik10` / `filing_url` | :134 / :138 | 식별자 정규화 |

### `core/packet.py` ← `single_stock_packet.py`

**역할이 바뀝니다.** 기존에는 최종 산출물이었지만, 이제 **LLM이 채울 골격 생성기**가 됩니다.

| 함수 | 위치 | 새 역할 |
|---|---|---|
| `build_packet` | :537 | 골격 전체 조립 → LLM 프롬프트의 구조 입력 |
| `margin_angle` / `growth_vs_margin_angle` / `profit_transition_angle` / `expense_mix_angle` / `limited_coverage_angle` | :179~:310 | 앵글 후보 → 사용자가 선택하는 목록 |
| `fact_anchor_from_evidence` / `bridge_anchor` / `transition_anchor` | :45 / :60 / :76 | **fact 앵커 → 토큰 ID의 원천** |
| `avoid_overclaims` | :376 | LLM 시스템 프롬프트의 금지 목록 |
| `chart_brief` | :456 | 차트 브리프 (Phase 3에서 실제 렌더링) |
| `top_signal` / `today_one_liner` / `title_candidates` | :98 / :116 / :152 | 골격 헤더 |

### `core/board.py` ← `editorial_board.py`

Phase 3용. **무수정 이관 가능.**

`build_board` (:639), `build_security_profile` (:240), `build_scope_and_universe` (:349), `build_core_operating_metrics` (:382), `build_operating_comps_snapshot` (:428), `build_landscape` (:477), `build_shortlist` (:579), `build_next_actions` (:610), `profit_transition` (:136), `story_strength` (:195)

### `compliance/precheck.py` ← `prepublication_precheck.py`

| 함수 | 위치 | 역할 |
|---|---|---|
| `run_precheck` | :219 | 전체 실행 |
| `iter_segments` | :48 | 문장 단위 분해 → **에디터 실시간 경고의 단위** |
| `run_pattern_rule` | :127 | 정규식 룰 |
| `run_missing_disclosure_rule` | :148 | 광고 고지 누락 검사 |
| `build_summary` / `max_severity` / `next_actions` | :186 / :180 / :205 | 요약 |

룰 시드 데이터는 `prepublication_wording_rules.json` — 파일 구조:

```
ruleset_version, ruleset_name, review_label, purpose,
default_review_status, public_basis_note,
rules[] → { id, category, rule_type, severity, issue, rationale,
            suggested_action, safer_rewrite, patterns[], public_basis[] }
```

6개 룰: 단정 표현(medium) / 투자권유(high) / 손실보전·이익보장(critical) / 광고 고지 누락(high) / 해외주식 환율·세금 오인(high) / 고위험 레버리지·옵션 조장(critical). 총 54개 정규식 패턴.

DB 테이블 `precheck_rule_version`으로 이관하고 테넌트별 오버라이드를 허용합니다.

**원칙 4(심사 비대체)의 설계 수준 보장** — 아래 3개는 코드에서 변경 불가한 상수로 유지합니다:

| 필드 | 값 | 의미 |
|---|---|---|
| `review_label` | `"발행 전 프리체크"` | 심사필이 아님. API 응답과 UI에 항상 노출 |
| `legal_conclusion` | `null` (모든 finding) | 위법/합법을 판정하지 않음 |
| `boundary.not_compliance_approval` | `true` | 승인 아님 |
| `boundary.final_review_owner` | `"compliance_officer"` | 최종 판단 주체 |

`public_basis[].mapping_note`("사전 점검 기준으로 매핑합니다")도 그대로 전달합니다 — 법령 링크가 법적 결론으로 오인되지 않게 하는 장치입니다. 프리체크 API가 `pass`/`fail` 같은 불리언 판정을 반환하는 경로를 **만들지 않습니다.** 반환값은 finding 목록과 `requires_compliance_officer_review` 플래그뿐입니다.

### 이관하지 않는 것

- `save_log.py` — 해커톤 제출용 대화 로그 슬리밍. 제품과 무관
- 각 도구의 `parse_args` / `main` — FastAPI 라우터가 대체

### 결정론 계약 보존 (중요)

`test_regression_examples.py`는 "동일 fixture + 동일 `generated_at` → 동일 JSON"을 검증합니다. 이 계약은 웹 서비스에서도 **반드시 살아 있어야 합니다.**

- `generated_at`은 계속 **주입 파라미터**로 유지 (호출 시점 `utc_now()`를 함수 내부에서 부르지 않음)
- 회귀 테스트를 서비스 코드 경로(`core/`, `adapters/`)에 대해 재실행
- fixture 8종목(`../src/examples/facts/`)을 골든 데이터로 유지 — 회귀 테스트 겸 데모·온보딩 데이터로 이중 활용

리팩터 후에도 이 테스트가 통과하는 것이 Phase 0의 종료 조건입니다.

---

## 3. Numeric Token Binding — 원칙 1의 구현

**설계의 중심입니다.** 나머지는 이걸 지원하기 위해 존재합니다.

### 문제

시스템 프롬프트에 "숫자를 만들지 마세요"라고 쓰는 것은 보장이 아닙니다. LLM은 확률적입니다. 리포트에 들어가는 숫자에 확률적 보장은 부족합니다.

### 해결 — 숫자를 표현 불가능하게 만든다

#### 3.1 Fact ID 부여

Fact Layer가 모든 숫자에 안정 ID를 부여합니다.

```
fact:{source}:{entity}:{fiscal_year}:{metric}[.{path}]
```

| 부분 | 예 | 비고 |
|---|---|---|
| `source` | `sec` / `dart` | 소스 구분 |
| `entity` | `NVDA` / `005930` | SEC는 티커, DART는 고유번호 |
| `fiscal_year` | `FY2025` | |
| `metric` | `revenue_yoy_growth` | 아래 20개 중 하나 |
| `path` | `cost_of_revenue_ratio.margin_contribution` | 브리지 하위 경로 (옵션) |

**원시 지표 9개** (공시에서 직접 추출, `facts[]`):
`revenue` · `cost_of_revenue` · `gross_profit` · `operating_income` · `research_and_development` · `selling_general_and_admin` · `net_income` · `diluted_eps` · `assets`

**계산 지표 11개** (`core/metrics.py`가 계산, `calculations[]`):
`revenue_yoy_growth` · `gross_profit_yoy_growth` · `operating_income_yoy_growth` · `net_income_yoy_growth` · `gross_margin` · `operating_margin` · `net_margin` · `cost_of_revenue_ratio` · `implied_operating_expense_ratio` · `research_and_development_intensity` · `selling_general_and_admin_ratio`

**브리지** (`margin_bridges[]`):
`operating_margin_bridge` + 하위 경로(`operating_margin_change`, `components[].margin_contribution`, `supporting_drivers[]`, `reconciliation`)

예:
```
fact:sec:TSLA:FY2025:operating_margin_bridge.operating_margin_change   → "-2.7pp"
fact:sec:TSLA:FY2025:revenue_yoy_growth                                → "-2.9%"
fact:sec:NVDA:FY2025:research_and_development_intensity                → "6.8%"
```

#### 3.2 LLM은 토큰만 쓴다

시스템 프롬프트에 fact 카탈로그(ID + 라벨 + 단위, **값 없이**)를 제공하고, 본문에서는 `{{fact:...}}` 형식만 허용합니다.

LLM 출력:
```
TSLA의 FY2025에서 먼저 볼 숫자는 매출 총액이 아니라
영업이익률 {{fact:sec:TSLA:FY2025:operating_margin_bridge.operating_margin_change}} 변화입니다.
```

렌더링 후:
```
TSLA의 FY2025에서 먼저 볼 숫자는 매출 총액이 아니라
영업이익률 -2.7pp 변화입니다.
```

> **값을 프롬프트에 넣지 않는 것이 핵심입니다.** 값을 주면 LLM이 그 값을 복사해 쓰거나 변형할 수 있습니다. ID만 주면 물리적으로 숫자를 쓸 수 없습니다. (단, 앵글 선택 근거를 위해 일부 대표값을 별도 섹션에 제공 — 이 경우 §3.3 게이트가 리터럴 복사를 잡습니다.)

#### 3.3 검증 게이트

생성된 초안을 렌더링 **전에** 스캔합니다.

```
1. 토큰 추출     → 모든 {{fact:...}} 수집
2. ID 검증       → 존재하지 않는 fact ID 참조 시 → 거부
3. 리터럴 스캔   → 화이트리스트 외 숫자 리터럴 발견 시 → 거부
4. 의견 스캔     → 투자의견·목표주가 패턴 발견 시 → 거부 (원칙 2)
5. 통과          → 토큰을 display_value로 치환하여 렌더링
```

거부 시 위반 내역을 프롬프트에 추가해 **최대 2회 재생성**. 그 뒤에도 실패하면 사용자에게 위반 위치를 표시하고 수동 처리를 요청합니다 (조용히 통과시키지 않음).

**리터럴 화이트리스트** (숫자여도 허용):

| 패턴 | 예 |
|---|---|
| 회계연도 | `FY2025`, `2025년`, `2025회계연도` |
| 분기 | `1Q`, `4분기` |
| 서수·개수 | `첫 번째`, `세 가지`, `3개` |
| 법령 조항 | `제49조`, `제21조` |
| 목차 번호 | `1.`, `2)` |

화이트리스트는 **좁게 유지**합니다. 애매하면 거부하고 fact 토큰을 쓰게 하는 쪽이 안전합니다.

#### 3.4 감사 추적

`fact_binding` 테이블이 초안 버전별로 어떤 토큰이 어떤 fact snapshot의 어떤 값으로 치환됐는지 기록합니다. 6개월 뒤 감사에서 "이 리포트의 -2.7pp는 어디서 왔는가"에 답할 수 있습니다.

---

## 4. 소스 어댑터 — SEC + DART

### 인터페이스

```python
class FactSourceAdapter(Protocol):
    source_id: str                      # "sec_edgar" | "dart"

    def resolve_entity(self, query: str) -> Entity: ...
    def fetch_raw(self, entity: Entity, years: int) -> RawFacts: ...
    def normalize(self, raw: RawFacts, *, generated_at: str) -> CanonicalFacts: ...
```

`normalize()`가 반환하는 `CanonicalFacts`는 소스와 무관한 형태입니다. `core/metrics.py`는 이것만 받으므로 **마진 브리지 코드가 SEC/DART 양쪽에 그대로 적용됩니다.**

### CanonicalFacts 스키마 변경점

기존 `sec_financial_facts.schema.json`에서 확장:

```json
{
  "schema_version": "2.0.0",
  "source": "sec_edgar",              // ← 신규 discriminator
  "entity": {
    "id": "NVDA",
    "name": "NVIDIA Corporation",
    "source_key": "0001045810"        // SEC: CIK / DART: corp_code
  },
  "facts": [{
    "metric": "revenue",
    "fiscal_year": 2025,
    "value": 130497000000,
    "display_value": "$130.50B",
    "source_ref": {                   // ← sec: {} 를 source_ref로 일반화
      "kind": "sec_edgar",
      "taxonomy": "us-gaap",
      "tag": "RevenueFromContractWithCustomerExcludingAssessedTax",
      "form": "10-K",
      "accession_number": "0001045810-25-000023",
      "filed_at": "2025-02-26",
      "document_url": "https://www.sec.gov/Archives/edgar/data/..."
    }
  }],
  "coverage": { ... },                 // 기존 유지
  "omitted": [ ... ],                  // 기존 유지
  "source_policy": { ... }             // 기존 유지
}
```

DART의 `source_ref`:
```json
{
  "kind": "dart",
  "account_id": "ifrs-full_Revenue",
  "account_nm": "매출액",
  "report_code": "11011",
  "rcept_no": "20250311000123",
  "document_url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=..."
}
```

### SEC 어댑터

기존 로직 유지. 운영 요구사항:

- `SEC_USER_AGENT` 필수 (앱명 + 연락 이메일). 없으면 SEC가 차단
- rate limit 10 req/s → Redis 토큰 버킷으로 전역 제어. 기존 `pause_seconds=0.12`는 단일 프로세스 전제라 서비스에서는 부족
- **공시는 불변** → Company Facts 응답을 영구 캐싱. 재무제표 정정(10-K/A)만 무효화 트리거

### DART 어댑터 (Phase 2)

dart.fss.or.kr Open API 사용:

| API | 용도 |
|---|---|
| 고유번호 (`corpCode.xml`) | 종목코드 → corp_code 매핑 |
| 단일회사 전체 재무제표 (`fnlttSinglAcntAll`) | 계정과목별 다년 재무 데이터 |
| 공시목록 (`list`) | 신규 공시 감지 |
| 기업개요 (`company`) | 회사명·업종 |

**최대 리스크: 계정과목 매핑.** SEC는 `MetricSpec.tags`로 us-gaap 태그를 후보 리스트로 잡으면 끝입니다. DART는 `account_id`(IFRS 표준계정)와 `account_nm`(한글 계정명)이 회사·연도별로 흔들립니다.

대응:

1. **매핑 대상이 유한합니다** — 원시 지표 9개만 매핑하면 계산 지표 11개는 자동으로 따라옵니다
2. 2단 매핑: `account_id` 우선(표준계정 코드), 실패 시 `account_nm` 정규화 매칭
3. 매핑 테이블을 코드가 아닌 **데이터**로 관리 (`dart_account_mapping` 테이블) → 커버리지 개선이 배포 없이 가능
4. 미매핑 시 추정하지 않고 `omitted[]` + `coverage.status: "limited"` (원칙 3)
5. KOSPI200 우선 검증. 커버리지를 지표로 추적

예상 매핑:

| canonical | account_id 후보 | account_nm 후보 |
|---|---|---|
| `revenue` | `ifrs-full_Revenue` | 매출액, 영업수익, 수익(매출액) |
| `cost_of_revenue` | `ifrs-full_CostOfSales` | 매출원가 |
| `gross_profit` | `ifrs-full_GrossProfit` | 매출총이익 |
| `operating_income` | `dart_OperatingIncomeLoss` | 영업이익, 영업이익(손실) |
| `research_and_development` | `dart_ResearchAndDevelopmentExpense` | 경상연구개발비, 연구개발비 |
| `selling_general_and_admin` | `dart_TotalSellingGeneralAdministrativeExpenses` | 판매비와관리비 |
| `net_income` | `ifrs-full_ProfitLoss` | 당기순이익, 당기순이익(손실) |
| `diluted_eps` | `ifrs-full_DilutedEarningsLossPerShare` | 희석주당이익 |
| `assets` | `ifrs-full_Assets` | 자산총계 |

> 위 매핑은 **설계 초안**입니다. 실제 DART 응답으로 검증해야 합니다.

---

## 5. LLM 서술 레이어

### 모델 선택

| 용도 | 모델 | 단가 (per MTok) | 근거 |
|---|---|---|---|
| 초안 본문 생성 (기본) | `claude-opus-5` | $5 / $25 | 숫자 논리 정확성 + 한국어 문장 품질이 동시에 필요. 1M 컨텍스트로 fact 카탈로그 여유 |
| 저비용 경로 (옵션) | `claude-sonnet-5` | $3 / $15<br>(2026-08-31까지 인트로 $2 / $10) | 재생성·짧은 섹션 |

### 요청 파라미터

```python
response = client.messages.create(
    model="claude-opus-5",
    max_tokens=16000,                        # thinking + 출력 합산 상한
    thinking={"type": "adaptive"},           # Opus 5는 기본 on
    output_config={
        "effort": "high",                    # 재생성 경로는 "medium"
        "format": {"type": "json_schema", "schema": DRAFT_OUTPUT_SCHEMA},
    },
    system=[
        {"type": "text", "text": STYLE_GUIDE},            # 안정
        {"type": "text", "text": TOKEN_SYNTAX_CONTRACT},  # 안정
        {"type": "text", "text": AVOID_OVERCLAIMS},       # 안정
        {"type": "text", "text": fact_catalog,            # 종목·연도 단위 안정
         "cache_control": {"type": "ephemeral"}},         # ← 브레이크포인트
    ],
    messages=[
        {"role": "user", "content": user_instruction},    # 볼라틸
    ],
)
```

주의점:

- **`max_tokens`는 thinking + 출력 합산 상한**입니다. Opus 5는 thinking이 기본 on이므로 출력 길이만 계산하면 중간에 잘립니다
- `thinking: {"type": "disabled"}`는 effort `high` 이하에서만 허용됩니다. 그리고 disabled 상태에서는 도구 호출이 텍스트로 새거나 `<thinking>` 태그가 노출되는 알려진 실패 모드가 있으므로 **켜둡니다**
- `budget_tokens`는 Opus 5에서 400 오류입니다. `effort`로 제어합니다

### 프롬프트 캐싱

렌더 순서는 `tools` → `system` → `messages`입니다. 프리픽스 바이트가 한 글자라도 바뀌면 그 뒤 전체가 무효화됩니다.

배치 원칙:

| 내용 | 위치 | 안정성 |
|---|---|---|
| 문체 가이드 | system[0] | 영구 (테넌트별) |
| 토큰 문법 계약 | system[1] | 영구 |
| 금지 표현 목록 | system[2] | 룰셋 버전별 |
| fact 카탈로그 | system[3] + **캐시 브레이크포인트** | 종목·회계연도별 |
| 앵글 선택 · 사용자 지시 | messages[0] | 매 요청 |

- Opus 5 최소 캐시 프리픽스는 **512 토큰**. 그보다 짧으면 조용히 캐싱되지 않음
- 캐시 읽기 약 0.1배, 쓰기 1.25배(5분 TTL). 같은 종목으로 2회 이상 생성하면 이득
- **시스템 프롬프트에 타임스탬프·UUID·세션 ID를 넣으면 안 됩니다** — 매 요청 프리픽스가 달라져 캐싱이 전면 무효화됩니다. 흔한 실수
- `usage.cache_read_input_tokens`를 관측 지표로 수집. 0이 계속되면 사일런트 무효화 발생 중

### 2단 스키마 (structured output 제약 대응)

Claude structured output의 JSON Schema는 다음을 **지원하지 않습니다**:

- `minLength` / `maxLength`
- `minimum` / `maximum` / `multipleOf`
- `minItems` / `maxItems`
- 재귀 스키마
- `additionalProperties`를 `false` 외의 값으로 설정

기존 `src/schemas/` 5개는 `minItems`·`minLength`·`const`를 씁니다. **그대로 LLM 출력 스키마로 쓸 수 없습니다.**

2단으로 분리합니다:

```
LLM 출력 스키마 (단순)          정식 검증 스키마 (엄격)
─────────────────────          ──────────────────────
제약 없음                       minItems / minLength / const 유지
additionalProperties: false     기존 5개 스키마 확장
   │                                    ▲
   └─→ Claude structured output ─→ 응답 ─┘
                                   │
                              불일치 시 재생성
```

LLM 응답을 받은 뒤 서버에서 정식 스키마로 2차 검증합니다. Anthropic 측 스키마 강제와 별개의 우리 게이트입니다. §3.3 검증 게이트도 이 단계에서 함께 실행됩니다.

### 의견 필드 차단 (원칙 2)

이중 차단:

1. **스키마 레벨** — `DRAFT_OUTPUT_SCHEMA`에 `investment_opinion` 필드가 존재하지 않음. `additionalProperties: false`이므로 LLM이 추가할 수 없음
2. **API 레벨** — `POST /drafts/{id}/opinion`은 사람 세션 토큰만 허용. `narrative/` 모듈에는 이 엔드포인트 호출 경로가 없음

추가로 §3.3 게이트 4단계가 생성된 문장에서 의견 패턴(목표주가, 매수/매도, 상승 여력 등)을 스캔합니다.

### 테넌트별 문체 학습

사내 과거 리포트를 few-shot 스타일 예시로 `system[0]`에 포함합니다.

- 테넌트 격리: 다른 하우스의 리포트가 절대 섞이지 않도록 프롬프트 조립 시 `tenant_id` 검증
- 스타일 예시는 **문체 참조용**이며 숫자는 여전히 토큰으로만 나옵니다
- 안정 프리픽스에 두므로 캐싱 이점을 받습니다

### 원가

| 항목 | 토큰 | 비용 |
|---|---|---|
| 입력 (캐시 미스) | ~10,000 | $0.050 |
| 입력 (캐시 히트) | ~10,000 | $0.005 |
| 출력 | ~4,000 | $0.100 |
| **건당 (미스)** | | **$0.150** |
| **건당 (히트)** | | **$0.105** |

seat당 월 20~50건 → $2~8. [00-PRD.md](../../surfaces/ra-tool-v0/prd.md) §9 가격 근거.

---

## 6. 데이터 모델

전 테이블에 `tenant_id` + Postgres Row Level Security.

```
tenant                  id, name, plan, deployment_mode(cloud|vpc), created_at
user                    id, tenant_id, email, role(ra|analyst|compliance|admin)
workspace               id, tenant_id, name, owner_user_id
coverage_ticker         id, workspace_id, source, entity_id, added_by, last_filing_seen

fact_snapshot           id, tenant_id, source, entity_id, fiscal_years[],
                        canonical_facts(jsonb), generated_at, fetched_at,
                        coverage_status, adapter_version
                        └─ 불변. 초안이 참조하는 시점의 팩트를 고정

draft                   id, tenant_id, workspace_id, entity_id, title,
                        status(drafting|review|precheck|ready), created_by
draft_version           id, draft_id, version_no, body_template(text),
                        rendered_body(text), fact_snapshot_id,
                        llm_model, llm_effort, token_usage(jsonb),
                        gate_result(jsonb), created_by, created_at
                        └─ body_template은 {{fact:...}} 토큰 상태로 보존

fact_binding            id, draft_version_id, token(text), fact_id(text),
                        resolved_value(text), source_ref(jsonb)
                        └─ 감사 추적의 핵심

opinion_entry           id, draft_id, rating, target_price, currency,
                        rationale, entered_by, entered_at
                        └─ ai_generated 컬럼 없음. 사람 입력만 존재 가능

precheck_rule_version   id, tenant_id(nullable=글로벌), ruleset_version,
                        rules(jsonb), effective_from
precheck_run            id, draft_version_id, ruleset_version,
                        findings(jsonb), max_severity, finding_count,
                        requires_compliance_review, run_at

audit_log               id, tenant_id, actor_user_id, action, target_type,
                        target_id, payload(jsonb), at
```

### 왜 `fact_snapshot`을 불변으로 두는가

공시는 정정될 수 있고(10-K/A), 어댑터 버전이 올라가면 계산이 바뀔 수 있습니다. 초안이 "현재 팩트"를 참조하면 6개월 뒤 감사 시점에 숫자가 달라져 있을 수 있습니다.

초안은 항상 특정 `fact_snapshot_id`를 참조합니다. 그 스냅샷의 `canonical_facts`는 변경되지 않습니다. 어댑터 버전도 함께 기록해서, 계산 로직이 바뀌었을 때 어떤 버전으로 계산된 값인지 추적 가능합니다.

### 왜 `opinion_entry`에 `ai_generated` 컬럼이 없는가

원칙 2([00-PRD.md](../../surfaces/ra-tool-v0/prd.md) §4)의 스키마 수준 구현입니다.

`ai_generated: boolean` 컬럼을 두면 `true`인 행이 **존재할 수 있게** 됩니다. 버그나 마이그레이션 실수로 그런 행이 하나 생기면 원칙이 깨집니다. 컬럼을 만들지 않으면 그 상태가 표현 불가능합니다.

대신 산출물(API 응답, 내보낸 문서)에서는 계약을 명시적으로 노출합니다:

```json
{
  "ai_generated_opinion": false,
  "investment_opinion": {
    "source": "human_analyst",
    "rating": "매수",
    "target_price": 420.00,
    "currency": "USD",
    "entered_by": "user_8f2a",
    "entered_at": "2026-07-30T05:22:11Z"
  }
}
```

`ai_generated_opinion`은 DB 컬럼이 아니라 **직렬화 시점의 상수**입니다. `investment_opinion.source`도 `"human_analyst"` 외의 값을 가질 수 없습니다(enum 단일값). 의견이 없으면 `investment_opinion: null`이며, 이 경우에도 `ai_generated_opinion: false`는 유지됩니다.

3중 방어를 정리하면:

| 레이어 | 방어 |
|---|---|
| 스키마 | `DRAFT_OUTPUT_SCHEMA`에 의견 필드 부재 + `additionalProperties: false` → LLM이 추가 불가 |
| DB | `ai_generated` 컬럼 부재 → AI 생성 의견이 표현 불가능 |
| API | `POST /drafts/{id}/opinion`이 `role in (analyst, admin)` 요구. `narrative/`에 호출 경로 없음 |

추가로 §3.3 검증 게이트 4단계가 생성된 본문에서 의견 패턴(목표주가, 매수/매도, 상승 여력 등)을 스캔합니다.

---

## 7. API 명세

모든 엔드포인트는 세션에서 `tenant_id`를 주입받고, DB 접근은 RLS로 강제 격리됩니다.

### Fact Layer

| 메서드 | 경로 | 설명 |
|---|---|---|
| `POST` | `/facts/resolve` | `{source, query}` → `{entity, exists, name}` |
| `POST` | `/facts/snapshots` | `{source, entity_id, years}` → 스냅샷 생성 (fetch + normalize + calculate). 캐시 히트 시 기존 스냅샷 반환 |
| `GET` | `/facts/snapshots/{id}` | `CanonicalFacts` 전체 |
| `GET` | `/facts/snapshots/{id}/catalog` | fact ID 카탈로그 (LLM 프롬프트용, 값 제외) |
| `GET` | `/facts/snapshots/{id}/export.xlsx` | 엑셀 내보내기 |

### 초안

| 메서드 | 경로 | 설명 |
|---|---|---|
| `POST` | `/drafts` | `{entity_id, fact_snapshot_id}` → 초안 생성 + `core/packet.py` 골격(앵글·제목 후보) 반환 |
| `POST` | `/drafts/{id}/generate` | `{angle_id, instruction}` → **SSE 스트리밍**. 생성 → 게이트 → 렌더링 |
| `GET` | `/drafts/{id}/versions` | 버전 목록 |
| `PATCH` | `/drafts/{id}/versions/{v}` | 사용자 수동 편집 저장 (게이트 재실행) |
| `GET` | `/drafts/{id}/bindings` | fact 바인딩 목록 (출처 팝오버용) |

### 의견 — 사람 전용

| 메서드 | 경로 | 설명 |
|---|---|---|
| `POST` | `/drafts/{id}/opinion` | `{rating, target_price, currency, rationale}`. **`role in (analyst, admin)` 필수.** `narrative/`에서 호출 불가 |
| `GET` | `/drafts/{id}/opinion` | 의견 + `entered_by` + `entered_at` |

### 컴플라이언스

| 메서드 | 경로 | 설명 |
|---|---|---|
| `POST` | `/precheck/inline` | `{text}` → 문장 단위 finding (에디터 실시간용, 저장 안 함) |
| `POST` | `/precheck/runs` | `{draft_version_id}` → 전체 실행 + 저장 |
| `GET` | `/precheck/runs/{id}` | finding 상세 + 법령 매핑 |
| `GET` | `/precheck/runs/{id}/export` | 심사 요청 문서 (Phase 2) |

### 감사

| 메서드 | 경로 | 설명 |
|---|---|---|
| `GET` | `/audit` | 필터: actor / action / target / 기간. **`role in (compliance, admin)`** |

### SSE 이벤트 (`/drafts/{id}/generate`)

```
event: status      data: {"phase": "generating"}
event: delta       data: {"text": "TSLA의 FY2025에서..."}
event: status      data: {"phase": "gating"}
event: gate        data: {"passed": false, "violations": [...], "retry": 1}
event: status      data: {"phase": "generating"}          // 재생성
event: delta       data: {...}
event: gate        data: {"passed": true}
event: rendered    data: {"version_no": 3, "body": "...", "bindings": [...]}
event: done        data: {"token_usage": {...}}
```

게이트 실패와 재생성을 사용자에게 **숨기지 않고 보여줍니다.** 제품의 신뢰 메커니즘이 작동하는 것을 보는 게 신뢰를 만듭니다.

---

## 8. 멀티테넌시와 VPC 전환

### 클라우드 (Phase 1~2)

- 단일 배포, 다수 테넌트
- Postgres RLS로 행 단위 격리. 애플리케이션 버그가 있어도 DB가 막음
- 테넌트별 암호화 키로 초안·의견 필드 암호화
- LLM 요청 조립 시 `tenant_id` 검증 (문체 예시가 섞이면 안 됨)

### VPC / 온프렘 (Phase 3)

**같은 코드베이스로 single-tenant 배포합니다.** 전환 시 코드 변경 없음:

- `tenant` 테이블에 행 1개
- RLS는 그대로 작동 (항상 통과)
- `deployment_mode: "vpc"`로 표시해 기능 게이팅(예: 외부 텔레메트리 비활성)
- Claude API는 고객사 자체 키 또는 Bedrock/Vertex 경유 옵션 — 단, **Managed Agents·프롬프트 자동 캐싱·Batches는 Bedrock/Vertex에서 미지원**이므로 해당 경로를 쓰지 않도록 설계 (현재 설계는 Messages API + 수동 `cache_control`만 사용 → 이식 가능)

이 제약을 처음부터 지키는 것이 Phase 3 전환 비용을 없앱니다.

---

## 9. 보안

초안과 의견은 **미공개 정보(MNPI)**입니다. 유출 시 규제 이슈로 직결됩니다.

| 항목 | 설계 |
|---|---|
| 저장 암호화 | `draft_version.body_template`, `rendered_body`, `opinion_entry` — 테넌트별 키 |
| 전송 | TLS 1.3 전 구간 |
| 인증 | 클라우드: 이메일 + TOTP. Enterprise: SSO (SAML/OIDC) |
| 권한 | `ra` / `analyst` / `compliance` / `admin`. 의견 입력은 `analyst`+ |
| LLM 전송 데이터 | fact(공개 공시) + 사용자 지시 + 문체 예시. **의견 필드는 절대 전송하지 않음** |
| 감사 | 모든 상태 변경이 `audit_log`에 기록. 삭제 불가 (append-only) |
| SEC User-Agent | 연락 가능한 이메일 필수 (SEC 정책) |

**오픈 아이템 (O4)**: Anthropic의 30일 데이터 보존 정책과 증권사 요구사항의 정합성 검토가 필요합니다. Enterprise 논의 전에 결론이 있어야 합니다. Zero Data Retention이 요구되면 사용 가능한 모델이 제한될 수 있습니다.

---

## 10. 관측성

제품 무결성 지표가 일반 서비스 지표보다 중요합니다.

| 지표 | 임계 | 대응 |
|---|---|---|
| 미바인딩 숫자 리터럴 렌더링 | **0** | P0. 원칙 1 위반 |
| LLM 경로 의견 필드 쓰기 시도 | **0** | P0. 원칙 2 위반 |
| 게이트 1차 통과율 | < 90% | 프롬프트 또는 fact 카탈로그 점검 |
| 평균 재생성 횟수 | > 0.5 | 프롬프트 튜닝 필요 |
| 캐시 히트율 (`cache_read_input_tokens > 0`) | < 50% | 사일런트 무효화 조사 |
| 테넌트별 토큰 원가 | seat 가격의 10% 초과 | 가격 모델 재검토 |
| SEC / DART fetch 실패율 | > 1% | rate limit 또는 API 변경 |
| `coverage.status == "limited"` 비율 | 소스별 추적 | DART 매핑 커버리지 지표 |

---

## 11. 기술 결정 요약

| # | 결정 | 대안 | 선택 이유 |
|---|---|---|---|
| 1 | FastAPI + 기존 Python 재사용 | TypeScript 전면 재작성 | 검증된 계산 로직(마진 브리지, 정규식 룰 54개)과 회귀 테스트 10개를 버리지 않음 |
| 2 | Numeric Token Binding | 프롬프트 지시 + 사후 검증 | 확률적 보장이 아닌 구조적 보장. 제품의 유일한 진짜 차별점 |
| 3 | 2단 스키마 | structured output 미사용 | structured output의 JSON Schema 제약과 기존 스키마의 엄격성을 동시에 만족 |
| 4 | 불변 `fact_snapshot` | 실시간 fact 조회 | 감사 시점 재현성. 공시 정정·어댑터 버전 변경에도 초안의 숫자가 고정 |
| 5 | `claude-opus-5` 기본 | Sonnet 5로 원가 절감 | 원가가 seat 가격의 2~6%에 불과. 숫자 논리 정확성이 더 중요 |
| 6 | Messages API + 수동 캐싱만 사용 | Managed Agents 등 | Bedrock/Vertex 이식성 확보 → VPC 전환 비용 제거 |
| 7 | Postgres RLS | 애플리케이션 레벨 격리만 | 앱 버그가 있어도 DB가 막음. MNPI 취급 서비스에 필요 |

---

## 참고 문서

- [00-PRD.md](../../surfaces/ra-tool-v0/prd.md) — 제품 기획
- [02-SCREENS.md](../../surfaces/ra-tool-v0/screens.md) — 화면 설계
- [03-ROADMAP.md](../../surfaces/ra-tool-v0/roadmap.md) — 실행 계획
