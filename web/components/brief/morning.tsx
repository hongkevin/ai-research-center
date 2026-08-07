"use client";

import { useEffect, useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { fmtPct, getBrief, type Brief, type BriefLine } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * 모닝 브리프 — **아침에 이것만 봐도 되게.**
 *
 * 인터뷰의 말이 그대로 요구다: *"이것만 아침에 해줘도 되는데"*.
 *
 * **요약 문장이 없다.** 브리프는 서술이 아니라 배열이다 — 크게 움직인 것을
 * 위로 올리고 그 옆에 공시와 기사를 놓는 것이 전부다. 문장으로 요약하면
 * 비용이 들고, 틀릴 여지가 생기고, **RA가 원문을 안 보게 된다.** 아침에
 * 필요한 것은 판단이 아니라 **놓친 것이 없다는 확인**이다.
 */

/** 「크게 움직였다」의 기준(%). 서버의 `NOTABLE`과 같은 값이다. */
const NOTABLE = 3.0;

export function MorningBrief({
  onOpenCoverage,
}: {
  onOpenCoverage: () => void;
}) {
  const [data, setData] = useState<Brief | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const b = await getBrief();
        if (alive) setData(b);
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  if (error) {
    return (
      <Alert variant="destructive" className="max-w-[720px]">
        <AlertDescription>{error}</AlertDescription>
      </Alert>
    );
  }
  if (!data) {
    return (
      <p className="text-[13px] text-muted-foreground">
        공시와 기사를 읽는 중… 처음 한 번은 몇 초 걸립니다.
      </p>
    );
  }

  const empty = data.cover.length === 0 && data.watch.length === 0;

  return (
    <div className="max-w-[900px] space-y-7">
      <div>
        <p className="text-[15px] font-medium">{data.note}</p>
        {data.asof && (
          <p className="mt-1 text-[11.5px] text-muted-foreground">
            종가 {data.asof.slice(0, 4)}-{data.asof.slice(4, 6)}-
            {data.asof.slice(6, 8)} 기준 · 공시는 최근 3일 ·{" "}
            <strong>기사는 검증된 것이 아닙니다</strong>
          </p>
        )}
      </div>

      {empty && (
        <button
          type="button"
          onClick={onOpenCoverage}
          className="w-full rounded-lg border border-dashed px-4 py-6 text-left transition-colors hover:bg-accent/30"
        >
          <p className="text-[14px] font-medium">커버 종목을 먼저 넣으십시오.</p>
          <p className="mt-1 text-[12.5px] text-muted-foreground">
            「내 커버리지」에서 정하면 그때부터 여기 뜹니다. →
          </p>
        </button>
      )}

      {data.cover.length > 0 && <Section title="커버 종목" lines={data.cover} />}
      {data.watch.length > 0 && (
        <Section title="관심 종목" lines={data.watch} muted />
      )}
    </div>
  );
}

function Section({
  title,
  lines,
  muted = false,
}: {
  title: string;
  lines: BriefLine[];
  muted?: boolean;
}) {
  return (
    <section>
      <h3 className="mb-2 border-b pb-1.5 text-[12px] font-semibold">
        {title}{" "}
        <span className="font-mono font-normal text-muted-foreground">
          {lines.length}
        </span>
      </h3>
      <div className="divide-y">
        {lines.map((l) => (
          <Line key={l.symbol} line={l} muted={muted} />
        ))}
      </div>
    </section>
  );
}

function Line({ line: l, muted }: { line: BriefLine; muted: boolean }) {
  const day = l.moves.find((m) => m.key === "1d")?.change_pct ?? null;
  const notable =
    l.filings.length > 0 || (day != null && Math.abs(day) >= NOTABLE);
  return (
    <div className={cn("py-2.5", muted && "opacity-80")}>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        {/* **눈에 띄어야 하는 것만 표시한다.** 전부 강조하면 아무것도 강조가 아니다 */}
        <span className="text-[13.5px] font-medium">
          {notable && <span className="mr-1 text-warn">★</span>}
          {l.company}
        </span>
        <span className="font-mono text-[11px] text-muted-foreground">
          {l.symbol}
          {l.sector && ` · ${l.sector}`}
        </span>
        <span className="ml-auto flex gap-2.5 font-mono text-[12px] tabular-nums">
          {l.moves.map((m) => (
            <span
              key={m.key}
              title={
                m.change_pct == null
                  ? "비교할 자료가 없습니다"
                  : `${m.from_date} → ${m.to_date}`
              }
            >
              <span className="text-[10.5px] text-muted-foreground">
                {m.label}{" "}
              </span>
              <span
                className={cn(
                  m.change_pct == null
                    ? "text-muted-foreground"
                    : m.change_pct > 0
                      ? "text-bad"
                      : m.change_pct < 0
                        ? "text-num"
                        : "text-muted-foreground",
                )}
              >
                {fmtPct(m.change_pct)}
              </span>
            </span>
          ))}
        </span>
      </div>

      {l.filings.length > 0 && (
        <ul className="mt-1.5 space-y-0.5">
          {l.filings.map((f, i) => (
            <li key={i} className="text-[12.5px]">
              <span className="mr-1.5 text-[10.5px] text-ok">공시</span>
              <a
                href={f.url}
                target="_blank"
                rel="noreferrer"
                className="underline-offset-2 hover:underline"
              >
                {f.title}
              </a>
              <span className="ml-1.5 text-[11px] text-muted-foreground">
                {f.filed_at.slice(5, 10)}
              </span>
            </li>
          ))}
        </ul>
      )}

      {/* **기사는 다른 줄에 다른 색으로.** 합치면 「보도됐다」가 「공시됐다」로 읽힌다 */}
      {l.articles.length > 0 && (
        <ul className="mt-1 space-y-0.5">
          {l.articles.map((a, i) => (
            <li key={i} className="text-[12px] text-muted-foreground">
              <span className="mr-1.5 text-[10.5px] text-warn">기사</span>
              <a
                href={a.url}
                target="_blank"
                rel="noreferrer"
                className="underline-offset-2 hover:underline"
              >
                {a.title}
              </a>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
