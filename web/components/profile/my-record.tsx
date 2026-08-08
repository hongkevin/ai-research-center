"use client";

import { useEffect, useState } from "react";

import {
  EVENT_LABEL,
  getEvents,
  type EventCount,
  type EventSummary,
} from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * 내 기록 — **쌓인 것이 보여야 쌓을 마음이 든다.**
 *
 * 사건 로그를 눈에 안 보이는 데 두면 그게 맞게 쌓이는지 아무도 모른다.
 * 그리고 여기 나오는 것이 곧 나중에 질문할 때 맥락으로 들어갈 것들이라,
 * **미리 보고 「이건 아닌데」를 말할 수 있어야 한다.**
 *
 * **무엇이 남는지 화면이 말한다.** 사용자 활동을 기록하면서 알리지 않는 것은
 * 안 된다 — 그래서 아래 설명이 장식이 아니라 이 화면의 절반이다.
 *
 * **숫자는 안 남는다.** 질문은 가려서, 편집은 조립본(`{{num:key}}`) 그대로
 * 남는다. 불변식 1이 여기서 깨지면 안 된다.
 */
export function MyRecord() {
  const [summary, setSummary] = useState<EventSummary | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const got = await getEvents(30);
        if (alive) setSummary(got.summary);
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  if (error || !summary) return null;

  return (
    <section className="border-t pt-6">
      <div className="mb-2 flex flex-wrap items-baseline gap-x-2.5">
        <h3 className="text-[12px] font-semibold">내 기록</h3>
        <span className="text-[11.5px] text-muted-foreground">최근 30일</span>
        {summary.total > 0 && (
          <span className="font-mono text-[11px] text-muted-foreground">
            {summary.total}건
          </span>
        )}
      </div>

      {summary.total === 0 ? (
        <p className="rounded-lg border border-dashed px-3.5 py-3 text-[12px] leading-[1.8] text-muted-foreground">
          아직 쌓인 것이 없습니다. 리포트를 열고·묻고·고치면 여기 남습니다.
          <br />
          <strong>목록은 설정이지 개인화가 아닙니다</strong> — 도구가 사람을
          알게 되는 것은 무엇을 고쳤고 무엇을 안 넣었나에서 옵니다.
        </p>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2">
          <Block
            title="자주 본 것"
            hint="지금 집중하는 종목"
            rows={summary.focus}
          />
          <Block
            title="두 번 이상 물은 것"
            hint="답이 부족했다는 신호"
            rows={summary.repeated}
            tone="warn"
          />
          {summary.edited_sections.length > 0 && (
            <div className="rounded-lg border px-3 py-2.5">
              <p className="text-[12px] font-medium">문장을 고친 곳</p>
              <p className="mb-1.5 text-[10.5px] text-muted-foreground">
                생성이 약한 자리입니다
              </p>
              {summary.edited_sections.map((s) => (
                <Line key={s.section} label={s.section} count={s.count} />
              ))}
            </div>
          )}
          <Block
            title="넣지 않은 피어"
            hint="「이건 내 피어가 아니다」"
            rows={summary.skipped_peers}
          />
        </div>
      )}

      {/* **무엇이 남는지 말한다.** 기록하면서 안 알리는 것은 안 된다. */}
      <p className="mt-2.5 text-[11px] leading-[1.75] text-muted-foreground">
        남는 것: {Object.keys(summary.by_kind).length > 0
          ? Object.entries(summary.by_kind)
              .map(([k, n]) => `${EVENT_LABEL[k] ?? k} ${n}`)
              .join(" · ")
          : "열어봄 · 질문 · 문장 고침 · 넘김 · 피어 선택"}
        . <strong>숫자는 안 남습니다</strong> — 질문은 수치를 가려서, 고친
        문장은 조립본(플레이스홀더) 그대로 남습니다.
      </p>
    </section>
  );
}

function Block({
  title,
  hint,
  rows,
  tone,
}: {
  title: string;
  hint: string;
  rows: EventCount[];
  tone?: "warn";
}) {
  if (rows.length === 0) return null;
  return (
    <div className={cn("rounded-lg border px-3 py-2.5", tone === "warn" && "border-warn/50")}>
      <p className="text-[12px] font-medium">{title}</p>
      <p className="mb-1.5 text-[10.5px] text-muted-foreground">{hint}</p>
      {rows.map((r) => (
        <Line key={r.subject} label={r.company || r.subject} count={r.count} />
      ))}
    </div>
  );
}

function Line({ label, count }: { label: string; count: number }) {
  return (
    <div className="flex items-baseline gap-2 py-0.5">
      <span className="min-w-0 flex-1 truncate text-[12px]">{label}</span>
      <span className="font-mono text-[11px] tabular-nums text-muted-foreground">
        {count}
      </span>
    </div>
  );
}
