/**
 * 정기보고서 선택지.
 *
 * **RA는 연도를 고르지 않는다.** 방금 올라온 보고서에 반응한다. 그래서 축이
 * 「사업연도」라는 숫자가 아니라 **어느 보고서인가**여야 한다.
 *
 * 12월 결산 기준 법정 제출기한 (자본시장법):
 *
 * | 보고서 | 기한 | 대략 |
 * |---|---|---|
 * | 1분기 | 분기 경과 후 45일 | 5월 중순 |
 * | 반기   | 반기 경과 후 45일 | 8월 중순 |
 * | 3분기 | 분기 경과 후 45일 | 11월 중순 |
 * | 사업   | 사업연도 경과 후 90일 | 이듬해 3월 말 |
 *
 * 기한이므로 그 전에 나올 수도 있다. 여기서는 **기한이 지난 것만** 기본값으로
 * 삼는다 — 아직 안 나왔을 보고서를 기본으로 걸면 첫 시도가 실패한다.
 *
 * 결산월이 12월이 아닌 회사는 이 계산이 어긋난다. 그때는 목록에서 직접 고른다.
 */

export type PeriodCode = "Q1" | "HALF" | "Q3" | "ANNUAL";

export interface ReportPeriod {
  year: number;
  period: PeriodCode;
  label: string;
  /** 법정 제출기한. 지났으면 나와 있을 것으로 본다. */
  due: Date;
}

const LABEL: Record<PeriodCode, string> = {
  Q1: "1분기보고서",
  HALF: "반기보고서",
  Q3: "3분기보고서",
  ANNUAL: "사업보고서",
};

function periodsOfYear(year: number): ReportPeriod[] {
  return [
    { year, period: "Q1", label: `${year} ${LABEL.Q1}`, due: new Date(year, 4, 15) },
    { year, period: "HALF", label: `${year} ${LABEL.HALF}`, due: new Date(year, 7, 14) },
    { year, period: "Q3", label: `${year} ${LABEL.Q3}`, due: new Date(year, 10, 14) },
    // 사업보고서만 이듬해에 나온다
    { year, period: "ANNUAL", label: `${year} ${LABEL.ANNUAL}`, due: new Date(year + 1, 2, 31) },
  ];
}

/** 최신이 위로. 기본 3년치면 충분하다 — 그보다 과거는 실적 리뷰의 대상이 아니다. */
export function reportPeriods(today = new Date(), years = 3): ReportPeriod[] {
  const y = today.getFullYear();
  const all: ReportPeriod[] = [];
  for (let i = 0; i <= years; i++) all.push(...periodsOfYear(y - i));
  return all.sort((a, b) => b.due.getTime() - a.due.getTime());
}

/**
 * 기본값 — **기한이 지난 것 중 가장 최신.** 종류를 가리지 않는다.
 *
 * 한때 사업보고서만 기본값으로 삼았는데 틀렸다. 그러면 종목당 리포트가 1년에
 * 한 번이 되는데, 코퍼스 7,410건을 세어보니 **종목·연도당 중앙값 2건 ·
 * 평균 5.0건**이고 발간이 **연 4파도**로 몰린다(1~2월 · 4~5월 · 7~8월 ·
 * 10~11월). 어닝시즌마다 쓰는 것이지 연 1회가 아니다.
 */
export function defaultPeriod(today = new Date()): ReportPeriod {
  const all = reportPeriods(today);
  return all.find((p) => p.due <= today) ?? all[all.length - 1];
}

/** 기한이 지났는가 — 안 지났으면 아직 안 나왔을 수 있다. */
export function isFiled(p: ReportPeriod, today = new Date()): boolean {
  return p.due <= today;
}

/**
 * 정기보고서 주요정보 6종(주식수·배당·감사의견·인력·지분·출자)이 있는가.
 *
 * **분기에도 부문 손익은 나온다** — 실측: 삼성전자 2026 1분기에서 부문 4개·
 * 수치 66건. 다만 위 6종은 연간 공시라 분기에는 0/6이다. 못 쓸 경로가 아니라
 * **덜 들어 있는 경로**이므로, 막지 말고 무엇이 빠지는지만 말한다.
 */
export function hasPeriodicInfo(p: { period: PeriodCode }): boolean {
  return p.period === "ANNUAL";
}

export function periodKey(p: { year: number; period: PeriodCode }): string {
  return `${p.year}:${p.period}`;
}
