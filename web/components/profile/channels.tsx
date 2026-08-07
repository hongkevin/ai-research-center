"use client";

import { Checkbox } from "@/components/ui/checkbox";
import { isStale, KIND_LABEL, type TgChannel } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * 볼 채널 고르기 — **커버 종목과 같은 성격이다.**
 *
 * 다 긁으면 하루 3,000건이 쏟아지고 그중 대부분은 이미 DART·뉴스 API로
 * 갖고 있는 것이다(D66). 그래서 고른다.
 *
 * **추천이 위다.** 증권사 공식·리서치 채널은 출처가 확실하고 애널리스트 본인
 * 코멘트라 값이 다르다. 종토방은 개별 글이 아니라 **언급이 몰리는 것**이
 * 신호라 따로 묶는다.
 *
 * **구독자 수만 보면 시체를 잡는다.** 실측: 20,437명짜리 채널이 219일째
 * 정지, 18,638명짜리가 943일째 정지. 애널리스트가 퇴사하면 구독자는 남고
 * 채널만 죽는다 — 그래서 마지막 글 날짜를 함께 낸다.
 */

const GROUPS: { keys: string[]; title: string; hint: string }[] = [
  {
    keys: ["broker", "research"],
    title: "추천 — 증권사·리서치",
    hint: "출처가 확실하고 애널리스트 본인 코멘트입니다",
  },
  {
    keys: ["chatter", "unknown", "internal"],
    title: "종토방·기타",
    hint: "개별 글이 아니라 언급이 몰리는 것이 신호입니다",
  },
  {
    keys: ["bot_feed"],
    title: "봇·정형 알림",
    hint: "대부분 DART·뉴스 API와 중복입니다 — 필요할 때만",
  },
];

export function Channels({
  channels,
  onToggle,
}: {
  channels: TgChannel[];
  onToggle: (chatId: number, enabled: boolean) => void;
}) {
  const on = channels.filter((c) => c.enabled).length;

  if (channels.length === 0) {
    return (
      <section>
        <h3 className="mb-2 text-[12px] font-semibold">텔레그램 채널</h3>
        <div className="rounded-lg border border-dashed px-4 py-5">
          <p className="text-[13px]">아직 받아 둔 채널 목록이 없습니다.</p>
          <p className="mt-1 text-[12px] leading-[1.8] text-muted-foreground">
            터미널에서 <code className="font-mono">arc telegram login</code> 한
            뒤 <code className="font-mono">arc telegram channels</code> 를
            돌리면 구독 중인 채널이 여기 나옵니다. 로그인은 인증 코드를 받아
            쳐야 해서 <strong>터미널에서만</strong> 됩니다.
          </p>
        </div>
      </section>
    );
  }

  return (
    <section>
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-[12px] font-semibold">
          텔레그램 채널{" "}
          <span className="font-mono font-normal text-muted-foreground">
            {on}/{channels.length}
          </span>
        </h3>
        <span className="text-[11.5px] text-muted-foreground">
          <strong>켜 둔 채널만</strong> 가져옵니다 — 다 긁으면 하루 3,000건이
          쏟아집니다
        </span>
      </div>

      <div className="space-y-4">
        {GROUPS.map((g) => {
          const rows = channels
            .filter((c) => g.keys.includes(c.kind))
            .sort((a, b) => b.subscribers - a.subscribers);
          if (rows.length === 0) return null;
          return (
            <div key={g.title}>
              <p className="mb-1 flex items-baseline gap-2 text-[11.5px]">
                <span className="font-medium">{g.title}</span>
                <span className="text-muted-foreground">{g.hint}</span>
              </p>
              <div className="rounded-lg border">
                {rows.map((c) => (
                  <Row key={c.chat_id} channel={c} onToggle={onToggle} />
                ))}
              </div>
            </div>
          );
        })}
      </div>

      <p className="mt-2 text-[11.5px] leading-[1.7] text-muted-foreground">
        고른 뒤 터미널에서{" "}
        <code className="font-mono">arc telegram sync --days 7</code> 로
        가져옵니다. 가져온 것은 <strong>「시장 센티」 탭</strong>에서 봅니다.
      </p>
    </section>
  );
}

function Row({
  channel: c,
  onToggle,
}: {
  channel: TgChannel;
  onToggle: (chatId: number, enabled: boolean) => void;
}) {
  const dead = isStale(c);
  return (
    <label
      className={cn(
        "flex cursor-pointer items-center gap-3 border-b px-3 py-2 last:border-b-0",
        c.enabled && "bg-accent/40",
      )}
    >
      <Checkbox
        checked={c.enabled}
        onCheckedChange={(v) => onToggle(c.chat_id, !!v)}
      />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[13px]">
          {c.name}
          {/* **구독자 수만 보면 시체를 잡는다.** 애널리스트가 퇴사하면
              구독자는 남고 채널만 죽는다. */}
          {dead && (
            <span className="ml-1.5 text-[11px] text-bad" title="한 달 넘게 글이 없습니다">
              💀 멈춤
            </span>
          )}
        </span>
        <span className="block font-mono text-[10.5px] text-muted-foreground">
          {KIND_LABEL[c.kind] ?? c.kind}
          {c.username && ` · @${c.username}`}
          {c.last_post && ` · ${c.last_post}`}
        </span>
      </span>
      <span className="font-mono text-[12px] tabular-nums text-muted-foreground">
        {c.subscribers > 0 ? c.subscribers.toLocaleString() : "—"}
      </span>
    </label>
  );
}
