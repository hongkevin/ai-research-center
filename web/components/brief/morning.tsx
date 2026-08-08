"use client";

import { useEffect, useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  fmtPct,
  getBrief,
  BRIEF_SESSION_LABEL,
  BRIEF_SESSIONS,
  type Brief,
  type BriefLine,
  type MacroPoint,
  type Move,
  type SectorLine,
  type Session,
} from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * 모닝 브리프 — **아침에 이것만 봐도 되게.**
 *
 * 인터뷰의 말이 그대로 요구다: *"이것만 아침에 해줘도 되는데"*.
 *
 * **세 칸이다** — 왼쪽 1/5 매크로, 가운데 2/5 섹터, 오른쪽 2/5 종목. 아침
 * 회의가 그 순서로 간다: 오늘 한국 증시·환율·금리가 어땠고, 내 섹터가 어땠고,
 * 그래서 내 종목이 어땠나. 세로로 쌓으면 셋을 **한눈에 견주지 못한다** —
 * 섹터가 빠졌는데 시장이 더 빠졌는지는 나란히 놔야 보인다.
 *
 * **칸마다 맨 위에 한 줄이 있고 그 밑에 디테일이 온다.** 그 한 줄은 서술이
 * 아니라 **아래 숫자를 다시 읽은 것**이다 — 새 사실이 생기지 않고, 틀릴
 * 여지가 없고, 값이 없으면 그 절이 통째로 빠진다. 판단(「그래서 무엇을 봐야
 * 하나」)은 여기 없다. 그건 미검증 레인이라 배지를 달고 따로 나가야 한다.
 */

/** 「크게 움직였다」의 기준(%). 서버의 `NOTABLE`과 같은 값이다. */
const NOTABLE = 3.0;

export function MorningBrief({
  onOpenCoverage,
  onAsk,
}: {
  onOpenCoverage: () => void;
  /** 종목을 눌러 그 자리에서 묻는다 (D86) */
  onAsk?: (company: string) => void;
}) {
  const [data, setData] = useState<Brief | null>(null);
  const [error, setError] = useState("");
  // **지금 시각이 정한다.** 고르는 것은 그 다음이다 — 아침에 열면 모닝이다
  const [want, setWant] = useState<Session | null>(null);

  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const b = await getBrief(want ?? undefined);
        if (alive) setData(b);
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => {
      alive = false;
    };
  }, [want]);

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
    <div className="space-y-5">
      {/* **언제 얘기인지가 먼저다.** 「1일 -2.4%」만 있으면 오늘인지 어제인지
          모른다 — EOD 시세라 장 마감 전에는 어제 종가가 최신이다. */}
      <div className="flex flex-wrap items-baseline gap-x-3">
        <span className="text-[15px] font-medium">
          {data.session_label}
        </span>
        {/* **장중에는 「종가 기준」이라고 안 적는다.** 오늘 종가가 없다 */}
        <span className="text-[12.5px] text-muted-foreground">
          {data.session === "midday"
            ? data.session_why
            : `${data.asof_label || "시세 없음"} 종가 기준`}
        </span>
        <span className="text-[12.5px] text-muted-foreground">{data.note}</span>

        {/* 하루에 세 번. **지금 시각이 기본을 정하고**, 눌러서 옮긴다 */}
        <span className="ml-auto flex rounded-md border p-0.5">
          {BRIEF_SESSIONS.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => setWant(s)}
              className={cn(
                "rounded px-2 py-0.5 text-[11.5px] transition-colors",
                data.session === s
                  ? "bg-accent font-medium"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {BRIEF_SESSION_LABEL[s]}
            </button>
          ))}
        </span>
      </div>

      {/* **못 읽은 것을 맨 위에 둔다.** 이 화면은 「놓친 것이 없다」는 확인이
          목적이라, 그 확인이 불완전하다는 사실이 가장 먼저 보여야 한다. */}
      {(data.unavailable?.length ?? 0) > 0 && (
        <div className="rounded-lg border border-warn px-3.5 py-2.5">
          <p className="text-[12.5px] leading-[1.75]">
            <strong className="text-warn">
              {data.unavailable.join(" · ")}을(를) 못 읽었습니다.
            </strong>{" "}
            아래 화면이 전부가 아닙니다 — 「없다」가 아니라 <strong>확인하지
            못했다</strong>는 뜻입니다.
          </p>
        </div>
      )}

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

      {/* **수주는 칸 위가 아니라 화면 맨 위다** (D87).
          미드스몰캡 애널리스트의 리포트는 수주로 시작한다 — 코세스는 블룸
          에너지 1,504억, 씨이랩은 삼성SDS 3,151억이 그 리포트의 첫 문장이다.
          전에는 「단일판매ㆍ공급계약체결」 여섯 글자가 다른 공시 열 건과 같은
          크기로 목록에 섞여 지나갔다. */}
      <Contracts brief={data} onAsk={onAsk} />

      {/* 1/5 · 2/5 · 2/5. 좁은 화면에서는 위아래로 떨어진다 */}
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-5">
        <Column
          className="lg:col-span-1"
          title="시장"
          head={data.heads.macro}
          hint="지수·환율·금리"
        >
          <Macro brief={data} />
        </Column>

        <Column
          className="lg:col-span-2"
          title="섹터"
          head={data.heads.sectors}
          hint={
            data.sectors.some((s) => s.basis === "peer")
              ? "피어 그룹 전체의 중앙값"
              : "내 종목의 중앙값"
          }
        >
          {data.sectors.length > 0 ? (
            <div className="divide-y">
              {data.sectors.map((s) => (
                <SectorRow key={s.sector} row={s} />
              ))}
            </div>
          ) : data.watch.length > 0 ? (
            /* **거짓말하지 않는다** (D86). 섹터는 커버 종목으로만 만든다.
               시드에서 들어온 종목은 전부 「관심」이라, 섹터를 넣고 종목이
               열둘인데도 「섹터를 넣으면 여기 뜹니다」라고 말했다. 진짜
               필요한 행동은 「관심 → 커버로 옮기기」인데 그 말이 없었다. */
            <button
              type="button"
              onClick={onOpenCoverage}
              className="w-full rounded-md border border-dashed px-3 py-3 text-left text-[12px] leading-[1.8] text-muted-foreground transition-colors hover:bg-accent/30"
            >
              섹터 줄은 <strong>커버 종목</strong>으로 만듭니다. 지금은 전부
              「관심」이라 낼 것이 없습니다 — 리포트를 낼 종목을 커버로
              옮기십시오. →
            </button>
          ) : (
            <Nothing>섹터를 넣으면 여기 뜹니다.</Nothing>
          )}
        </Column>

        <Column
          className="lg:col-span-2"
          title="종목"
          head={data.heads.stocks}
          hint={`괄호는 ${data.market_label} 대비(%p)`}
        >
          {data.cover.length > 0 && (
            <Section title="커버" lines={data.cover} onAsk={onAsk} />
          )}
          {data.watch.length > 0 && (
            <Section title="관심" lines={data.watch} muted onAsk={onAsk} />
          )}
          {!data.cover.length && !data.watch.length && (
            <Nothing>커버 종목을 넣으면 여기 뜹니다.</Nothing>
          )}
        </Column>
      </div>

      <p className="text-[11.5px] leading-[1.7] text-muted-foreground">
        공시는 최근 3일 · <strong>기사는 검증된 것이 아닙니다</strong> · 칸마다
        맨 위 줄은 아래 숫자를 다시 읽은 것이라 <strong>새 사실이 없습니다</strong>
        {data.market.length > 0 && (
          <>
            {" · "}
            {data.market_label}{" "}
            {data.market
              .filter((m) => m.change_pct != null)
              .map((m) => `${m.label} ${fmtPct(m.change_pct)}`)
              .join(" ")}
          </>
        )}
      </p>
    </div>
  );
}

/**
 * 칸 하나 — **맨 위 한 줄, 그 밑에 디테일.**
 *
 * 요구가 정확히 이 모양이었다: *"한 줄 요약(실제로는 2~3줄?)씩 칸마다 맨 위에
 * 있고, 그 다음에 밑에 디테일이 나오는 형태"*.
 */
function Column({
  title,
  head,
  hint,
  className,
  children,
}: {
  title: string;
  head?: string;
  hint?: string;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <section className={cn("min-w-0", className)}>
      <div className="mb-2 border-b pb-1.5">
        <h3 className="text-[12px] font-semibold">
          {title}
          {hint && (
            <span className="ml-1.5 font-normal text-muted-foreground">
              {hint}
            </span>
          )}
        </h3>
      </div>
      {/* 요약 줄. **없으면 자리도 없다** */}
      {head && (
        <p className="mb-2.5 text-[12.5px] leading-[1.75]">{head}</p>
      )}
      {children}
    </section>
  );
}

function Nothing({ children }: { children: React.ReactNode }) {
  return (
    <p className="rounded-md border border-dashed px-3 py-3 text-[12px] text-muted-foreground">
      {children}
    </p>
  );
}

/** 시장 칸 — 지수 + 환율·금리. **날짜가 다르면 다르다고 적는다.** */
function Macro({ brief }: { brief: Brief }) {
  if (brief.indices.length === 0 && brief.macro.length === 0) {
    return <Nothing>지수·환율을 아직 못 받았습니다.</Nothing>;
  }
  return (
    <div className="divide-y">
      {brief.indices.map((i) => (
        <div key={i.name} className="flex items-baseline gap-2 py-1.5">
          <span className="text-[12.5px] text-muted-foreground">{i.name}</span>
          <span className="ml-auto font-mono text-[12.5px] tabular-nums">
            {i.close?.toLocaleString(undefined, { maximumFractionDigits: 2 })}
          </span>
          <span
            className={cn(
              "w-[54px] text-right font-mono text-[11.5px] tabular-nums",
              (i.change_pct ?? 0) > 0
                ? "text-bad"
                : (i.change_pct ?? 0) < 0
                  ? "text-num"
                  : "text-muted-foreground",
            )}
          >
            {fmtPct(i.change_pct)}
          </span>
        </div>
      ))}
      {brief.macro.map((m) => (
        <MacroRow key={m.key} point={m} />
      ))}
    </div>
  );
}

function MacroRow({ point: m }: { point: MacroPoint }) {
  // **기준금리는 계단이라 「전일 대비」가 정보가 아니다.** 알고 싶은 것은
  // 「언제 올렸나」이고, 그래서 변경 시점을 대신 적는다.
  const stepped = !!m.changed_at;
  const when = stepped
    ? `${m.changed_at.slice(0, 4)}-${m.changed_at.slice(4, 6)}`
    : "";
  return (
    <div
      className="flex items-baseline gap-2 py-1.5"
      title={
        `${m.date} 기준` +
        (m.stale_days && m.stale_days > 1 ? ` · ${m.stale_days}일 전 값` : "") +
        (m.scope ? ` · ${m.scope}` : "")
      }
    >
      <span className="text-[12.5px] text-muted-foreground">
        {m.label}
        {/* **한계를 값 옆에 적는다.** 어딘가 주석으로 두면 안 읽힌다 */}
        {m.scope && (
          <span className="ml-1 text-[9.5px] text-warn" title={m.scope}>
            시장
          </span>
        )}
      </span>
      <span className="ml-auto font-mono text-[12.5px] tabular-nums">
        {m.display}
      </span>
      <span
        className={cn(
          "w-[54px] text-right font-mono text-[11.5px] tabular-nums",
          m.change == null || m.change === 0
            ? "text-muted-foreground"
            : m.change > 0
              ? "text-bad"
              : "text-num",
        )}
      >
        {m.change == null
          ? "—"
          : stepped
            ? when
            : `${m.change >= 0 ? "+" : ""}${m.change.toFixed(m.digits)}`}
      </span>
    </div>
  );
}

/**
 * 섹터 한 줄 — **모수를 밝힌다.**
 *
 * 피어 그룹 전체로 낸 것과 내 종목 3개로 낸 것은 완전히 다른 얘기고, 그 차이가
 * 화면에 없으면 둘 다 「섹터 지수」로 읽힌다.
 */
function SectorRow({ row: s }: { row: SectorLine }) {
  return (
    <div className="py-2">
      <div className="flex flex-wrap items-baseline gap-x-2.5">
        <span className="text-[13px] font-medium">{s.sector}</span>
        <span
          className={cn(
            "font-mono text-[10.5px]",
            s.basis === "peer" ? "text-ok" : "text-muted-foreground",
          )}
          title={
            s.basis === "peer"
              ? "그 섹터의 피어 그룹 전체로 냈습니다 — 「섹터가 어땠나」의 답입니다"
              : "피어 그룹이 없어 내 종목만으로 냈습니다 — 섹터 얘기가 아니라 내 것들 얘기입니다"
          }
        >
          {s.basis_label}
        </span>
        <span className="ml-auto flex gap-2.5 font-mono text-[12px] tabular-nums">
          {s.moves.map((m) => (
            <Pct key={m.key} move={m} excess={s.excess} />
          ))}
        </span>
      </div>
    </div>
  );
}

function Pct({
  move: m,
  excess,
}: {
  move: Move;
  excess?: Record<string, number>;
}) {
  // **시장 대비가 진짜 답이다.** 5% 빠진 것이 시장이 5% 빠져서인지 이
  // 종목만인지는 완전히 다른 얘기다.
  const over = excess?.[m.key];
  return (
    <span
      title={
        m.change_pct == null
          ? "비교할 자료가 없습니다"
          : `${m.from_date} → ${m.to_date}` +
            (over != null
              ? ` · 시장 대비 ${over >= 0 ? "+" : ""}${over.toFixed(1)}%p`
              : "")
      }
    >
      <span className="text-[10.5px] text-muted-foreground">{m.label} </span>
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
      {over != null && (
        <span className="ml-0.5 text-[10px] text-muted-foreground">
          ({over >= 0 ? "+" : ""}
          {over.toFixed(1)})
        </span>
      )}
    </span>
  );
}

function Contracts({
  brief,
  onAsk,
}: {
  brief: Brief;
  onAsk?: (company: string) => void;
}) {
  const rows = brief.cover.flatMap((line) =>
    line.contracts.map((c) => ({ line, c })),
  );
  // **없으면 칸을 세우지 않는다.** 「수주 0건」이 매일 떠 있으면 눈이 그
  // 자리를 지나치게 되고, 정작 난 날에도 안 보인다.
  if (rows.length === 0) return null;

  // 큰 것이 위로. **금액이 아니라 「최근 매출 대비」다** — 1,504억은 회사에
  // 따라 사소하기도 하고 회사를 바꾸기도 한다.
  rows.sort((a, b) => (b.c.ratio_pct ?? 0) - (a.c.ratio_pct ?? 0));

  return (
    <section className="rounded-lg border border-num/40 bg-num/5 px-4 py-3">
      <h3 className="flex items-baseline gap-2 text-[12px] font-semibold">
        수주
        <span className="font-mono font-normal text-muted-foreground">
          {rows.length}
        </span>
        <span className="ml-auto text-[11px] font-normal text-muted-foreground">
          최근 90일 · 금액과 비율은 <strong>공시에 적힌 값</strong>입니다
        </span>
      </h3>
      <div className="mt-2 divide-y divide-border/60">
        {rows.map(({ line, c }, i) => (
          <div
            key={`${line.symbol}-${i}`}
            className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1 py-1.5"
          >
            <button
              type="button"
              onClick={() => onAsk?.(line.company)}
              disabled={!onAsk}
              className="text-[13px] font-medium underline-offset-2 hover:underline disabled:cursor-default disabled:no-underline"
            >
              {line.company}
            </button>
            <span className="text-[12.5px]">{c.counterparty}</span>
            <span className="font-mono text-[12.5px] tabular-nums">
              {c.display_amount}
            </span>
            {c.ratio_pct != null && (
              /* **비율이 이 줄의 요점이다.** 금액만으로는 뜻이 없다 */
              <span className="font-mono text-[12.5px] font-medium text-num tabular-nums">
                최근 매출 대비 {c.ratio_pct.toLocaleString()}%
              </span>
            )}
            {c.business && (
              <span className="text-[11px] text-muted-foreground">
                · {c.business}
              </span>
            )}
            <span className="ml-auto flex items-baseline gap-2 text-[11px] text-muted-foreground">
              {c.filed_at}
              {c.ends_at && <span>납기 {c.ends_at}</span>}
              <a
                href={c.url}
                target="_blank"
                rel="noreferrer"
                className="underline-offset-2 hover:text-foreground hover:underline"
              >
                원문
              </a>
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}

function Section({
  title,
  lines,
  muted = false,
  onAsk,
}: {
  title: string;
  lines: BriefLine[];
  muted?: boolean;
  onAsk?: (company: string) => void;
}) {
  return (
    <div className="mb-3 last:mb-0">
      <p className="mb-1 text-[11px] font-medium text-muted-foreground">
        {title} {lines.length}
      </p>
      <div className="divide-y">
        {lines.map((l) => (
          <Line key={l.symbol} line={l} muted={muted} onAsk={onAsk} />
        ))}
      </div>
    </div>
  );
}

function Line({
  line: l,
  muted,
  onAsk,
}: {
  line: BriefLine;
  muted: boolean;
  onAsk?: (company: string) => void;
}) {
  const day = l.moves.find((m) => m.key === "1d")?.change_pct ?? null;
  const notable =
    l.filings.length > 0 || (day != null && Math.abs(day) >= NOTABLE);
  return (
    <div className={cn("py-2", muted && "opacity-80")}>
      <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
        {/* **눈에 띄어야 하는 것만 표시한다.** 전부 강조하면 아무것도 강조가 아니다 */}
        {/* **여기서 나갈 수 있어야 한다** (D86). 아침에 「-6%」를 보고 나면
            다음 동작은 「왜 빠졌나」다. 전에는 회사명이 그냥 글자라, 종목명을
            눈으로 읽고 다른 탭에 다시 쳐야 했다. */}
        <button
          type="button"
          onClick={() => onAsk?.(l.company)}
          disabled={!onAsk}
          className="text-[13px] font-medium underline-offset-2 hover:underline disabled:cursor-default disabled:no-underline"
          title={onAsk ? `${l.company} 물어보기` : undefined}
        >
          {notable && <span className="mr-1 text-warn">★</span>}
          {l.company}
        </button>
        <span className="font-mono text-[10.5px] text-muted-foreground">
          {l.symbol}
        </span>
        <span className="ml-auto flex gap-2.5 font-mono text-[12px] tabular-nums">
          {l.moves.map((m) => (
            <Pct key={m.key} move={m} excess={l.excess} />
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
