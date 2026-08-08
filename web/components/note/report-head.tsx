"use client";

import type { ViewModel } from "@/lib/api";

/**
 * 리포트 첫 화면 — **Signal / Key / Step + STOCK DATA** (D87).
 *
 * 왜 이 꼴인가
 * -----------
 * 국내 리서치의 미드스몰캡 노트는 본문 앞에 이 블록을 세운다. 실물 3편에서
 * 확인한 구조 그대로다:
 *
 *     Signal: 실적 및 멀티플 동반 개선 국면 진입
 *     Key:    블룸에너지향 반복 수주 및 실적 인식 본격화
 *     Step:   26년 수주 원년, 27년 실적 반영
 *     STOCK DATA   주가 · 52주 최고가 · 60일 평균 거래대금
 *     COMPANY DATA 발행주식수 · 시가총액 · 최대주주 지분율
 *
 * 읽는 사람이 세 줄만 보고 넘어가는 일이 많아서, 이건 요약의 요약이 아니라
 * **글의 뼈대**다.
 *
 * 두 블록은 성격이 다르다
 * -----------------------
 * * 세 줄은 **LLM이 쓴 글**이다 — 본문과 같은 게이트를 거치고 숫자는
 *   레지스트리에서 온다. 못 쓰면 비고, 그러면 이 칸을 안 세운다
 * * STOCK DATA는 **측정값**이다 — LLM을 아예 안 거친다
 *
 * **투자의견·목표주가 자리는 없다** (D4). 그 자리에 「Not Rated」라고 적는다 —
 * 빼는 것이 아니라 **우리가 무엇을 안 내는지 말하는 것**이다. 미드스몰캡
 * 노트에도 Not Rated 리포트가 있다(탐방노트가 그렇다).
 */
export function ReportHead({ vm }: { vm: ViewModel }) {
  const h = vm.headline ?? {};
  const d = vm.stock_data;
  const lines: [string, string | undefined][] = [
    ["Signal", h.signal],
    ["Key", h.key],
    ["Step", h.step],
  ];
  const hasLines = lines.some(([, v]) => v);
  if (!hasLines && !d) return null;

  return (
    <section className="mb-8 max-w-[860px] rounded-lg border">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b bg-muted/30 px-4 py-2">
        <span className="text-[12px] font-semibold">{vm.company}</span>
        <span className="font-mono text-[11px] text-muted-foreground">
          {vm.symbol}
          {d?.board && ` · ${d.board}`}
        </span>
        {/* **투자의견을 안 낸다는 것을 말한다** (D4). 빈칸으로 두면 「깜빡한
            것」으로 읽히고, 이 제품이 무엇인지가 안 드러난다. */}
        <span className="ml-auto rounded border px-1.5 py-0.5 text-[10.5px] text-muted-foreground">
          Not Rated · 목표주가 없음
        </span>
      </div>

      {hasLines && (
        <dl className="divide-y">
          {lines.map(([label, value]) =>
            value ? (
              <div key={label} className="flex gap-3 px-4 py-2">
                <dt className="w-[46px] shrink-0 text-[11px] font-semibold text-num">
                  {label}
                </dt>
                {/* 숫자는 레지스트리 span으로 와서 클릭하면 출처가 열린다 —
                    본문과 같은 글이다 */}
                <dd
                  className="text-[13px] leading-[1.7]"
                  dangerouslySetInnerHTML={{ __html: value }}
                />
              </div>
            ) : null,
          )}
        </dl>
      )}

      {d && <StockData data={vm.stock_data} />}
    </section>
  );
}

function StockData({ data: d }: { data: ViewModel["stock_data"] }) {
  const rows: [string, string][] = [];
  if (d.cap_display) rows.push(["시가총액", d.cap_display]);
  if (d.shares) rows.push(["발행주식수", `${(d.shares / 1e4).toLocaleString(undefined, { maximumFractionDigits: 0 })}만주`]);
  if (d.avg_turnover != null)
    rows.push([
      `${d.turnover_days}일 평균 거래대금`,
      `${(d.avg_turnover / 1e8).toLocaleString(undefined, { maximumFractionDigits: 0 })}억원`,
    ]);
  if (d.high_52w != null)
    rows.push([
      "52주 최고가",
      `${d.high_52w.toLocaleString()}원${d.high_basis ? ` (${d.high_basis})` : ""}`,
    ]);
  if (d.owner_stake != null)
    rows.push([
      "최대주주 지분율",
      `${d.owner_stake.toFixed(2)}%${d.owner ? ` · ${d.owner}` : ""}`,
    ]);

  if (rows.length === 0 && d.unavailable.length === 0) return null;

  return (
    <div className="border-t px-4 py-2.5">
      <div className="grid grid-cols-2 gap-x-6 gap-y-1 sm:grid-cols-3">
        {rows.map(([label, value]) => (
          <div key={label} className="flex items-baseline justify-between gap-2">
            <span className="text-[11px] text-muted-foreground">{label}</span>
            <span className="font-mono text-[12px] tabular-nums">{value}</span>
          </div>
        ))}
      </div>
      {/* **왜 비었는지 적는다.** 빈칸만 있으면 「0」인지 「모른다」인지 알 수
          없고, 외국인 지분율은 우리가 게을러서가 아니라 공개 API에 없다. */}
      {d.unavailable.length > 0 && (
        <p className="mt-2 text-[10.5px] leading-[1.7] text-muted-foreground">
          못 낸 것: {d.unavailable.join(" · ")}
        </p>
      )}
      {d.asof && (
        <p className="mt-1 text-[10.5px] text-muted-foreground">
          시장 데이터 기준일 {d.asof.slice(0, 4)}-{d.asof.slice(4, 6)}-
          {d.asof.slice(6)}
        </p>
      )}
    </div>
  );
}
