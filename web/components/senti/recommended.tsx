"use client";

import { useEffect, useState } from "react";

import {
  adoptChannel,
  getRecommendedChannels,
  KIND_LABEL,
  type RecommendedChannel,
} from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * 추천 채널 — **고르게 하되 실측한 것만.**
 *
 * 볼 채널 목록이 「내가 이미 들어가 있는 방」뿐이면, 무엇을 구독해야 하는지
 * 모르는 사람에게 빈 목록은 계속 빈 목록이다. 섹터 시드와 같은 문제이고 같은
 * 답이다 — **정답이 아니라 출발점을 준다.**
 *
 * **구독하지 않는다.** 공개 채널은 들어가지 않고도 읽힌다. 남의 계정으로 방에
 * 들어가는 것은 되돌리기 어려운 일이고, 읽는 데 필요하지도 않다.
 *
 * **증권사가 규모보다 앞이다.** 소속이 드러나 있으면 틀렸을 때 책임 소재가
 * 있고 컴플라이언스를 거친 글이라, 46,000명짜리 익명 채널보다 205명짜리
 * 담당 애널리스트 채널이 RA에게 먼저다.
 */
export function Recommended({ onAdopted }: { onAdopted?: () => void }) {
  const [rows, setRows] = useState<RecommendedChannel[]>([]);
  const [checkedAt, setCheckedAt] = useState("");
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const got = await getRecommendedChannels();
        if (!alive) return;
        setRows(got.channels);
        setCheckedAt(got.checked_at);
        // 하나도 안 넣었으면 펼쳐 둔다 — 접힌 막대만 보이면 못 찾는다
        setOpen(got.channels.every((c) => !c.have));
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  async function adopt(c: RecommendedChannel) {
    setBusy(c.username);
    setError("");
    try {
      await adoptChannel(c.username);
      setRows((v) =>
        v.map((x) => (x.username === c.username ? { ...x, have: true } : x)),
      );
      onAdopted?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy("");
    }
  }

  if (rows.length === 0) return null;
  const left = rows.filter((c) => !c.have);

  return (
    <section className="rounded-lg border">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full flex-wrap items-baseline gap-x-2 px-3.5 py-2 text-left"
      >
        <span className="text-[12.5px] font-medium">추천 채널</span>
        <span className="font-mono text-[11px] text-muted-foreground">
          {left.length}
        </span>
        <span className="text-[11.5px] text-muted-foreground">
          내 섹터 담당 애널리스트가 먼저 — <strong>구독하지 않고</strong> 읽습니다
        </span>
        <span className="ml-auto text-[11px] text-muted-foreground">
          {open ? "접기" : "보기"}
        </span>
      </button>

      {open && (
        <div className="border-t">
          {rows.map((c) => (
            <Row
              key={c.username}
              row={c}
              busy={busy === c.username}
              disabled={!!busy}
              onAdopt={() => void adopt(c)}
            />
          ))}
          {error && (
            <p className="px-3.5 py-2 text-[11.5px] leading-[1.7] text-bad">
              {error}
            </p>
          )}
          <p className="px-3.5 py-2 text-[11.5px] leading-[1.7] text-muted-foreground">
            {checkedAt}에 <strong>전부 직접 확인</strong>했습니다 — 조사로 모은
            이름에는 지어낸 것이 섞이고 실제로 셋은 존재하지 않았습니다. 한 달
            넘게 글이 없는 채널과 <strong>사칭방</strong>은 여기 없습니다.
            구독자 수는 확인 시점 값이라 순서를 정하는 데만 씁니다.
          </p>
        </div>
      )}
    </section>
  );
}

function Row({
  row: c,
  busy,
  disabled,
  onAdopt,
}: {
  row: RecommendedChannel;
  busy: boolean;
  disabled: boolean;
  onAdopt: () => void;
}) {
  return (
    <div
      className={cn(
        "flex items-center gap-3 border-b px-3.5 py-1.5 last:border-b-0",
        c.have && "opacity-60",
      )}
    >
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[12.5px]">
          {c.title}
          {/* **내 섹터를 표시한다.** 순서만으로는 왜 위에 있는지 모른다 */}
          {c.mine && (
            <span className="ml-1.5 rounded bg-ok/15 px-1 py-0.5 text-[10px] text-ok">
              {c.sector}
            </span>
          )}
        </span>
        <span className="block font-mono text-[10px] text-muted-foreground">
          {KIND_LABEL[c.kind] ?? c.kind} · @{c.username}
          {c.sector && !c.mine && ` · ${c.sector}`}
        </span>
        {/* **단정하지 않은 것은 단정하지 않는다고 적는다** */}
        {c.note && (
          <span className="block text-[10.5px] leading-[1.6] text-warn">
            {c.note}
          </span>
        )}
      </span>
      <span className="w-[60px] text-right font-mono text-[11px] tabular-nums text-muted-foreground">
        {c.subscribers.toLocaleString()}
      </span>
      {c.have ? (
        <span className="w-[52px] text-right text-[11px] text-muted-foreground">
          있음
        </span>
      ) : (
        <button
          type="button"
          disabled={disabled}
          onClick={onAdopt}
          className="w-[52px] rounded border px-2 py-0.5 text-[11px] transition-colors hover:bg-accent disabled:opacity-50"
        >
          {busy ? "…" : "넣기"}
        </button>
      )}
    </div>
  );
}
