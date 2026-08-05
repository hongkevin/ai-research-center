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
  segment_chart: string;
  segment_legend: string;
  trend_chart: string;
  trend_legend: string;
  industry_context: boolean;
  llm_used: boolean;
  llm_model: string;
  llm_cost: number | null;
  published_path: string;
  notice: string;
  error: string;
}

/** 보드의 칸. 순서가 곧 왼→오. */
export const COLUMNS = ["running", "attention", "review", "published"] as const;
export type Column = (typeof COLUMNS)[number];

export const COLUMN_LABEL: Record<Column, string> = {
  running: "수집됨",
  // 카드가 실제로 쌓이는 곳. 다른 데서 못 하는 일(1차 공시 대조)이 여기 있다.
  attention: "확인 필요",
  review: "검토 대기",
  published: "발간됨",
};

/** 목록용 — 본문(60KB)이 빠져 있다. */
export interface CardSummary {
  id: string;
  symbol: string;
  year: number;
  created_at: string;
  column: Column;
  confirmed: boolean;
  company: string;
  attention: string[];
  error: string;
  gate_passed: boolean;
  registry_size: number;
  stage_count: number;
  version: string;
  revision_count: number;
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

export interface CardDetail
  extends Omit<CardSummary, "gate_passed" | "registry_size" | "stage_count" | "revision_count"> {
  vm: ViewModel;
  versions: Revision[];
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

export async function listSections(id: string): Promise<{ version: string; sections: DocSection[] }> {
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

export interface JobRequest {
  symbol: string;
  year: number;
  llm: boolean;
  assume: string;
  publish: boolean;
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

export async function searchCompanies(q: string, limit = 10): Promise<CompanyHit[]> {
  const r = await api(`/api/search?q=${encodeURIComponent(q)}&limit=${limit}`);
  if (!r.ok) return [];
  const d = await r.json();
  return d.results ?? [];
}

export async function startJob(req: JobRequest): Promise<string> {
  const r = await api(`/api/jobs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!r.ok) await fail(r);
  const d = await r.json();
  return d.job_id;
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
