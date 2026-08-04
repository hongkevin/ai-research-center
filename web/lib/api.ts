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
