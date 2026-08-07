"use client";

import { useEffect, useState } from "react";

import { ChannelPicker } from "@/components/senti/channel-picker";
import { Recommended } from "@/components/senti/recommended";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  getSentiment,
  KIND_LABEL,
  SESSION_LABEL,
  type Sentiment,
  type SentiMention,
  type SentiSample,
} from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * 시장 센티 — **지금 무슨 말이 도는가.**
 *
 * 브리프와 따로인 이유: 브리프는 「놓친 것이 없다」는 확인이라 짧아야 하고,
 * 센티는 **뒤지는 화면**이다. 어느 종목이 왜 도는지, 누가 말했는지, 언제부터
 * 말했는지를 파고든다. 한 화면에 두면 브리프가 아침에 안 읽힌다.
 *
 * **시간대가 뜻을 가진다.** 같은 「5회 언급」이라도 장전(밤사이 해외·전일
 * 리포트)·장중(지금 움직이는 것에 대한 반응)·장후(마감 리뷰)는 완전히 다른
 * 얘기다.
 *
 * **전부 미검증 레인이다** (D45). 화면 어디에도 이 숫자를 공시 수치와 같은
 * 무게로 그리지 않는다.
 */

const SESSIONS = ["pre", "intra", "post"] as const;

export function Senti() {
  const [data, setData] = useState<Sentiment | null>(null);
  const [day, setDay] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const s = await getSentiment(day);
        if (alive) setData(s);
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => {
      alive = false;
    };
  }, [day]);

  if (error) {
    return (
      <Alert variant="destructive" className="max-w-[720px]">
        <AlertDescription>{error}</AlertDescription>
      </Alert>
    );
  }
  if (!data) {
    return <p className="text-[13px] text-muted-foreground">읽는 중…</p>;
  }

  return (
    <div className="max-w-[900px] space-y-7">
      <div>
        <div className="flex flex-wrap items-baseline gap-x-3">
          <span className="text-[15px] font-medium">{data.day || "센티"}</span>
          <span className="text-[12.5px] text-muted-foreground">{data.note}</span>
          {(data.days?.length ?? 0) > 1 && (
            <select
              value={data.day}
              onChange={(e) => setDay(e.target.value)}
              className="ml-auto h-7 rounded-md border bg-transparent px-1.5 text-[12px]"
            >
              {data.days!.map((d) => (
                <option key={d} value={d}>
                  {d}
                </option>
              ))}
            </select>
          )}
        </div>
        <p className="mt-1 text-[11.5px] leading-[1.7] text-muted-foreground">
          텔레그램에서 읽은 것입니다. <strong>확인된 사실이 아니고</strong>{" "}
          여기 숫자는 본문 어디에도 들어가지 않습니다 — 언급이 몰린다는 것은
          「볼 만하다」는 신호이지 사실이 아닙니다.
        </p>
      </div>

      {/* **채널은 센티의 재료다.** 소비하는 자리에서 관리한다.
          추천이 먼저인 이유: 빈 목록을 채우는 것이 첫 일이다. */}
      <div className="space-y-2">
        <Recommended onAdopted={() => setDay((d) => d)} />
        <ChannelPicker onChanged={() => setDay((d) => d)} />
      </div>

      {data.total > 0 && <Rhythm data={data} />}

      {data.mentions.length > 0 ? (
        <>
          {/* **내 것이 먼저다.** 언급이 아무리 몰려도 내가 안 보는 종목은
              그 아래다 — 아침에 알고 싶은 것은 「시장에서 뜬 것」이 아니라
              「내 것 중에 뜬 것」이다. 순서만 바꾸면 눈에 안 띄어서
              **구획을 나눈다.** */}
          <MentionGroup
            title="내 커버·관심 종목"
            hint="리포트를 내거나 옆에서 보는 종목"
            mentions={data.mentions.filter(
              (m) => m.mine === "cover" || m.mine === "watch",
            )}
          />
          <MentionGroup
            title="내 피어 그룹"
            hint="확정해 고정한 그룹 안의 종목 — 「내 섹터」의 실질적 정의입니다"
            mentions={data.mentions.filter((m) => m.mine === "peer")}
          />
          <MentionGroup
            title="그 밖"
            hint="시장에서 돌지만 내 목록에는 없는 종목"
            mentions={data.mentions.filter((m) => !m.mine)}
            muted
          />
        </>
      ) : (
        <div className="rounded-lg border border-dashed px-4 py-6">
          <p className="text-[14px] font-medium">아직 볼 것이 없습니다.</p>
          <p className="mt-1 text-[12.5px] leading-[1.8] text-muted-foreground">
            터미널에서 <code className="font-mono">arc telegram sync</code> 로
            메시지를 가져오면 여기 뜹니다.
          </p>
        </div>
      )}

      {data.channels.length > 0 && <Channels data={data} />}
    </div>
  );
}

/** 그날의 소란 자체가 신호다 — 장중에 몰렸나, 장후에 몰렸나. */
function Rhythm({ data }: { data: Sentiment }) {
  const max = Math.max(...SESSIONS.map((s) => data.by_session[s] ?? 0), 1);
  return (
    <section className="flex flex-wrap gap-x-8 gap-y-3">
      {SESSIONS.map((s) => {
        const n = data.by_session[s] ?? 0;
        return (
          <div key={s} className="min-w-[110px]">
            <div className="flex items-baseline gap-1.5">
              <span className="text-[12px] text-muted-foreground">
                {SESSION_LABEL[s]}
              </span>
              <span className="font-mono text-[15px]">{n.toLocaleString()}</span>
            </div>
            <div className="mt-1 h-1 w-full rounded bg-muted">
              <div
                className="h-1 rounded bg-num"
                style={{ width: `${(n / max) * 100}%` }}
              />
            </div>
          </div>
        );
      })}
      <div className="ml-auto flex flex-wrap gap-x-3 self-end text-[11.5px] text-muted-foreground">
        {Object.entries(data.by_kind).map(([k, n]) => (
          <span key={k}>
            {KIND_LABEL[k] ?? k} <span className="font-mono">{n}</span>
          </span>
        ))}
      </div>
    </section>
  );
}

function MentionGroup({
  title,
  hint,
  mentions,
  muted = false,
}: {
  title: string;
  hint: string;
  mentions: SentiMention[];
  muted?: boolean;
}) {
  // **빈 구획을 세우지 않는다.** 「내 종목 0건」이 매일 떠 있으면 눈이
  // 그 자리를 지나치게 된다.
  if (mentions.length === 0) return null;
  return (
    <section className={cn(muted && "opacity-75")}>
      <h3 className="mb-1 flex items-baseline gap-2 border-b pb-1.5 text-[12px] font-semibold">
        {title}
        <span className="font-mono font-normal text-muted-foreground">
          {mentions.length}
        </span>
        <span className="ml-auto text-[11px] font-normal text-muted-foreground">
          {hint}
        </span>
      </h3>
      <div className="divide-y">
        {mentions.map((m) => (
          <MentionRow key={m.symbol} mention={m} />
        ))}
      </div>
    </section>
  );
}

function MentionRow({ mention: m }: { mention: SentiMention }) {
  const [open, setOpen] = useState(false);
  const total = SESSIONS.reduce((a, s) => a + (m.by_session[s] ?? 0), 0);
  return (
    <div className="py-2.5">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full flex-wrap items-baseline gap-x-3 gap-y-1 text-left"
      >
        <span className="text-[13.5px] font-medium">
          {/* 내 종목이 도는 것이 가장 먼저 알고 싶은 것이다 */}
          {(m.mine === "cover" || m.mine === "watch") && (
            <span className="mr-1 text-[10.5px] text-ok">
              {m.mine === "cover" ? "커버" : "관심"}
            </span>
          )}
          {m.name}
        </span>
        <span className="font-mono text-[11px] text-muted-foreground">
          {m.symbol}
          {m.via && <span className="ml-1.5 font-sans">· {m.via}</span>}
        </span>

        {/* 시간대 분포 — **언제 돌았는지가 절반이다** */}
        {total > 0 && (
          <span className="flex h-1.5 w-[76px] overflow-hidden rounded bg-muted">
            {SESSIONS.map((s) => (
              <span
                key={s}
                className={cn(
                  s === "pre" && "bg-warn",
                  s === "intra" && "bg-num",
                  s === "post" && "bg-muted-foreground",
                )}
                style={{ width: `${((m.by_session[s] ?? 0) / total) * 100}%` }}
                title={`${SESSION_LABEL[s]} ${m.by_session[s] ?? 0}건`}
              />
            ))}
          </span>
        )}

        <span className="ml-auto font-mono text-[12px] tabular-nums">
          <span className="text-[10.5px] text-muted-foreground">오늘 </span>
          {m.today}회
          <span className="ml-2 text-bad">×{m.ratio.toFixed(1)}</span>
          <span className="ml-2 text-[10.5px] text-muted-foreground">
            채널 {m.channels.length}
          </span>
        </span>
      </button>

      {open && (
        <ul className="mt-2 space-y-2 border-l-2 pl-3">
          {m.samples.map((s, i) => (
            <SampleRow key={i} sample={s} />
          ))}
          {m.samples.length === 0 && (
            <li className="text-[12px] text-muted-foreground">
              발췌를 남기지 못했습니다.
            </li>
          )}
        </ul>
      )}
    </div>
  );
}

function SampleRow({ sample: s }: { sample: SentiSample }) {
  return (
    <li className="text-[12.5px] leading-[1.7]">
      <div className="flex flex-wrap items-baseline gap-x-2 text-[11px] text-muted-foreground">
        <span>{SESSION_LABEL[s.session] ?? s.session}</span>
        <span className="font-mono">{s.at.slice(11, 16)}</span>
        <span>{s.channel}</span>
        <span className="rounded border px-1">{KIND_LABEL[s.kind] ?? s.kind}</span>
        {/* **앱을 직접 연다.** 웹 링크는 「Open in Telegram」을 한 번 더 거친다 */}
        {s.app_link && (
          <a href={s.app_link} className="text-num underline-offset-2 hover:underline">
            앱에서 열기 ↗
          </a>
        )}
        {s.web_link && (
          <a
            href={s.web_link}
            target="_blank"
            rel="noreferrer"
            className="underline-offset-2 hover:underline"
          >
            웹
          </a>
        )}
      </div>
      <p className="mt-0.5 text-muted-foreground">{s.excerpt}</p>
    </li>
  );
}

function Channels({ data }: { data: Sentiment }) {
  return (
    <section>
      <h3 className="mb-2 border-b pb-1.5 text-[12px] font-semibold">
        채널{" "}
        <span className="font-mono font-normal text-muted-foreground">
          {data.channels.length}
        </span>
      </h3>
      <div className="flex flex-wrap gap-x-4 gap-y-1.5">
        {data.channels.map((c) => (
          <span key={c.name} className="text-[12px]">
            {c.name}
            <span className="ml-1 text-[10.5px] text-muted-foreground">
              {KIND_LABEL[c.kind] ?? c.kind} · {c.count}
            </span>
          </span>
        ))}
      </div>
    </section>
  );
}
