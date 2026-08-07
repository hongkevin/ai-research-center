"use client";

import { useEffect, useState } from "react";

import { Checkbox } from "@/components/ui/checkbox";
import {
  getTgChannels,
  KIND_LABEL,
  setTgChannels,
  type TgChannelList,
  type TgChannelRow,
} from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * 볼 채널 고르기 — **센티 탭이 가진다.**
 *
 * 채널은 센티의 **재료**라 소비하는 자리에서 관리하는 것이 맞다. 커버리지
 * 탭에 뒀더니 「무엇을 보는가(종목)」와 「어디서 보는가(채널)」가 한 화면에
 * 섞였다.
 *
 * **내 커버리지와 관련도 순이다.** 받아 둔 메시지에서 내 종목·섹터가 몇 번
 * 나왔는지 세고, 못 세면 종류(증권사·리서치 먼저)와 구독자로 떨어진다 —
 * **셀 수 있을 때만 세고, 없으면 없다고 한다.**
 *
 * **죽은 채널은 💀.** 구독자 수만 보면 시체를 잡는다 — 20,437명짜리가
 * 219일째 정지인 경우가 실제로 있다.
 */
export function ChannelPicker({ onChanged }: { onChanged?: () => void }) {
  const [data, setData] = useState<TgChannelList | null>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const got = await getTgChannels();
        if (alive) setData(got);
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  async function toggle(chatId: number, enabled: boolean) {
    if (!data) return;
    const next = data.channels.map((c) =>
      c.chat_id === chatId ? { ...c, enabled } : c,
    );
    setData({ ...data, channels: next });
    setBusy(true);
    try {
      await setTgChannels(next.map((c) => ({ chat_id: c.chat_id, enabled: c.enabled })));
      onChanged?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  if (error) return <p className="text-[12px] text-bad">{error}</p>;
  if (!data) return null;

  const on = data.channels.filter((c) => c.enabled).length;

  if (data.channels.length === 0) {
    return (
      <div className="rounded-lg border border-dashed px-4 py-4">
        <p className="text-[13px] font-medium">볼 채널을 아직 못 받았습니다.</p>
        <p className="mt-1 text-[12px] leading-[1.8] text-muted-foreground">
          터미널에서 <code className="font-mono">arc telegram login</code> 한 뒤{" "}
          <code className="font-mono">arc telegram channels</code> 를 돌리면
          구독 중인 채널이 여기 나옵니다. 로그인은 인증 코드를 받아 쳐야 해서{" "}
          <strong>터미널에서만</strong> 됩니다.
        </p>
      </div>
    );
  }

  return (
    <section className="rounded-lg border">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full flex-wrap items-baseline gap-x-2 px-3.5 py-2 text-left"
      >
        <span className="text-[12.5px] font-medium">볼 채널</span>
        <span className="font-mono text-[11px] text-muted-foreground">
          {on}/{data.channels.length}
        </span>
        <span className="text-[11.5px] text-muted-foreground">
          {data.measured
            ? "내 커버리지를 말한 횟수 순"
            : "아직 못 셌습니다 — 증권사·리서치와 규모 순"}
        </span>
        <span className="ml-auto text-[11px] text-muted-foreground">
          {open ? "접기" : "고르기"}
        </span>
      </button>

      {open && (
        <div className={cn("border-t", busy && "opacity-60")}>
          {data.channels.map((c) => (
            <Row key={c.chat_id} row={c} onToggle={toggle} />
          ))}
          <p className="px-3.5 py-2 text-[11.5px] leading-[1.7] text-muted-foreground">
            고른 뒤 터미널에서{" "}
            <code className="font-mono">arc telegram sync --days 7</code> 로
            가져옵니다. <strong>켜 둔 것만</strong> 긁습니다 — 다 긁으면 하루
            3,000건이 쏟아지고 대부분은 이미 DART·뉴스 API로 갖고 있습니다.
          </p>
        </div>
      )}
    </section>
  );
}

function Row({
  row: c,
  onToggle,
}: {
  row: TgChannelRow;
  onToggle: (chatId: number, enabled: boolean) => void;
}) {
  return (
    <label
      className={cn(
        "flex cursor-pointer items-center gap-3 border-b px-3.5 py-1.5 last:border-b-0",
        c.enabled && "bg-accent/40",
      )}
    >
      <Checkbox
        checked={c.enabled}
        onCheckedChange={(v) => void onToggle(c.chat_id, !!v)}
      />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[12.5px]">
          {c.name}
          {c.stale && (
            <span className="ml-1.5 text-[10.5px] text-bad" title="한 달 넘게 글이 없습니다">
              💀
            </span>
          )}
        </span>
        <span className="block font-mono text-[10px] text-muted-foreground">
          {KIND_LABEL[c.kind] ?? c.kind}
          {c.username && ` · @${c.username}`}
          {c.last_post && ` · ${c.last_post}`}
        </span>
      </span>
      {/* **관련도가 있으면 그것이 답이다.** 규모는 그다음이다. */}
      {c.relevance > 0 && (
        <span className="rounded bg-ok/15 px-1.5 py-0.5 font-mono text-[11px] text-ok">
          내 종목 {c.relevance}
        </span>
      )}
      <span className="w-[64px] text-right font-mono text-[11.5px] tabular-nums text-muted-foreground">
        {c.subscribers > 0 ? c.subscribers.toLocaleString() : "—"}
      </span>
    </label>
  );
}
