"use client";

import { useState } from "react";
import { ChevronRight } from "lucide-react";

import { Hint, SectionLabel } from "@/components/workbench/section-label";
import type { Stage } from "@/lib/api";
import type { Step } from "@/lib/use-generation";
import { cn } from "@/lib/utils";

/**
 * 파이프라인 단계 레일.
 *
 * **진행 표시와 단계 기록은 같은 것의 두 상태다.** 생성 중에는 단계가 흘러가고,
 * 끝나면 같은 자리에 그 단계가 무엇을 했는지 남는다. 이 화면이 종목코드를 넣으면
 * 30초 뒤 완성본을 뱉는 블랙박스였던 것을 여는 자리다.
 *
 * 단계 목록을 하드코딩하지 않는다 — `vm.stages`에서 온다. 엔진에 단계가 붙으면
 * 화면에 자동으로 나타난다(D33·D34·D35가 그렇게 붙었다).
 */

const STATUS: Record<Stage["status"], { mark: string; cls: string; label: string }> = {
  ok: { mark: "●", cls: "text-ok", label: "완료" },
  partial: { mark: "◐", cls: "text-warn", label: "일부" },
  // **없음은 실패가 아니다.** 단일 부문 회사에 부문 손익이 없는 건 정상이다.
  absent: { mark: "○", cls: "text-muted-foreground", label: "없음" },
  failed: { mark: "✕", cls: "text-bad", label: "실패" },
};

export function StageRail({
  stages,
  steps,
  running,
  elapsed,
}: {
  stages: Stage[];
  steps: Step[];
  running: boolean;
  elapsed: number;
}) {
  const [open, setOpen] = useState<Set<string>>(new Set());

  // 생성 중에는 SSE가 흘려보내는 단계를 그대로 보여준다
  if (running || stages.length === 0) {
    if (steps.length === 0) return null;
    return (
      <div className="mt-6">
        <SectionLabel>진행</SectionLabel>
        {steps.map((s, i) => {
          const last = i === steps.length - 1;
          return (
            <div
              key={i}
              className={cn(
                "flex items-baseline gap-2 py-0.5 text-[12.5px]",
                running && last ? "text-primary font-semibold" : "text-ok",
              )}
            >
              <span className="w-3.5 flex-none text-center">{running && last ? "▸" : "✓"}</span>
              <span>{s.message}</span>
            </div>
          );
        })}
        {running && <div className="mt-2 text-[11.5px] text-muted-foreground">{elapsed}초</div>}
      </div>
    );
  }

  const registered = stages.reduce((n, s) => n + s.registered, 0);

  return (
    <section className="mt-6">
      <SectionLabel>파이프라인 {stages.length}단계</SectionLabel>
      <div className="divide-y rounded-lg border">
        {stages.map((s) => {
          const st = STATUS[s.status] ?? STATUS.ok;
          const expandable = s.checks.length > 0 || Boolean(s.note);
          const isOpen = open.has(s.key);
          return (
            <div key={s.key} className="px-2.5 py-2">
              <button
                type="button"
                disabled={!expandable}
                aria-expanded={expandable ? isOpen : undefined}
                onClick={() =>
                  setOpen((prev) => {
                    const next = new Set(prev);
                    if (next.has(s.key)) next.delete(s.key);
                    else next.add(s.key);
                    return next;
                  })
                }
                className={cn(
                  "flex w-full items-baseline gap-2 text-left text-[12.5px]",
                  expandable && "cursor-pointer",
                )}
              >
                <span className={cn("w-3.5 flex-none text-center", st.cls)} title={st.label}>
                  {st.mark}
                </span>
                <span className="flex-1 min-w-0">
                  <span className="font-medium">{s.label}</span>
                  {s.summary && (
                    <span className="text-muted-foreground"> — {s.summary}</span>
                  )}
                </span>
                {s.registered > 0 && (
                  <span
                    className="flex-none font-mono text-[11px] text-num"
                    title="이 단계가 레지스트리에 넣은 수치"
                  >
                    +{s.registered}
                  </span>
                )}
                {expandable && (
                  <ChevronRight
                    className={cn(
                      "size-3 flex-none text-muted-foreground transition-transform",
                      isOpen && "rotate-90",
                    )}
                  />
                )}
              </button>

              {isOpen && (
                <div className="mt-1.5 pl-5">
                  {s.checks.map((c, i) => (
                    <div key={i} className="flex justify-between gap-2 py-0.5 text-[11.5px]">
                      <span className="text-muted-foreground">{c.label}</span>
                      <span className={cn("text-right font-mono", c.ok ? "" : "text-bad")}>
                        {c.value}
                      </span>
                    </div>
                  ))}
                  {s.note && <Hint>{s.note}</Hint>}
                </div>
              )}
            </div>
          );
        })}
      </div>
      <Hint>
        <span className="text-num">+n</span>은 그 단계가 만든 수치입니다. 전체{" "}
        {registered}건이 레지스트리를 거쳐 본문에 들어갑니다 — 이 경로 밖의 숫자는 G0가
        막습니다.
      </Hint>
    </section>
  );
}
