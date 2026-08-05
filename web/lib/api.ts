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

const BASE = process.env.NEXT_PUBLIC_API_BASE ?? "";

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
}

export interface CardDetail extends Omit<CardSummary, "gate_passed" | "registry_size" | "stage_count"> {
  vm: ViewModel;
}

export async function listCards(): Promise<CardSummary[]> {
  const r = await fetch(`${BASE}/api/cards`);
  if (!r.ok) return [];
  return (await r.json()).cards ?? [];
}

export async function getCard(id: string): Promise<CardDetail> {
  const r = await fetch(`${BASE}/api/cards/${id}`);
  if (!r.ok) await fail(r);
  return r.json();
}

/** 「확인함」 — 확인 필요를 벗어난다. 칸 배정 자체는 서버가 자동으로 한다. */
export async function confirmCard(id: string): Promise<CardSummary> {
  const r = await fetch(`${BASE}/api/cards/${id}/confirm`, { method: "POST" });
  if (!r.ok) await fail(r);
  return r.json();
}

export async function deleteCard(id: string): Promise<void> {
  const r = await fetch(`${BASE}/api/cards/${id}`, { method: "DELETE" });
  if (!r.ok) await fail(r);
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
  const r = await fetch(`${BASE}/api/search?q=${encodeURIComponent(q)}&limit=${limit}`);
  if (!r.ok) return [];
  const d = await r.json();
  return d.results ?? [];
}

export async function startJob(req: JobRequest): Promise<string> {
  const r = await fetch(`${BASE}/api/jobs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!r.ok) await fail(r);
  const d = await r.json();
  return d.job_id;
}

export async function fetchResult(jobId: string): Promise<ViewModel> {
  const r = await fetch(`${BASE}/api/jobs/${jobId}/result`);
  if (!r.ok) await fail(r);
  return r.json();
}

export function eventsUrl(jobId: string): string {
  return `${BASE}/api/jobs/${jobId}/events`;
}
