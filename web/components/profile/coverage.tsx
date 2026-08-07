"use client";

import { useEffect, useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { CompanySearch, symbolOf } from "@/components/workbench/company-search";
import {
  fmtPct,
  getProfile,
  pinPeerGroup,
  saveProfile,
  type CoverKind,
  type Covered,
  type Move,
  type Moves,
  type ProfileData,
} from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * 내 커버리지 — **로그인이 뜻을 갖는 자리.**
 *
 * RA는 같은 20~30종목을 몇 년 본다. 어느 섹터를 맡고, 어느 종목에 리포트를
 * 내고, 어느 피어를 옆에 두는지가 그 사람의 자산이다.
 *
 * **커버와 관심을 가른다.** 처음엔 종목마다 「발간」 체크박스를 뒀는데
 * 틀렸다 — 커버 종목이면 리포트를 내는 것이 자명해서 물을 이유가 없다.
 * 실제로 갈리는 축은 **내가 책임지느냐**다.
 *
 * 그리고 **기간 등락을 함께 낸다.** 분기에 한 번 찍은 사진만 보여주면 RA의
 * 하루에 못 들어간다 — 아침에 「어제 뭐가 빠졌나」를 보고 클라이언트가
 * 「이거 왜 올랐어요」를 묻는다.
 */

export function Coverage() {
  const [data, setData] = useState<ProfileData | null>(null);
  const [stocks, setStocks] = useState<Covered[]>([]);
  const [sectorText, setSectorText] = useState("");
  const [adding, setAdding] = useState("");
  const [addNote, setAddNote] = useState("");
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const p = await getProfile();
        if (!alive) return;
        setData(p);
        setStocks(p.stocks);
        setSectorText(p.sectors.join(", "));
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

  function add(kind: CoverKind) {
    const code = symbolOf(adding);
    // **말없이 실패하지 않는다.** 목록에서 안 고르고 「넣기」를 누르면 이름이
    // 그대로 남는데, 그때 아무 반응이 없으면 사용자는 넣은 줄 안다.
    if (!/^\d{6}$/.test(code)) {
      setAddNote("목록에서 종목을 골라 주십시오 — 이름만으로는 넣을 수 없습니다.");
      return;
    }
    if (stocks.some((s) => s.symbol === code)) {
      setAddNote("이미 들어 있는 종목입니다.");
      return;
    }
    setStocks((v) => [
      ...v,
      {
        symbol: code,
        company: adding.replace(/\s*\(\d{6}\)\s*$/, ""),
        sector: "",
        kind,
        note: "",
        added_at: "",
      },
    ]);
    setAdding("");
    setAddNote("");
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
          kind: s.kind,
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
  const moveOf = new Map(data.moves.map((m) => [m.symbol, m]));
  const sectors = sectorText
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  const cover = stocks.filter((s) => s.kind !== "watch");
  const watch = stocks.filter((s) => s.kind === "watch");
  const asof = data.moves.find((m) => m.last_date)?.last_date ?? "";

  return (
    <div className="max-w-[1080px] space-y-8">
      <p className="text-[13px] leading-[1.8] text-muted-foreground">
        여기 적어 두면 <strong>다음에 올 때도 그대로 있습니다.</strong>{" "}
        물어보기·피어 뷰·브리프가 이 목록을 기본 맥락으로 씁니다.
      </p>

      <section>
        <h3 className="mb-2 text-[12px] font-semibold">맡은 섹터</h3>
        <Input
          value={sectorText}
          onChange={(e) => {
            setSectorText(e.target.value);
            setDirty(true);
          }}
          placeholder="방산·우주, 조선, 헬스케어"
        />
        <p className="mt-1.5 text-[11.5px] text-muted-foreground">
          쉼표로 나눕니다. <strong>표준 분류를 쓰지 않습니다</strong> — 방산
          4종목이 KSIC 어느 자릿수에서도 한 그룹이 안 됩니다.
        </p>
      </section>

      <StockTable
        title="커버 종목"
        hint="내가 리포트를 내는 종목입니다. 실적 시즌에 반드시 봅니다."
        rows={cover}
        sectors={sectors}
        withCards={withCards}
        moveOf={moveOf}
        asof={asof}
        onPatch={patch}
        onRemove={(sym) => {
          setStocks((v) => v.filter((x) => x.symbol !== sym));
          setDirty(true);
        }}
      />

      <StockTable
        title="관심 종목"
        hint="피어·경쟁사·모니터링. 리포트는 안 냅니다."
        rows={watch}
        sectors={sectors}
        withCards={withCards}
        moveOf={moveOf}
        asof=""
        onPatch={patch}
        onRemove={(sym) => {
          setStocks((v) => v.filter((x) => x.symbol !== sym));
          setDirty(true);
        }}
      />

      <section>
        <div className="flex gap-2">
          <CompanySearch
            value={adding}
            onChange={(v) => {
              setAdding(v);
              setAddNote("");
            }}
          />
          <Button variant="outline" onClick={() => add("cover")} disabled={!adding.trim()}>
            커버로
          </Button>
          <Button variant="ghost" onClick={() => add("watch")} disabled={!adding.trim()}>
            관심으로
          </Button>
        </div>
        {addNote && <p className="mt-1.5 text-[12px] text-warn">{addNote}</p>}
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

function StockTable({
  title,
  hint,
  rows,
  sectors,
  withCards,
  moveOf,
  asof,
  onPatch,
  onRemove,
}: {
  title: string;
  hint: string;
  rows: Covered[];
  sectors: string[];
  withCards: Set<string>;
  moveOf: Map<string, Moves>;
  asof: string;
  onPatch: (symbol: string, over: Partial<Covered>) => void;
  onRemove: (symbol: string) => void;
}) {
  const cols = moveOf.values().next().value?.items ?? [];
  return (
    <section>
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-[12px] font-semibold">
          {title}{" "}
          <span className="font-mono font-normal text-muted-foreground">
            {rows.length}
          </span>
        </h3>
        <span className="text-[11.5px] text-muted-foreground">
          {hint}
          {asof && ` · 종가 ${asof.slice(4, 6)}/${asof.slice(6, 8)} 기준`}
        </span>
      </div>

      {rows.length === 0 ? (
        <p className="rounded-lg border border-dashed px-3 py-4 text-[13px] text-muted-foreground">
          아직 없습니다.
        </p>
      ) : (
        <div className="overflow-x-auto rounded-lg border">
          <table className="w-full min-w-[720px] border-collapse text-[13px]">
            <thead>
              <tr className="border-b bg-muted/40 text-[11px] text-muted-foreground">
                <th className="px-3 py-1.5 text-left font-medium">종목</th>
                <th className="px-2 py-1.5 text-left font-medium">섹터</th>
                {cols.map((c: Move) => (
                  <th key={c.key} className="px-2 py-1.5 text-right font-medium">
                    {c.label}
                  </th>
                ))}
                <th className="px-2 py-1.5 text-left font-medium">메모</th>
                <th className="w-6" />
              </tr>
            </thead>
            <tbody>
              {rows.map((s) => (
                <Row
                  key={s.symbol}
                  stock={s}
                  sectors={sectors}
                  hasCard={withCards.has(s.symbol)}
                  moves={moveOf.get(s.symbol)}
                  onPatch={onPatch}
                  onRemove={onRemove}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function Row({
  stock: s,
  sectors,
  hasCard,
  moves,
  onPatch,
  onRemove,
}: {
  stock: Covered;
  sectors: string[];
  hasCard: boolean;
  moves: Moves | undefined;
  onPatch: (symbol: string, over: Partial<Covered>) => void;
  onRemove: (symbol: string) => void;
}) {
  return (
    <tr className="border-b last:border-b-0">
      <td className="px-3 py-1.5 whitespace-nowrap">
        {s.company || s.symbol}
        <span className="ml-1.5 font-mono text-[11px] text-muted-foreground">
          {s.symbol}
        </span>
        {hasCard && <span className="ml-1.5 text-[11px] text-ok">· 카드</span>}
      </td>
      <td className="px-2 py-1">
        {/* **맡은 섹터에서 고른다.** 매번 손으로 치게 하면 「방산」과 「방산·우주」가
            섞이고, 그러면 섹터로 묶는 것 자체가 안 된다. */}
        <select
          value={s.sector}
          onChange={(e) => onPatch(s.symbol, { sector: e.target.value })}
          className="h-7 w-[110px] rounded-md border bg-transparent px-1.5 text-[12px]"
        >
          <option value="">—</option>
          {sectors.map((x) => (
            <option key={x} value={x}>
              {x}
            </option>
          ))}
          {s.sector && !sectors.includes(s.sector) && (
            <option value={s.sector}>{s.sector}</option>
          )}
        </select>
      </td>
      {(moves?.items ?? []).map((m) => (
        <td
          key={m.key}
          className={cn(
            "px-2 py-1.5 text-right font-mono tabular-nums whitespace-nowrap",
            m.change_pct == null
              ? "text-muted-foreground"
              : m.change_pct > 0
                ? "text-bad"
                : m.change_pct < 0
                  ? "text-num"
                  : "text-muted-foreground",
          )}
          /* 실제로 쓴 날짜를 남긴다 — 「1개월 +8.2%」만 있으면 언제부터인지 모른다 */
          title={
            m.change_pct == null
              ? "비교할 자료가 없습니다"
              : `${m.from_date} → ${m.to_date} (${m.days}거래일)${m.partial ? " · 자료가 짧습니다" : ""}`
          }
        >
          {fmtPct(m.change_pct)}
          {m.partial && m.change_pct != null && (
            <span className="text-muted-foreground">*</span>
          )}
        </td>
      ))}
      <td className="px-2 py-1">
        <Input
          value={s.note}
          onChange={(e) => onPatch(s.symbol, { note: e.target.value })}
          placeholder="왜 보는지"
          className="h-7 w-[150px] text-[12px]"
        />
      </td>
      <td className="px-1 whitespace-nowrap">
        {/* **커버와 관심은 오간다.** 처음엔 관심으로 넣었다가 커버가 되는
            것이 이 일의 정상이라, 지웠다 다시 넣게 하면 메모가 사라진다. */}
        <button
          type="button"
          onClick={() =>
            onPatch(s.symbol, { kind: s.kind === "watch" ? "cover" : "watch" })
          }
          className="mr-1 rounded border px-1.5 py-0.5 text-[11px] text-muted-foreground hover:text-foreground"
          title={
            s.kind === "watch" ? "커버 종목으로 옮깁니다" : "관심 종목으로 옮깁니다"
          }
        >
          {s.kind === "watch" ? "→커버" : "→관심"}
        </button>
        <button
          type="button"
          onClick={() => onRemove(s.symbol)}
          className="text-muted-foreground hover:text-bad"
          aria-label={`${s.company || s.symbol} 빼기`}
        >
          ×
        </button>
      </td>
    </tr>
  );
}
