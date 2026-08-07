"use client";

import { useEffect, useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { CompanySearch, symbolOf } from "@/components/workbench/company-search";
import {
  getProfile,
  pinPeerGroup,
  saveProfile,
  type Covered,
  type ProfileData,
} from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * 내 커버리지 — **로그인이 뜻을 갖는 자리.**
 *
 * 지금까지 이 제품은 매번 처음부터 시작했다. 종목코드를 넣고, 리포트를
 * 만들고, 떠난다. RA는 그렇게 일하지 않는다 — 같은 20~30종목을 몇 년 본다.
 *
 * **쌓는 것은 셋뿐이다**: 사람이 정한 것(커버 종목·섹터·고정한 피어 그룹),
 * 출처 있는 사실(카드), 리퀘스트 이력. **LLM이 만든 요약은 안 쌓는다** —
 * 필요하면 그때 다시 만들면 되고($0.002), 쌓아 두면 틀린 것이 굳는다.
 */
export function Coverage() {
  const [data, setData] = useState<ProfileData | null>(null);
  const [stocks, setStocks] = useState<Covered[]>([]);
  const [sectorText, setSectorText] = useState("");
  const [adding, setAdding] = useState("");
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    void (async () => {
      try {
        const p = await getProfile();
        setData(p);
        setStocks(p.stocks);
        setSectorText(p.sectors.join(", "));
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    })();
  }, []);

  function patch(symbol: string, over: Partial<Covered>) {
    setStocks((v) => v.map((s) => (s.symbol === symbol ? { ...s, ...over } : s)));
    setDirty(true);
  }

  function add() {
    const code = symbolOf(adding);
    if (!/^\d{6}$/.test(code) || stocks.some((s) => s.symbol === code)) return;
    setStocks((v) => [
      ...v,
      {
        symbol: code,
        company: adding.replace(/\s*\(\d{6}\)\s*$/, ""),
        sector: "",
        publishes: false,
        note: "",
        added_at: "",
      },
    ]);
    setAdding("");
    setDirty(true);
  }

  async function save() {
    setBusy(true);
    setError("");
    try {
      const next = await saveProfile({
        sectors: sectorText
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean),
        stocks: stocks.map((s) => ({
          symbol: s.symbol,
          sector: s.sector,
          publishes: s.publishes,
          note: s.note,
        })),
      });
      setData((d) => (d ? { ...d, ...next } : d));
      setStocks(next.stocks);
      setDirty(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function togglePin(cardId: string, pinned: boolean) {
    const { pinned_peers } = await pinPeerGroup(cardId, pinned);
    setData((d) =>
      d
        ? {
            ...d,
            pinned_peers,
            peer_groups: d.peer_groups.map((g) =>
              g.card_id === cardId ? { ...g, pinned } : g,
            ),
          }
        : d,
    );
  }

  if (error && !data) {
    return (
      <Alert variant="destructive" className="max-w-[720px]">
        <AlertDescription>{error}</AlertDescription>
      </Alert>
    );
  }
  if (!data) {
    return <p className="text-[13px] text-muted-foreground">읽는 중…</p>;
  }

  const withCards = new Set(data.with_cards);

  return (
    <div className="max-w-[820px] space-y-8">
      <p className="text-[13px] leading-[1.8] text-muted-foreground">
        여기 적어 두면 <strong>다음에 올 때도 그대로 있습니다.</strong> 물어보기·
        피어 뷰·브리프가 이 목록을 기본 맥락으로 씁니다.
      </p>

      <section>
        <h3 className="mb-2 text-[12px] font-semibold">맡은 섹터</h3>
        <Input
          value={sectorText}
          onChange={(e) => {
            setSectorText(e.target.value);
            setDirty(true);
          }}
          placeholder="방산·우주, 조선, 2차전지"
        />
        <p className="mt-1.5 text-[11.5px] text-muted-foreground">
          쉼표로 나눕니다. <strong>표준 분류를 쓰지 않습니다</strong> — 방산
          4종목이 KSIC 어느 자릿수에서도 한 그룹이 안 됩니다.
        </p>
      </section>

      <section>
        <div className="mb-2 flex items-baseline justify-between">
          <h3 className="text-[12px] font-semibold">
            커버 종목{" "}
            <span className="font-mono font-normal text-muted-foreground">
              {stocks.length}
            </span>
          </h3>
          <span className="text-[11.5px] text-muted-foreground">
            「발간」은 리포트를 내는 종목입니다
          </span>
        </div>

        <div className="rounded-lg border">
          {stocks.map((s) => (
            <div
              key={s.symbol}
              className="flex flex-wrap items-center gap-2 border-b px-3 py-2 last:border-b-0"
            >
              <span className="min-w-[140px] flex-1 text-[13px]">
                {s.company || s.symbol}
                <span className="ml-1.5 font-mono text-[11px] text-muted-foreground">
                  {s.symbol}
                </span>
                {/* 카드가 이미 있는지 — 「뭘 더 해야 하나」가 바로 보여야 한다 */}
                {withCards.has(s.symbol) && (
                  <span className="ml-1.5 text-[11px] text-ok">· 카드 있음</span>
                )}
              </span>
              <Input
                value={s.sector}
                onChange={(e) => patch(s.symbol, { sector: e.target.value })}
                placeholder="섹터"
                className="h-7 w-[110px] text-[12px]"
              />
              <label className="flex items-center gap-1.5 text-[12px]">
                <Checkbox
                  checked={s.publishes}
                  onCheckedChange={(v) => patch(s.symbol, { publishes: !!v })}
                />
                발간
              </label>
              <Input
                value={s.note}
                onChange={(e) => patch(s.symbol, { note: e.target.value })}
                placeholder="왜 보는지 (선택)"
                className="h-7 w-[180px] text-[12px]"
              />
              <button
                type="button"
                onClick={() => {
                  setStocks((v) => v.filter((x) => x.symbol !== s.symbol));
                  setDirty(true);
                }}
                className="text-[13px] text-muted-foreground hover:text-bad"
                aria-label={`${s.company || s.symbol} 빼기`}
              >
                ×
              </button>
            </div>
          ))}
          {stocks.length === 0 && (
            <p className="px-3 py-4 text-[13px] text-muted-foreground">
              커버하는 종목을 넣으십시오.
            </p>
          )}
        </div>

        <div className="mt-2 flex gap-2">
          <CompanySearch value={adding} onChange={setAdding} />
          <Button variant="outline" onClick={add} disabled={!adding.trim()}>
            넣기
          </Button>
        </div>
      </section>

      <section>
        <h3 className="mb-2 text-[12px] font-semibold">피어 그룹</h3>
        {data.peer_groups.length > 0 ? (
          <div className="rounded-lg border">
            {data.peer_groups.map((g) => (
              <label
                key={g.card_id}
                className={cn(
                  "flex cursor-pointer items-center gap-3 border-b px-3 py-2 last:border-b-0",
                  g.pinned && "bg-accent/40",
                )}
              >
                <Checkbox
                  checked={g.pinned}
                  onCheckedChange={(v) => void togglePin(g.card_id, !!v)}
                />
                <span className="flex-1 text-[13px]">{g.name}</span>
                <span className="font-mono text-[11px] text-muted-foreground">
                  {g.member_count}종목
                </span>
              </label>
            ))}
          </div>
        ) : (
          <p className="text-[13px] text-muted-foreground">
            아직 만든 피어 그룹이 없습니다.
          </p>
        )}
        <p className="mt-1.5 text-[11.5px] leading-[1.7] text-muted-foreground">
          체크한 그룹은 <strong>고정됩니다.</strong> 상관은 기간이 바뀌면
          흔들리는데(같은 씨앗의 top15가 4/15까지), 표가 조용히 바뀌면 「직전
          대비 변화」가 무의미해집니다.
        </p>
      </section>

      {error && <p className="text-[12px] text-bad">{error}</p>}

      <div className="sticky bottom-0 flex items-center justify-between border-t bg-background py-3">
        <span className="text-[11.5px] text-muted-foreground">
          {data.updated_at
            ? `마지막 저장 ${data.updated_at.slice(0, 16).replace("T", " ")}`
            : "아직 저장한 적이 없습니다"}
        </span>
        <Button onClick={() => void save()} disabled={busy || !dirty}>
          {busy ? "저장 중…" : dirty ? "저장" : "저장됨"}
        </Button>
      </div>
    </div>
  );
}
