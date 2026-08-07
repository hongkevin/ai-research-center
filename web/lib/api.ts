/**
 * FastAPI `/api/*` 클라이언트.
 *
 * 타입은 `src/arc/web/app.py`의 `ViewModel` 데이터클래스를 그대로 옮긴 것이다.
 * 서버가 `vm.__dict__`를 직렬화하므로 필드 이름이 곧 계약이다 — 파이썬 쪽을
 * 바꾸면 여기도 바꿔야 한다.
 *
 * 같은 출처(same-origin)를 기본으로 한다. 정적 익스포트를 FastAPI가 서빙하므로
 * 배포에서는 base가 빈 문자열이다. 개발에서 `next dev`(3000)와 `arc web`(8000)을
 * 따로 띄울 때만 `NEXT_PUBLIC_API_BASE`로 가리킨다.
 */

import { accessToken } from "./supabase";

const BASE = process.env.NEXT_PUBLIC_API_BASE ?? "";

/**
 * 인증 헤더를 붙인 fetch.
 *
 * Next 서버가 없어(D37) 브라우저가 토큰을 들고 있다가 호출마다 붙인다.
 * 로그인이 꺼져 있으면(로컬 개발) 그냥 통과한다.
 */
async function api(path: string, init: RequestInit = {}): Promise<Response> {
  const token = await accessToken();
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  return fetch(`${BASE}${path}`, { ...init, headers });
}

export interface Binding {
  key: string;
  label: string;
  value: string;
  formula: string;
  inputs: string[];
  source: string;
  document: string;
  url: string;
  api: string;
  internal: boolean;
}

export interface Violation {
  rule: string;
  line: number | null;
  detail: string;
}

export interface Assumption {
  key: string;
  label: string;
  value: number;
  unit: string;
  basis: string;
  override: boolean;
}

export interface Revision {
  label: string;
  previous: number;
  current: number;
  change: number;
  direction: string;
}

/** 추정 연도 1개. **2년차부터는 사람이 넣은 가정으로만 간다.** */
export interface EstimateYear {
  fiscal_year: number;
  values: Record<string, number>;
  assumptions: Assumption[];
}

export interface StageCheck {
  label: string;
  value: string;
  ok: boolean;
}

/**
 * 파이프라인 단계 하나의 기록.
 *
 * `status`에서 **`absent`와 `failed`는 다릅니다.** 단일 부문 회사에 부문 손익이
 * 없는 건 정상이고(D33이 정확히 거부합니다), DART 조회 실패는 결함입니다.
 * 같은 색으로 칠하면 검토자가 정상을 결함으로 읽습니다.
 */
export interface Stage {
  key: string;
  label: string;
  status: "ok" | "partial" | "absent" | "failed";
  summary: string;
  checks: StageCheck[];
  registered: number;
  note: string;
}

export interface SegmentItem {
  name: string;
  color: string;
  amount: string;
  share: string;
}

export interface NoteChange {
  name: string;
  kind: "actual" | "estimate" | "other";
  previous: string;
  current: string;
  direction: string;
  /** 이미 문자열로 굳어 온다 — 화면이 숫자를 다시 만들지 않는다. */
  change: string;
}

export interface AreaDiff {
  area: string;
  prior: string;
  now: string;
  /** 유지 · 강화 · 약화 · 확인불가 */
  verdict: string;
}

export interface ViewModel {
  symbol: string;
  year: number;
  company: string;
  market: string;
  basis: string;
  body_html: string;
  bindings: Binding[];
  gate_passed: boolean;
  gate_summary: string;
  violations: Violation[];
  metrics_found: number;
  metrics_missing: string[];
  registry_size: number;
  stages: Stage[];
  assumptions: Assumption[];
  revisions: Revision[];
  estimate_warnings: string[];
  /** 연차별 추정. 첫 해는 assumptions와 같다. */
  estimate_years: EstimateYear[];
  segment_chart: string;
  segment_legend: string;
  /** 부문별 이름 · 금액 · 비중. 값은 레지스트리가 만든 표시 문자열이다. */
  segment_items: SegmentItem[];
  trend_chart: string;
  trend_legend: string;
  /** 분기 막대 + 이익률 선 (D57). 차트 그리기는 RA의 업무 카테고리다. */
  quarter_chart: string;
  quarter_margin_chart: string;
  quarter_note: string;
  trend_note: string;
  industry_context: boolean;
  llm_used: boolean;
  llm_model: string;
  llm_cost: number | null;
  published_path: string;
  /** 직전 발간 노트 대비 변화 (D46). 비교 대상이 없으면 빈 목록. */
  changes: NoteChange[];
  changes_basis: string;
  /** 「얼마가」가 아니라 **「무엇이」** 달라졌는가 (D64). */
  areas: AreaDiff[];
  areas_basis: string;
  areas_note: string;
  notice: string;
  error: string;
}

/**
 * 보드의 칸. 순서가 곧 왼→오 (D51).
 *
 * 넷에서 셋으로 줄였다. 「수집됨」은 1.5초 머무는 곳이라 칸이 아니라 카드
 * 스피너였고, 「확인 필요」는 칸이 아니라 **속성**이었다 — 검토 중인 카드가
 * 확인이 필요할 수도 아닐 수도 있다.
 *
 * 종착점이 「발간」이 아닌 이유: 조사분석자료는 공표 전 심의가 법정 절차이고
 * 해외 RMS도 애널리스트 → 어소시에이트 → Supervisory Analyst → 컴플라이언스로
 * 간다. **RA는 발간 권한이 없다.**
 */
export const COLUMNS = ["draft", "review", "handoff"] as const;
export type Column = (typeof COLUMNS)[number];

export const COLUMN_LABEL: Record<Column, string> = {
  draft: "초안",
  review: "검토 중",
  handoff: "넘김",
};

export const COLUMN_HINT: Record<Column, string> = {
  draft: "기계가 만들어 놓은 것. 아직 안 봤습니다",
  review: "읽고 고치는 중",
  handoff: "확정해서 내보낸 것",
};

/**
 * 카드의 종류. **새 칸이 아니라 새 종류다** — 피어 뷰도 초안이고, 검토하고,
 * 넘긴다. 종목 카드와 똑같은 수명을 산다 (D68).
 */
export type CardKind = "single" | "peer";

/** 목록용 — 본문(60KB)이 빠져 있다. */
export interface CardSummary {
  id: string;
  kind: CardKind;
  /** 피어 카드는 비어 있다 — 종목 하나에 매이지 않는다 */
  symbol: string;
  year: number;
  created_at: string;
  column: Column;
  /** 생성 중인가. **칸이 아니라 카드의 상태다** (D51). */
  running: boolean;
  confirmed: boolean;
  company: string;
  attention: string[];
  error: string;
  gate_passed: boolean;
  registry_size: number;
  stage_count: number;
  version: string;
  revision_count: number;
  published_path: string;
  /** 피어 카드만 — 묶은 종목 수 */
  member_count: number;
  member_symbols: string[];
  /** 피어 카드만 — 실제로 카드가 있어 표에 설 수 있는 종목 수 */
  member_ready?: number;
}

/** 수정 1건의 기록. **버전 이력은 감사 흔적이 아니라 콘텐츠다** (D25). */
export interface Revision {
  version: string;
  created_at: string;
  section: string;
  comment: string;
  before: string;
  after: string;
}

export interface CardDetail extends Omit<
  CardSummary,
  "gate_passed" | "registry_size" | "stage_count" | "revision_count"
> {
  vm: ViewModel;
  versions: Revision[];
  /** 피어 카드만. 구성원은 **읽을 때마다 저장소에서 다시 찾은 것**이다. */
  members?: PeerMember[];
  peer_table?: PeerTable;
  /** 기간 등락. **리포트를 기다리지 않는다** — 시세는 매일 있다 */
  moves?: Moves[];
}

export async function listCards(): Promise<CardSummary[]> {
  const r = await api(`/api/cards`);
  if (!r.ok) return [];
  return (await r.json()).cards ?? [];
}

export async function getCard(id: string): Promise<CardDetail> {
  const r = await api(`/api/cards/${id}`);
  if (!r.ok) await fail(r);
  return r.json();
}

/** 「확인함」 — 확인 필요를 벗어난다. 칸 배정 자체는 서버가 자동으로 한다. */
export async function confirmCard(id: string): Promise<CardSummary> {
  const r = await api(`/api/cards/${id}/confirm`, { method: "POST" });
  if (!r.ok) await fail(r);
  return r.json();
}

/**
 * 가정을 바꿔 다시 계산한다 → 버전이 오른다.
 *
 * 문장 수정과 달리 **숫자가 바뀐다.** 서버가 LLM 서술을 버리고 결정론 문장으로
 * 다시 만든다 — 옛 문장이 새 숫자를 설명한다고 우길 수 없다.
 */
export async function recompute(
  id: string,
  assumptions: Record<string, number>,
  forward: Record<string, number>[] = [],
): Promise<{ version: string } | { error: string }> {
  const r = await api(`/api/cards/${id}/recompute`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ assumptions, forward }),
  });
  const body = await r.json().catch(() => ({}));
  return r.ok ? body : { error: body.error ?? `HTTP ${r.status}` };
}

/**
 * 카드를 발간한다 — **읽고 고친 뒤에 하는 일**이라 여기 있다.
 *
 * 카드의 현재 본문을 그대로 낸다. 코멘트로 고친 것도, 직접 편집한 것도 살아서
 * 나간다. 그리고 추정이 스냅샷으로 저장돼 다음 발간의 변화 추적 기준이 된다.
 */
export async function publishCard(
  id: string,
): Promise<{ published_path: string } | { error: string }> {
  const r = await api(`/api/cards/${id}/publish`, { method: "POST" });
  const body = await r.json().catch(() => ({}));
  return r.ok ? body : { error: body.error ?? `HTTP ${r.status}` };
}

export async function deleteCard(id: string): Promise<void> {
  const r = await api(`/api/cards/${id}`, { method: "DELETE" });
  if (!r.ok) await fail(r);
}

export interface DocSection {
  title: string;
  editable: boolean;
  chars: number;
  /** 플레이스홀더가 살아 있는 원문. 직접 편집이 이걸 고친다. */
  text: string;
}

export interface SaveResult {
  ok: boolean;
  version?: string;
  /** G0가 막았을 때. **직접 숫자를 타이핑하면 여기 걸린다.** */
  violations?: { rule: string; detail: string }[];
  error?: string;
}

/** 제안된 수정 1건. **아직 채택되지 않았다.** */
export interface Proposal {
  section: string;
  comment: string;
  before: string;
  after: string;
  changed: boolean;
  /** 이 루프의 핵심 보장 — 문장은 바뀌어도 수치는 그대로다. */
  numbers_unchanged: boolean;
  numbers: string[];
  problems: string[];
  used_llm: boolean;
  model: string;
  cost_usd: number | null;
}

export async function listSections(
  id: string,
): Promise<{ version: string; sections: DocSection[] }> {
  const r = await api(`/api/cards/${id}/sections`);
  if (!r.ok) await fail(r);
  return r.json();
}

export async function proposeRevision(
  id: string,
  section: string,
  comment: string,
): Promise<Proposal> {
  const r = await api(`/api/cards/${id}/revise`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ section, comment }),
  });
  if (!r.ok) await fail(r);
  return r.json();
}

/**
 * 섹션을 저장한다 → 버전이 오른다.
 *
 * LLM 제안을 채택할 때도, 사람이 직접 고친 것을 저장할 때도 **같은 경로**다.
 * 서버가 G0를 다시 돌린 뒤에만 받아주므로, 직접 편집으로 숫자를 타이핑하면
 * 여기서 막히고 이유가 돌아온다 — 불변식이 처음으로 사람에게 보이는 자리다.
 */
export async function saveSection(
  id: string,
  section: string,
  after: string,
  comment: string,
): Promise<SaveResult> {
  const r = await api(`/api/cards/${id}/accept`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ section, after, comment }),
  });
  const body = await r.json().catch(() => ({}));
  if (r.ok) return { ok: true, version: body.version };
  return { ok: false, error: body.error, violations: body.violations };
}

export interface CompanyHit {
  name: string;
  symbol: string;
}

/** 시장 구분까지 붙은 회사 한 줄. corpCode.xml에 없어 company.json을 친다. */
export interface CompanyInfo {
  symbol: string;
  name: string;
  market: string;
}

/** 실제로 제출된 정기보고서 1건. **계산이 아니라 DART가 준 사실이다.** */
export interface Filing {
  year: number;
  period: string;
  label: string;
  title: string;
  filed_at: string;
  rcept_no: string;
  url: string;
}

/** 우리가 아직 못 읽는 것 — 하지만 있다는 사실은 알려야 한다. */
export interface Preliminary {
  title: string;
  filed_at: string;
  url: string;
}

export async function getFilings(
  symbol: string,
): Promise<{ periodic: Filing[]; preliminary: Preliminary[] }> {
  const r = await api(`/api/company/${encodeURIComponent(symbol)}/reports`);
  if (!r.ok) return { periodic: [], preliminary: [] };
  return r.json();
}

export async function getCompany(symbol: string): Promise<CompanyInfo | null> {
  const r = await api(`/api/company/${encodeURIComponent(symbol)}`);
  return r.ok ? r.json() : null;
}

export interface DetectedCompany {
  symbol: string;
  name: string;
  short_name: string;
  market: string;
}

export interface Converted {
  markdown: string;
  source_name: string;
  kind: string;
  pages: number;
  chars: number;
  warnings: string[];
  outline: string[];
  /** 문서에서 읽어낸 종목. 실측 적중 92%라 **사람이 확인한다**. */
  company: DetectedCompany | null;
}

/**
 * 업로드 문서 → 마크다운. **저장하지 않고 돌려만 준다.**
 *
 * 오래된 PDF는 글자가 부분적으로 깨져 나올 수 있는데 통계로는 정상 문서와
 * 안 갈린다. 그래서 변환 결과를 사람이 보고 넘긴다 (D48).
 */
export async function convertFile(
  file: File,
): Promise<Converted | { error: string }> {
  const body = new FormData();
  body.append("file", file);
  const r = await api("/api/convert", { method: "POST", body });
  const d = await r.json().catch(() => ({}));
  return r.ok ? d : { error: d.error ?? `HTTP ${r.status}` };
}

export interface JobRequest {
  symbol: string;
  year: number;
  period: string;
  llm: boolean;
  /** 최근 기사를 찾아 「최근 이슈」 절을 붙일 것인가 (D45). */
  search: boolean;
  /** 업로드한 직전 노트의 마크다운 (D48). 숫자는 본문에 안 들어간다. */
  prior_markdown: string;
  prior_name: string;
  assume: string;
  publish: boolean;
}

/**
 * 노트 파일 주소. **증권사에서 리포트가 오가는 형식은 Word다** (D53).
 *
 * `<a download>` 대신 그냥 링크로 연다 — 서버가 `Content-Disposition`을
 * 붙이므로 브라우저가 알아서 내려받고, 한글 파일명도 안 깨진다.
 */
export function downloadUrl(
  cardId: string,
  format: "md" | "docx" | "xlsx",
): string {
  return `${BASE}/api/cards/${cardId}/download?format=${format}`;
}

export interface FillSummary {
  written: {
    sheet: string;
    cell: string;
    label: string;
    year: number;
    before: unknown;
    after: number;
  }[];
  skipped: { sheet: string; cell: string; label: string; reason: string }[];
}

/**
 * 엑셀 모델에 공시 실적을 채워 **사본을 내려받는다** (D62).
 *
 * 채운 내역은 `X-Arc-Fill-Summary` 헤더로 온다 — 파일만 주면 무엇이
 * 바뀌었는지 알 수 없다.
 */
export async function fillModel(
  cardId: string,
  file: File,
  unit: number,
): Promise<{ summary: FillSummary } | { error: string }> {
  const body = new FormData();
  body.append("file", file);
  body.append("unit", String(unit));
  const r = await api(`/api/cards/${cardId}/fill-model`, {
    method: "POST",
    body,
  });
  if (!r.ok) {
    const d = await r.json().catch(() => ({}));
    return { error: d.error ?? `HTTP ${r.status}` };
  }
  const raw = r.headers.get("X-Arc-Fill-Summary");
  const blob = await r.blob();
  // 브라우저가 내려받게 한다 — 사본이므로 원본은 그대로다.
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = file.name.replace(/\.(xlsx|xlsm)$/i, "") + "_ARC업데이트.xlsx";
  a.click();
  URL.revokeObjectURL(url);
  return {
    summary: raw
      ? (JSON.parse(decodeURIComponent(raw)) as FillSummary)
      : { written: [], skipped: [] },
  };
}

export interface Capabilities {
  llm_key: boolean;
  news_key: boolean;
}

/**
 * 서버가 무엇을 할 수 있는가.
 *
 * 기사 검색은 별도 키(NAVER_CLIENT_ID/SECRET)가 있어야 돈다. 키가 없는데
 * 체크박스를 켤 수 있게 두면 눌러도 아무 일이 안 일어나고, 사용자는 기능이
 * 고장 났다고 읽는다. 못 하면 **못 한다고 쓴다.**
 */
export async function getCapabilities(): Promise<Capabilities> {
  try {
    const r = await api("/api/health");
    if (!r.ok) return { llm_key: false, news_key: false };
    const d = await r.json();
    return { llm_key: !!d.llm_key, news_key: !!d.news_key };
  } catch {
    return { llm_key: false, news_key: false };
  }
}

/** 서버가 준 메시지를 그대로 올린다 — 원인을 화면에 보여주는 게 목적이다. */
async function fail(r: Response): Promise<never> {
  let detail = `HTTP ${r.status}`;
  try {
    const body = await r.json();
    if (body?.error) detail = body.error;
  } catch {
    /* 본문이 JSON이 아니면 상태 코드로 충분하다 */
  }
  throw new Error(detail);
}

export async function searchCompanies(
  q: string,
  limit = 10,
): Promise<CompanyHit[]> {
  const r = await api(`/api/search?q=${encodeURIComponent(q)}&limit=${limit}`);
  if (!r.ok) return [];
  const d = await r.json();
  return d.results ?? [];
}

/**
 * 생성 시작. `job_id`와 **카드 id**를 함께 돌려준다.
 *
 * 카드 id가 필요한 이유: 진행 표시를 **그 카드에** 붙여야 한다. 예전에는
 * 진행 표시가 생성 폼에 붙어 있어서 두 건을 돌리면 마지막 것만 보였다 (D49).
 */
export class DuplicateDraftError extends Error {
  constructor(readonly cardId: string) {
    super("같은 보고서로 만든 초안이 이미 있습니다.");
  }
}

export async function startJob(
  req: JobRequest,
): Promise<{ jobId: string; cardId: string }> {
  const r = await api(`/api/jobs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  // 같은 보고서 초안이 이미 있으면 새로 만들지 않고 **그것을 연다** (D51).
  if (r.status === 409) {
    const d = await r.json().catch(() => ({}));
    if (d.existing_card_id) throw new DuplicateDraftError(d.existing_card_id);
  }
  if (!r.ok) await fail(r);
  const d = await r.json();
  return { jobId: d.job_id, cardId: d.card_id ?? "" };
}

export async function fetchResult(jobId: string): Promise<ViewModel> {
  const r = await api(`/api/jobs/${jobId}/result`);
  if (!r.ok) await fail(r);
  return r.json();
}

/**
 * 진행 스트림 주소.
 *
 * **`EventSource`는 헤더를 붙일 수 없다.** 그래서 이 경로만 토큰을 쿼리로
 * 넘긴다. 쿼리 토큰은 서버 로그에 남을 수 있어 일반적으로 피해야 하지만,
 * 다른 방법이 없고 이 엔드포인트가 흘리는 것은 단계 이름뿐이다.
 */
export async function eventsUrl(jobId: string): Promise<string> {
  const token = await accessToken();
  const q = token ? `?access_token=${encodeURIComponent(token)}` : "";
  return `${BASE}/api/jobs/${jobId}/events${q}`;
}

/* ── 리서치 채팅 ────────────────────────────────────────────────────
 *
 * 인터뷰가 준 가장 큰 발견: *"리포트 쓰는 시간보다 훨씬 많은 비중은 클라이언트
 * 리퀘스트"* — 하루 10~15건. 일반 챗봇이 아니라 **모르면 모른다고 하는** 것이
 * 설계 원칙이다.
 */

/** 답이 딛고 선 것 하나. **항목마다 다르다** (D36). */
export interface AskSource {
  /** `"number"` = 수치 하나 · `"card"` = 카드 전체 */
  kind: string;
  /** 본문의 `[c1]` */
  marker: string;
  card_id: string;
  symbol: string;
  company: string;
  period_label: string;
  key: string;
  label: string;
  value: string;
  formula: string;
  sections: string[];
  dataset: string;
  document: string;
  /** 공시 뷰어 — 눌렀던 숫자의 그 절로 간다 (D44) */
  verify_url: string;
  source_url: string;
}

export interface AskArticle {
  /** 숫자가 `⟨수치⟩`로 가려진 제목 */
  title: string;
  url: string;
  press: string;
  date: string;
}

/** 기사에서 온 추측. **검증한 것이 아니라 되짚을 수 있는 것이다** (D45). */
export interface AskHint {
  text: string;
  articles: AskArticle[];
}

/**
 * 다음 질문에 되돌려 줄 것.
 *
 * **서버는 대화를 안 들고 있다.** 화면이 이걸 들고 있다가 다음 요청에 그대로
 * 넘긴다 — 워커가 늘어나도 대화가 갈라지지 않는다.
 */
export interface AskContext {
  symbols: string[];
  tokens: string[];
  year: number | null;
}

export interface Answer {
  /** 검증 레인만(`facts` + `analysis`). **힌트는 여기 없다.** */
  text: string;
  facts: string;
  analysis: string;
  hints: AskHint[];
  sources: AskSource[];
  unanswered: string[];
  /** 근거에 연결됐는가. 확신도가 아니다 */
  grounded: boolean;
  rejected: string[];
  unsourced: string[];
  /** D4로 답을 안 낸 이유. 비어 있지 않으면 답이 없다 */
  refused: string;
  cards: { tag: string; card_id: string; symbol: string; company: string }[];
  context: AskContext;
  /** 직전 턴에서 이어받은 것. **비어 있지 않으면 반드시 보여준다** */
  carried_over: string[];
  used_llm: boolean;
  model: string;
  cost_usd: number | null;
  problems: string[];
}

export async function ask(
  question: string,
  context: AskContext | null,
): Promise<Answer> {
  const r = await api(`/api/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, context }),
  });
  if (!r.ok) await fail(r);
  return r.json();
}

/* ── 피어 카드 ──────────────────────────────────────────────────────
 *
 * 인터뷰가 먼저 꺼낸 고통이 **커버 밖 종목**이었다. 업종 분류로는 못 찾는다 —
 * 방산 4종목이 KSIC 어느 자릿수에서도 한 그룹이 안 된다 (D68).
 */

/** 피어 카드의 구성원 한 줄. `card_id`가 비면 아직 종목 카드가 없다는 뜻. */
export interface PeerMember {
  symbol: string;
  company: string;
  card_id: string;
  year: number;
  period: string;
  /** `pending` | `running` | `ready` | `failed` */
  status: string;
  error: string;
  gate_passed?: boolean;
}

/** 비교표의 칸 하나. **표시 문자열은 레지스트리에서 그대로 온다.** */
export interface PeerCell {
  display: string;
  key: string;
  card_id: string;
  value: number | null;
  absent: boolean;
}

export interface PeerRow {
  label: string;
  group: string;
  unit: string;
  cells: PeerCell[];
}

export interface PeerColumn {
  symbol: string;
  company: string;
  card_id: string;
  year: number;
  period: string;
  /** 「2026년 1분기 누적」 */
  basis: string;
  ready: boolean;
}

export interface PeerTable {
  columns: PeerColumn[];
  rows: PeerRow[];
  /** **기준 기간이 섞이면 표가 조용히 거짓말을 한다** */
  mixed_basis: boolean;
  note: string;
}

/** 씨앗과 같이 움직이는 종목 하나. **상관계수를 늘 함께 낸다.** */
export interface PeerCandidate {
  symbol: string;
  company: string;
  correlation: number;
  /** 상관을 낸 거래일 수 */
  overlap: number;
}

export interface PeerSuggestion {
  seeds: { symbol: string; company: string }[];
  candidates: PeerCandidate[];
  /** 후보끼리도 같이 움직이는가. 아니면 이 목록을 섹터로 읽으면 안 된다 */
  meaningful: boolean;
  cohesion: number;
  note: string;
  universe: number;
  source: string;
}

export async function suggestPeers(
  seeds: string[],
  top = 15,
): Promise<PeerSuggestion> {
  const r = await api(`/api/peers/suggest`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ seeds, top }),
  });
  if (!r.ok) await fail(r);
  return r.json();
}

export async function createPeerCard(
  name: string,
  symbols: string[],
  /** 카드가 없는 종목은 **수치만** 자동으로 채운다 — 리포트를 안 쓴다 */
): Promise<{ card_id: string; members: PeerMember[]; filling: string[] }> {
  const r = await api(`/api/peers`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, symbols }),
  });
  if (!r.ok) await fail(r);
  return r.json();
}

export async function setPeerMembers(
  cardId: string,
  symbols: string[],
): Promise<{ members: PeerMember[]; attention: string[]; filling: string[] }> {
  const r = await api(`/api/cards/${cardId}/members`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ symbols }),
  });
  if (!r.ok) await fail(r);
  return r.json();
}

/** 피어 그룹 이름을 고친다. **이름이 곧 그 그룹의 정체다.** */
export async function renameCard(
  cardId: string,
  name: string,
): Promise<{ company: string }> {
  const r = await api(`/api/cards/${cardId}/rename`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  if (!r.ok) await fail(r);
  return r.json();
}

/* ── 내 커버리지 ────────────────────────────────────────────────────
 *
 * RA는 같은 20~30종목을 몇 년 본다. 어느 섹터를 맡고, 어느 종목에 리포트를
 * 내고, 어느 피어를 옆에 두는지가 그 사람의 자산이고, 그게 쌓이지 않으면
 * 로그인할 이유가 없다.
 *
 * **LLM이 만든 요약은 여기 없다.** 빠뜨린 게 아니라 정한 것이다 — 필요하면
 * 그때 다시 만들면 되고, 쌓아 두면 틀린 것이 굳는다.
 */

/**
 * `cover` 내가 리포트를 낸다 · `watch` 옆에서 본다.
 *
 * 「발간 여부」 체크박스였던 것을 고쳤다 — **커버 종목이면 리포트를 내는 것이
 * 자명해서** 물을 이유가 없었다. 실제로 갈리는 축은 내가 책임지느냐다.
 */
export type CoverKind = "cover" | "watch";

export interface Covered {
  symbol: string;
  company: string;
  sector: string;
  kind: CoverKind;
  note: string;
  added_at: string;
}

/** 한 구간의 등락. **쓴 날짜를 함께 낸다.** */
export interface Move {
  key: string;
  label: string;
  change_pct: number | null;
  from_date: string;
  to_date: string;
  days: number;
  /** 요청보다 짧게 잡혔는가 — 신규 상장이면 「1년」이 실제로는 3개월이다 */
  partial: boolean;
}

export interface Moves {
  symbol: string;
  company: string;
  last_close: number | null;
  last_date: string;
  items: Move[];
}

export async function getMoves(symbols: string[]): Promise<Moves[]> {
  if (!symbols.length) return [];
  const r = await api(`/api/prices/moves?symbols=${symbols.join(",")}`);
  if (!r.ok) return [];
  return (await r.json()).moves ?? [];
}

/** 등락률 표시. **부호가 먼저다.** */
export function fmtPct(pct: number | null): string {
  return pct == null ? "—" : `${pct >= 0 ? "+" : ""}${pct.toFixed(1)}%`;
}

export interface PeerGroupRef {
  card_id: string;
  name: string;
  member_count: number;
  pinned: boolean;
}

export interface ProfileData {
  uid: string;
  display_name: string;
  /** 자유 텍스트다 — 표준 분류로는 못 적는다는 것이 D68의 결론이다 */
  sectors: string[];
  stocks: Covered[];
  pinned_peers: string[];
  updated_at: string;
  /** 카드에서 끌어온 것 — 사람에게 두 번 묻지 않는다 */
  peer_groups: PeerGroupRef[];
  with_cards: string[];
  /** 종목별 기간 등락 — 받아 둔 시세로 나눗셈만 한 것 */
  moves: Moves[];
}

export async function getProfile(): Promise<ProfileData> {
  const r = await api(`/api/profile`);
  if (!r.ok) await fail(r);
  return r.json();
}

export async function saveProfile(patch: {
  display_name?: string;
  sectors?: string[];
  stocks?: { symbol: string; sector?: string; kind?: CoverKind; note?: string }[];
}): Promise<ProfileData> {
  const r = await api(`/api/profile`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!r.ok) await fail(r);
  return r.json();
}

/** 피어 그룹 고정 (D68) — 확정한 그룹은 매번 재계산하지 않는다. */
export async function pinPeerGroup(
  cardId: string,
  pinned: boolean,
): Promise<{ pinned_peers: string[] }> {
  const r = await api(`/api/profile/pin`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ card_id: cardId, pinned }),
  });
  if (!r.ok) await fail(r);
  return r.json();
}

/* ── 모닝 브리프 ────────────────────────────────────────────────────
 *
 * 인터뷰의 말이 그대로 요구다: *"이것만 아침에 해줘도 되는데"*.
 * **LLM을 안 쓴다** — 브리프는 서술이 아니라 배열이다.
 */

export interface BriefFiling {
  title: string;
  filed_at: string;
  url: string;
}

/** 기사는 **미검증 레인**이다 (D45). 숫자를 여기서 읽지 않는다. */
export interface BriefArticle {
  title: string;
  url: string;
  date: string;
}

export interface BriefLine {
  symbol: string;
  company: string;
  sector: string;
  kind: CoverKind;
  last_close: number | null;
  moves: Move[];
  filings: BriefFiling[];
  articles: BriefArticle[];
  /** 구간별 시장 대비 초과(%p). **아침에 알고 싶은 것은 이쪽이다** */
  excess: Record<string, number>;
}

/** 코스피·코스닥 실제 지수. **아침 회의가 여기서 시작한다.** */
export interface MarketIndex {
  name: string;
  date: string;
  close: number | null;
  change_pct: number | null;
  members: number | null;
}

/** 섹터 한 줄. **내 종목들의 중앙값**이지 섹터 지수가 아니다. */
export interface SectorLine {
  sector: string;
  count: number;
  moves: Move[];
  /** 구간별 시장 대비 초과(%p) */
  excess: Record<string, number>;
}

export interface Brief {
  /** 시세 기준일 (YYYYMMDD) */
  asof: string;
  /** 「어제(8/6 목)」 — **언제 얘기인지가 먼저다** */
  asof_label: string;
  indices: MarketIndex[];
  /** 동일가중 중앙값 지수의 기간 등락 */
  market: Move[];
  market_label: string;
  sectors: SectorLine[];
  cover: BriefLine[];
  watch: BriefLine[];
  /** 맨 위 한 줄. **없으면 없다고 말한다** */
  note: string;
}

export async function getBrief(): Promise<Brief> {
  const r = await api(`/api/brief`);
  if (!r.ok) await fail(r);
  return r.json();
}
