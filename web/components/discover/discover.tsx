"use client";

import { useState } from "react";

import { CompanySearch, symbolOf } from "@/components/workbench/company-search";
import { Button } from "@/components/ui/button";
import { addStock, discover, type DiscoverResult } from "@/lib/api";

/**
 * 발굴 — **「숨겨진 ○○ 수혜주」** (D87).
 *
 * 왜 이 탭이 있나
 * ---------------
 * 나머지 화면은 전부 **이미 아는 종목**에서 시작한다 — 커버리지에 넣은 것,
 * 브리프에 뜨는 것, 리포트를 쓴 것. 그런데 미드스몰캡 애널리스트가 돈 버는
 * 순간은 그 목록에 **없던 이름을 찾을 때**다. 공개 리포트 60여 편에서
 * 「숨겨진 ○○ 수혜주」가 제목에만 다섯 번 나온다.
 *
 * 무엇을 말하지 않나
 * ------------------
 * **「이 종목이 수혜주입니다」라고 하지 않는다.** 상관은 「같이 움직였다」이지
 * 「밸류체인에 있다」가 아니다 — 같은 수급에 실렸어도 올라간다. 그래서
 * 업종·시총·거래대금을 같이 내고, 묶음이 무작위 수준이면 그렇게 적는다.
 */
export function Discover({
  onAsk,
  onReport,
}: {
  onAsk?: (company: string) => void;
  onReport?: (symbol: string) => void;
}) {
  const [seeds, setSeeds] = useState<{ symbol: string; company: string }[]>([]);
  const [data, setData] = useState<DiscoverResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [added, setAdded] = useState<Record<string, string>>({});
  const [typing, setTyping] = useState("");

  // `CompanySearch`는 「회사명 (123456)」 꼴을 남긴다 — 커버리지와 같은 규칙이다
  function addSeed() {
    const symbol = symbolOf(typing);
    if (!symbol) return;
    const company = typing.replace(/\s*\(\d{6}\)\s*$/, "").trim();
    setSeeds((v) =>
      v.some((x) => x.symbol === symbol) ? v : [...v, { symbol, company }],
    );
    setTyping("");
  }

  async function run() {
    setBusy(true);
    setError("");
    try {
      setData(await discover({ seeds: seeds.map((s) => s.symbol) }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "찾지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  async function keep(symbol: string) {
    setAdded((a) => ({ ...a, [symbol]: "담는 중…" }));
    try {
      await addStock(symbol, "watch");
      setAdded((a) => ({ ...a, [symbol]: "관심에 담았습니다" }));
    } catch (e) {
      setAdded((a) => ({
        ...a,
        [symbol]: e instanceof Error ? e.message : "담지 못했습니다",
      }));
    }
  }

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-[15px] font-medium">
          앵커 종목에서 같이 움직이는 소형주를 찾습니다.
        </h2>
        <p className="mt-1.5 max-w-[620px] text-[12.5px] leading-[1.8] text-muted-foreground">
          테마의 대표 종목을 <strong>앵커</strong>로 주면, 시장 요인을 걷어낸
          뒤 같이 움직인 <strong>코스닥 소형주</strong>를 냅니다. 이미 보는
          종목은 빠집니다.{" "}
          <strong>앵커를 둘 이상 주면 훨씬 또렷해집니다.</strong>
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {seeds.map((s) => (
          <span
            key={s.symbol}
            className="flex items-center gap-1 rounded-full border py-0.5 pr-1 pl-2.5 text-[12px]"
          >
            {s.company || s.symbol}
            <button
              type="button"
              onClick={() =>
                setSeeds((v) => v.filter((x) => x.symbol !== s.symbol))
              }
              className="px-1 text-muted-foreground hover:text-bad"
            >
              ×
            </button>
          </span>
        ))}
        {seeds.length < 4 && (
          <div className="flex w-[320px] gap-2">
            <CompanySearch value={typing} onChange={setTyping} />
            <Button
              size="sm"
              variant="outline"
              disabled={!symbolOf(typing)}
              onClick={addSeed}
            >
              앵커 추가
            </Button>
          </div>
        )}
        <Button size="sm" disabled={seeds.length === 0 || busy} onClick={() => void run()}>
          {busy ? "찾는 중…" : "찾기"}
        </Button>
      </div>

      {error && (
        <p className="rounded-md border border-bad/50 px-3 py-2 text-[12.5px] text-bad">
          {error}
        </p>
      )}

      {data && (
        <>
          {/* **판정을 검산할 수 있게 낸다** — 「0.41」만으로는 높은지 모른다.
              무작위로 같은 크기 묶음을 지었을 때의 값을 옆에 놓는다. */}
          <div className="rounded-md border px-3.5 py-2.5 text-[12px] leading-[1.8]">
            <span className="text-muted-foreground">
              코스닥 소형주 <strong>{data.universe}종목</strong> 중에서 골랐습니다
              · 묶음 내부 상관{" "}
            </span>
            <span className="font-mono">{data.cohesion.toFixed(2)}</span>
            <span className="text-muted-foreground">
              {" "}
              (무작위 수준 <span className="font-mono">{data.baseline.toFixed(2)}</span>)
            </span>
            <p className="mt-1 text-muted-foreground">
              {data.note ? (
                <span className="text-warn">{data.note}</span>
              ) : (
                <>
                  <strong>같이 움직였다는 뜻이지 밸류체인에 있다는 뜻이 아닙니다.</strong>{" "}
                  업종과 규모를 보고 직접 고르십시오.
                </>
              )}
            </p>
          </div>

          {data.found.length === 0 ? (
            <p className="rounded-lg border border-dashed px-4 py-6 text-center text-[13px] text-muted-foreground">
              조건에 맞는 후보가 없습니다.
            </p>
          ) : (
            <div className="divide-y rounded-lg border">
              {data.found.map((f) => (
                <div key={f.symbol} className="px-3.5 py-2.5">
                  <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
                    {/* **아직 안 도는 것이 진짜 「숨겨진」 것이다.**
                        이미 도는 종목을 발굴이라고 부르면 뒷북이다. */}
                    {f.unheard ? (
                      <span
                        className="text-[10.5px] text-ok"
                        title="오늘 텔레그램 언급 없음"
                      >
                        아직 안 돎
                      </span>
                    ) : (
                      <span
                        className="text-[10.5px] text-warn"
                        title={`오늘 ${f.mentions}회 언급`}
                      >
                        이미 돎 {f.mentions}
                      </span>
                    )}
                    <span className="text-[13.5px] font-medium">{f.company}</span>
                    <span className="font-mono text-[11px] text-muted-foreground">
                      {f.symbol}
                    </span>
                    {f.industry && (
                      <span className="text-[11.5px] text-muted-foreground">
                        {f.industry}
                      </span>
                    )}
                    <span className="ml-auto flex items-baseline gap-3 font-mono text-[12px] tabular-nums">
                      <span title="시장 요인을 걷어낸 상관">
                        <span className="text-[10.5px] text-muted-foreground">
                          상관{" "}
                        </span>
                        {f.correlation.toFixed(2)}
                      </span>
                      <span>{f.cap_display}</span>
                      <span className="text-muted-foreground">
                        {((f.avg_turnover ?? 0) / 1e8).toFixed(0)}억
                      </span>
                    </span>
                  </div>
                  {/* 후보에서 곧바로 나갈 수 있어야 한다 (D86과 같은 규칙) */}
                  <div className="mt-1 flex flex-wrap items-center gap-x-3 text-[11px]">
                    <button
                      type="button"
                      onClick={() => void keep(f.symbol)}
                      disabled={!!added[f.symbol]}
                      className="text-muted-foreground underline-offset-2 hover:text-foreground hover:underline disabled:no-underline"
                    >
                      {added[f.symbol] || "관심에 담기"}
                    </button>
                    <button
                      type="button"
                      onClick={() => onAsk?.(f.company)}
                      className="text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
                    >
                      물어보기
                    </button>
                    <button
                      type="button"
                      onClick={() => onReport?.(f.symbol)}
                      className="text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
                    >
                      리포트 만들기
                    </button>
                    <a
                      href={`https://dart.fss.or.kr/dsab007/main.do?textCrpNm=${encodeURIComponent(f.company)}`}
                      target="_blank"
                      rel="noreferrer"
                      className="text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
                    >
                      공시 보기
                    </a>
                  </div>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
