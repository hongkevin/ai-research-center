"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  getTgChannels,
  KIND_LABEL,
  refreshTgChannels,
  setTgChannels,
  syncTgMessages,
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
  // **켜 둔 채널이 없으면 펼쳐 둔다.** 접힌 「고르기」 막대만 보이면 센티 탭이
  // 통째로 비어 보이고, 무엇을 해야 채워지는지가 화면에 없다.
  const [open, setOpen] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // 마지막 가져오기 결과. **버튼을 눌렀는데 아무 말이 없으면 안 눌린 줄 안다**
  const [said, setSaid] = useState("");

  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const got = await getTgChannels();
        if (!alive) return;
        setData(got);
        setOpen((v) => v ?? !got.channels.some((c) => c.enabled));
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

  async function reload() {
    setData(await getTgChannels());
  }

  async function refresh() {
    setBusy(true);
    setError("");
    setSaid("");
    try {
      const got = await refreshTgChannels();
      await reload();
      setSaid(`구독 채널 ${got.found}개를 받았습니다.`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function pull() {
    setBusy(true);
    setError("");
    setSaid("");
    try {
      const got = await syncTgMessages(7);
      // **못 받은 채널을 삼키지 않는다** — 켰는데 안 오면 죽은 채널이다
      const missed = got.skipped
        ? ` · ${got.skipped}개 채널은 그 기간에 글이 없습니다`
        : "";
      setSaid(
        got.messages
          ? `${got.days}일치 ${got.messages.toLocaleString()}건을 가져왔습니다${missed}.`
          : `가져올 메시지가 없었습니다${missed}.`,
      );
      await reload();
      onChanged?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  if (error && !data) return <p className="text-[12px] text-bad">{error}</p>;
  if (!data) return null;

  const on = data.channels.filter((c) => c.enabled).length;

  if (data.channels.length === 0) {
    return (
      <div className="rounded-lg border border-dashed px-4 py-4">
        <p className="text-[13px] font-medium">볼 채널을 아직 못 받았습니다.</p>
        <p className="mt-1 text-[12px] leading-[1.8] text-muted-foreground">
          아래 <strong>「구독 채널 받기」</strong>를 누르면 들어가 있는 채널이
          여기 나옵니다. 처음 한 번은 터미널에서{" "}
          <code className="font-mono">arc telegram login</code> 이 필요합니다 —
          인증 코드를 받아 쳐야 해서 화면에서는 안 됩니다.
        </p>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Button size="sm" onClick={() => void refresh()} disabled={busy}>
            {busy ? "받는 중…" : "구독 채널 받기"}
          </Button>
          {said && (
            <span className="text-[11.5px] text-muted-foreground">{said}</span>
          )}
          {error && <span className="text-[11.5px] text-bad">{error}</span>}
        </div>
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
          {on === 0
            ? "아직 하나도 안 켰습니다 — 켜야 센티가 채워집니다"
            : data.measured
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
          <div className="flex flex-wrap items-center gap-2 px-3.5 py-2.5">
            <Button
              size="sm"
              onClick={() => void pull()}
              disabled={busy || on === 0}
            >
              {busy ? "가져오는 중…" : `켜 둔 ${on}개에서 7일치 가져오기`}
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => void refresh()}
              disabled={busy}
            >
              구독 채널 다시 받기
            </Button>
            {said && (
              <span className="text-[11.5px] text-muted-foreground">{said}</span>
            )}
            {error && <span className="text-[11.5px] text-bad">{error}</span>}
          </div>
          <p className="px-3.5 pb-2 text-[11.5px] leading-[1.7] text-muted-foreground">
            <strong>켜 둔 것만</strong> 긁습니다 — 다 긁으면 하루 3,000건이
            쏟아지고 대부분은 이미 DART·뉴스 API로 갖고 있습니다.
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
