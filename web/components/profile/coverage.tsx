"use client";

import { useEffect, useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { SectorPicker } from "@/components/profile/sector-picker";
import { CompanySearch, symbolOf } from "@/components/workbench/company-search";
import {
  getProfile,
  pinPeerGroup,
  saveProfile,
  type CoverKind,
  type Covered,
  type PeerGroupRef,
  type ProfileData,
} from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * 내 커버리지 — **이 사람의 업무 지도.**
 *
 * 나머지 네 탭이 전부 여기서 파생된다: 브리프는 커버 종목을 보고, 센티는
 * 커버·관심·피어로 순서를 매기고, 피어그룹은 여기서 만들어지고, 리포트는
 * 커버 종목에 대해 쓴다.
 *
 * **섹터가 컨테이너다.** 처음엔 섹터를 쉼표 한 줄로 두고 커버·관심·피어를
 * 각각 평평한 목록으로 뒀는데, 그러면 **관계가 화면에 안 보인다.** RA의
 * 업무는 그렇게 생기지 않았다 — 섹터로 배정받고, 그 안에 커버 종목이 있고,
 * 그 옆에 안 보는 종목이 있다.
 *
 * **피어 그룹도 섹터 안에 산다.** D68에서 「섹터는 자유 텍스트라 코드가 못
 * 읽고 실질적 정의는 피어 그룹」이라고 정했는데, 뒤집으면 **피어 그룹이 곧
 * 그 섹터의 조작적 정의**다. 그래서 섹터 헤더의 「피어 그룹 만들기」는 그
 * 섹터의 커버 종목을 씨앗으로 들고 간다 — 지금까지 가장 큰 마찰이었다.
 *
 * **주가는 없다.** 여기는 「무엇을 보는가」를 정하는 곳이고, 「무엇이
 * 달라졌나」는 브리프가 답한다.
 */

export function Coverage({
  onComposePeer,
  onOpenCard,
}: {
  /** 씨앗을 들고 피어 그룹 만들기로 간다 */
  onComposePeer?: (seeds: string[], name: string) => void;
  onOpenCard?: (cardId: string) => void;
}) {
  const [data, setData] = useState<ProfileData | null>(null);
  const [stocks, setStocks] = useState<Covered[]>([]);
  const [sectors, setSectors] = useState<string[]>([]);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [newSector, setNewSector] = useState("");

  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const p = await getProfile();
        if (!alive) return;
        setData(p);
        setStocks(p.stocks);
        setSectors(p.sectors);
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  function patch(symbol: string, over: Partial<Covered>) {
    setStocks((v) => v.map((s) => (s.symbol === symbol ? { ...s, ...over } : s)));
    setDirty(true);
  }

  function remove(symbol: string) {
    setStocks((v) => v.filter((s) => s.symbol !== symbol));
    setDirty(true);
  }

  async function save() {
    setBusy(true);
    setError("");
    try {
      const next = await saveProfile({
        sectors,
        stocks: stocks.map((s) => ({
          symbol: s.symbol,
          sector: s.sector,
          kind: s.kind,
          note: s.note,
        })),
      });
      setData((d) => (d ? { ...d, ...next } : d));
      setStocks(next.stocks);
      setSectors(next.sectors);
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

  const loose = stocks.filter((s) => !s.sector || !sectors.includes(s.sector));
  const withCards = new Set(data.with_cards);

  return (
    <div className="max-w-[960px] space-y-8">
      <p className="text-[13px] leading-[1.8] text-muted-foreground">
        <strong>맡은 섹터를 먼저 정하고</strong>, 그 안에 커버 종목과 관심
        종목을 넣습니다. 여기서 정한 것이 브리프·센티·피어그룹·리포트의 기본
        맥락이 됩니다.
      </p>

      {/* **섹터가 먼저다.** 관심사를 고르고 나서 그 안을 채우는 흐름이다. */}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          const name = newSector.trim();
          if (!name || sectors.includes(name)) return;
          setSectors((v) => [...v, name]);
          setNewSector("");
          setDirty(true);
        }}
        className="flex gap-2"
      >
        <Input
          value={newSector}
          onChange={(e) => setNewSector(e.target.value)}
          placeholder="맡은 섹터 — 방산·우주, 조선, 2차전지…"
          className="max-w-[420px]"
        />
        <Button type="submit" variant="outline" disabled={!newSector.trim()}>
          섹터 추가
        </Button>
      </form>

      {/* **고르는 편이 치는 편보다 낫다.** 다만 시드는 출발점이라 아래에서
          얼마든지 고칠 수 있고, 저장은 하나뿐인 저장 버튼이 한다. */}
      <SectorPicker
        taken={sectors}
        onAdopt={(seed) => {
          setSectors((v) => (v.includes(seed.name) ? v : [...v, seed.name]));
          setStocks((v) => {
            const have = new Set(v.map((s) => s.symbol));
            const add = seed.symbols
              // **이미 있는 것은 안 건드린다** — 커버/관심 표시가 사람 것이다
              .map((symbol, i) => ({ symbol, company: seed.companies[i] ?? "" }))
              .filter((s) => !have.has(s.symbol))
              .map((s) => ({
                ...s,
                sector: seed.name,
                // **커버가 아니라 관심으로 들어온다.** 커버는 「내가 리포트를
                // 낸다」는 선언이라 사람이 골라야 한다
                kind: "watch" as CoverKind,
                note: "",
                added_at: "",
              }));
            return [...v, ...add];
          });
          setDirty(true);
        }}
      />

      {sectors.length === 0 && loose.length === 0 && (
        <div className="rounded-lg border border-dashed px-4 py-8 text-center">
          <p className="text-[14px] font-medium">섹터부터 넣으십시오.</p>
          <p className="mx-auto mt-1.5 max-w-[460px] text-[12.5px] leading-[1.8] text-muted-foreground">
            <strong>표준 분류를 쓰지 않습니다</strong> — 방산 4종목이 KSIC 어느
            자릿수에서도 한 그룹이 안 됩니다. 실무에서 부르는 이름 그대로
            적으십시오.
          </p>
        </div>
      )}

      <div className="space-y-5">
        {sectors.map((sector) => (
          <SectorCard
            key={sector}
            sector={sector}
            stocks={stocks.filter((s) => s.sector === sector)}
            groups={data.peer_groups.filter((g) => g.sector === sector)}
            withCards={withCards}
            onAdd={(symbol, company, kind) => {
              setStocks((v) => [
                ...v.filter((s) => s.symbol !== symbol),
                { symbol, company, sector, kind, note: "", added_at: "" },
              ]);
              setDirty(true);
            }}
            onPatch={patch}
            onRemove={remove}
            onRemoveSector={() => {
              // **종목은 안 지운다.** 섹터만 빼고 종목은 「섹터 없음」으로
              // 내려온다 — 섹터를 고치다 커버 종목을 잃으면 안 된다.
              setSectors((v) => v.filter((x) => x !== sector));
              setStocks((v) =>
                v.map((s) => (s.sector === sector ? { ...s, sector: "" } : s)),
              );
              setDirty(true);
            }}
            onComposePeer={onComposePeer}
            onOpenCard={onOpenCard}
            onTogglePin={togglePin}
          />
        ))}

        {loose.length > 0 && (
          <SectorCard
            sector=""
            title="섹터 없음"
            stocks={loose}
            groups={data.peer_groups.filter((g) => !g.sector)}
            withCards={withCards}
            sectorOptions={sectors}
            onPatch={patch}
            onRemove={remove}
            onOpenCard={onOpenCard}
            onTogglePin={togglePin}
          />
        )}
      </div>

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

function SectorCard({
  sector,
  title,
  stocks,
  groups,
  withCards,
  sectorOptions,
  onAdd,
  onPatch,
  onRemove,
  onRemoveSector,
  onComposePeer,
  onOpenCard,
  onTogglePin,
}: {
  sector: string;
  title?: string;
  stocks: Covered[];
  groups: PeerGroupRef[];
  withCards: Set<string>;
  sectorOptions?: string[];
  onAdd?: (symbol: string, company: string, kind: CoverKind) => void;
  onPatch: (symbol: string, over: Partial<Covered>) => void;
  onRemove: (symbol: string) => void;
  onRemoveSector?: () => void;
  onComposePeer?: (seeds: string[], name: string) => void;
  onOpenCard?: (cardId: string) => void;
  onTogglePin: (cardId: string, pinned: boolean) => void;
}) {
  const [adding, setAdding] = useState("");
  const [note, setNote] = useState("");
  const cover = stocks.filter((s) => s.kind !== "watch");
  const watch = stocks.filter((s) => s.kind === "watch");

  function add(kind: CoverKind) {
    const code = symbolOf(adding);
    // **말없이 실패하지 않는다.** 이름만 치고 누르면 아무 일도 안 일어났다.
    if (!/^\d{6}$/.test(code)) {
      setNote("목록에서 골라 주십시오 — 이름만으로는 넣을 수 없습니다.");
      return;
    }
    onAdd?.(code, adding.replace(/\s*\(\d{6}\)\s*$/, ""), kind);
    setAdding("");
    setNote("");
  }

  return (
    <section className="rounded-lg border">
      <header className="flex flex-wrap items-center gap-2 border-b bg-muted/30 px-3.5 py-2">
        <h3 className="text-[13.5px] font-medium">{title ?? sector}</h3>
        <span className="font-mono text-[11px] text-muted-foreground">
          커버 {cover.length} · 관심 {watch.length}
        </span>
        <span className="ml-auto flex items-center gap-1.5">
          {/* **씨앗을 들고 간다.** 지금까지 피어 그룹을 만들려면 커버 종목을
              처음부터 다시 쳐야 했다 — 가장 큰 마찰이었다. */}
          {onComposePeer && cover.length > 0 && (
            <Button
              size="sm"
              variant="outline"
              onClick={() =>
                onComposePeer(
                  cover.slice(0, 2).map((s) => s.symbol),
                  `${sector} 피어`,
                )
              }
              className="h-7 text-[12px]"
            >
              피어 그룹 만들기
            </Button>
          )}
          {onRemoveSector && (
            <button
              type="button"
              onClick={onRemoveSector}
              className="px-1 text-[13px] text-muted-foreground hover:text-bad"
              title="섹터만 뺍니다 — 종목은 「섹터 없음」으로 내려갑니다"
            >
              ×
            </button>
          )}
        </span>
      </header>

      <div className="divide-y">
        <StockLine
          label="커버"
          hint="리포트를 냅니다"
          stocks={cover}
          withCards={withCards}
          sectorOptions={sectorOptions}
          onPatch={onPatch}
          onRemove={onRemove}
        />
        <StockLine
          label="관심"
          hint="옆에서 봅니다"
          stocks={watch}
          withCards={withCards}
          sectorOptions={sectorOptions}
          onPatch={onPatch}
          onRemove={onRemove}
        />

        {/* **피어 그룹은 섹터의 조작적 정의다** (D68). 그래서 여기 산다. */}
        {groups.length > 0 && (
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 px-3.5 py-2">
            <span className="w-[36px] shrink-0 text-[11px] text-muted-foreground">
              피어
            </span>
            {groups.map((g) => (
              <span key={g.card_id} className="flex items-center gap-1">
                <button
                  type="button"
                  onClick={() => onOpenCard?.(g.card_id)}
                  className="rounded border px-2 py-0.5 text-[12px] hover:bg-accent"
                >
                  ▤ {g.name}
                  <span className="ml-1 font-mono text-[10.5px] text-muted-foreground">
                    {g.member_count}
                  </span>
                </button>
                <button
                  type="button"
                  onClick={() => void onTogglePin(g.card_id, !g.pinned)}
                  className={cn(
                    "text-[12px]",
                    g.pinned
                      ? "text-ok"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                  title={
                    g.pinned
                      ? "고정 해제 — 상관은 기간이 바뀌면 흔들립니다"
                      : "고정하면 표가 조용히 바뀌지 않습니다"
                  }
                >
                  {g.pinned ? "📌" : "☆"}
                </button>
              </span>
            ))}
          </div>
        )}
      </div>

      {onAdd && (
        <div className="border-t px-3.5 py-2">
          <div className="flex gap-2">
            <CompanySearch
              value={adding}
              onChange={(v) => {
                setAdding(v);
                setNote("");
              }}
            />
            <Button
              size="sm"
              variant="outline"
              onClick={() => add("cover")}
              disabled={!adding.trim()}
            >
              커버로
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => add("watch")}
              disabled={!adding.trim()}
            >
              관심으로
            </Button>
          </div>
          {note && <p className="mt-1.5 text-[12px] text-warn">{note}</p>}
        </div>
      )}
    </section>
  );
}

function StockLine({
  label,
  hint,
  stocks,
  withCards,
  sectorOptions,
  onPatch,
  onRemove,
}: {
  label: string;
  hint: string;
  stocks: Covered[];
  withCards: Set<string>;
  sectorOptions?: string[];
  onPatch: (symbol: string, over: Partial<Covered>) => void;
  onRemove: (symbol: string) => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5 px-3.5 py-2">
      <span
        className="w-[36px] shrink-0 text-[11px] text-muted-foreground"
        title={hint}
      >
        {label}
      </span>
      {stocks.length === 0 && (
        <span className="text-[12px] text-muted-foreground">없음</span>
      )}
      {stocks.map((s) => (
        <span
          key={s.symbol}
          className="flex items-center gap-1 rounded-full border py-0.5 pr-1 pl-2.5 text-[12px]"
        >
          {s.company || s.symbol}
          {withCards.has(s.symbol) && (
            <span className="text-[10px] text-ok" title="리포트 카드가 있습니다">
              ●
            </span>
          )}
          {/* 「섹터 없음」에서만 섹터를 지정한다 — 안에 있으면 이미 정해져 있다 */}
          {sectorOptions && sectorOptions.length > 0 && (
            <select
              value=""
              onChange={(e) =>
                e.target.value && onPatch(s.symbol, { sector: e.target.value })
              }
              className="h-5 rounded border-0 bg-transparent text-[10.5px] text-muted-foreground"
              title="섹터 지정"
            >
              <option value="">섹터…</option>
              {sectorOptions.map((x) => (
                <option key={x} value={x}>
                  {x}
                </option>
              ))}
            </select>
          )}
          <button
            type="button"
            onClick={() =>
              onPatch(s.symbol, { kind: s.kind === "watch" ? "cover" : "watch" })
            }
            className="px-1 text-[10.5px] text-muted-foreground hover:text-foreground"
            title={s.kind === "watch" ? "커버로 옮깁니다" : "관심으로 옮깁니다"}
          >
            {s.kind === "watch" ? "→커버" : "→관심"}
          </button>
          <button
            type="button"
            onClick={() => onRemove(s.symbol)}
            className="px-0.5 text-muted-foreground hover:text-bad"
            aria-label={`${s.company || s.symbol} 빼기`}
          >
            ×
          </button>
        </span>
      ))}
    </div>
  );
}
