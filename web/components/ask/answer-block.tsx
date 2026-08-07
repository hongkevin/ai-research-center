"use client";

import { useState } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import type { Answer, AskHint, AskSource } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * 답 하나를 그리는 곳. **탭과 위젯이 같은 것을 쓴다.**
 *
 * 두 벌로 두면 「미검증」 배지나 「이어받음」 표시가 한쪽에서만 빠지는데,
 * 그건 조용히 빠지고 그 순간 기사에서 온 문장이 공시에서 온 문장으로 읽힌다.
 */

export interface Turn {
  question: string;
  answer: Answer | null;
  error: string;
}

export function AnswerBlock({
  answer,
  compact = false,
}: {
  answer: Answer;
  compact?: boolean;
}) {
  const [openSources, setOpenSources] = useState(false);

  // **D4로 거부한 것은 답이 아니다.** 다른 것을 그리지 않는다.
  if (answer.refused) {
    return (
      <Alert className="mt-3">
        <AlertTitle>투자 판단은 내지 않습니다</AlertTitle>
        <AlertDescription>{answer.refused}</AlertDescription>
      </Alert>
    );
  }

  const text = compact ? "text-[12.5px]" : "text-[14px]";
  const numbers = answer.sources.filter((s) => s.kind === "number");
  const cards = answer.sources.filter((s) => s.kind === "card");

  return (
    <div className="mt-3 space-y-4">
      {/* **이어받았으면 밝힌다.** 조용히 이어받으면 사용자가 다른 회사를
          생각했을 때 틀린 답을 확신 있게 하게 된다. */}
      {answer.carried_over.length > 0 && (
        <p className="text-[12px] text-muted-foreground">
          ↳ 이어받음: {answer.carried_over.join(" · ")}
        </p>
      )}

      {answer.facts && (
        <p className={cn("leading-[1.9] whitespace-pre-wrap", text)}>
          {answer.facts}
        </p>
      )}

      {answer.analysis && (
        <p className={cn("leading-[1.9] whitespace-pre-wrap text-muted-foreground", text)}>
          {answer.analysis}
        </p>
      )}

      {/* **힌트는 반드시 다른 블록이다.** 여기 문장은 검증된 것이 아니다. */}
      {answer.hints.length > 0 && <HintBlock hints={answer.hints} />}

      {answer.unanswered.length > 0 && (
        <div className="text-[12px] leading-[1.8] text-muted-foreground">
          <span className="font-medium">답하지 못한 것</span>
          <ul className="mt-1 list-disc space-y-0.5 pl-4">
            {answer.unanswered.map((u, i) => (
              <li key={i}>{u}</li>
            ))}
          </ul>
        </div>
      )}

      {answer.sources.length > 0 && (
        <div>
          <button
            type="button"
            onClick={() => setOpenSources((v) => !v)}
            className="text-[12px] text-muted-foreground underline-offset-2 hover:underline"
          >
            출처 {numbers.length > 0 && `수치 ${numbers.length}건 · `}
            카드 {cards.length}건 {openSources ? "접기" : "펼치기"}
          </button>
          {openSources && (
            <div className="mt-2 space-y-2 rounded-md border p-3">
              {answer.sources.map((s, i) => (
                <SourceRow key={i} source={s} />
              ))}
            </div>
          )}
        </div>
      )}

      <p className="text-[11px] text-muted-foreground">
        {answer.grounded ? "근거에 연결됨" : "근거를 찾지 못함"}
        {answer.model && ` · ${answer.model}`}
        {answer.cost_usd != null && ` · $${answer.cost_usd.toFixed(4)}`}
      </p>
    </div>
  );
}

/**
 * 미검증 레인.
 *
 * 색과 배지로 갈라 놓는 것이 이 블록의 전부다 — 같은 회색 문단으로 그리면
 * 「보도됐다」가 「공시됐다」로 읽힌다.
 */
function HintBlock({ hints }: { hints: AskHint[] }) {
  return (
    <div className="rounded-md border border-warn/40 bg-warn/5 p-3">
      <Badge variant="outline" className="border-warn/50 text-[11px] text-warn">
        미검증 · 기사 기반
      </Badge>
      <ul className="mt-2 space-y-3">
        {hints.map((h, i) => (
          <li key={i} className="text-[13px] leading-[1.8]">
            {h.text}
            <ul className="mt-1 space-y-0.5">
              {h.articles.map((a, j) => (
                <li key={j} className="text-[11px] text-muted-foreground">
                  ↳{" "}
                  <a
                    href={a.url}
                    target="_blank"
                    rel="noreferrer"
                    className="underline-offset-2 hover:underline"
                  >
                    {a.press} {a.date} · {a.title}
                  </a>
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ul>
      <p className="mt-3 text-[11px] leading-[1.7] text-muted-foreground">
        기사에서 읽은 것입니다. <strong>확인된 사실이 아니고</strong> 숫자는
        싣지 않습니다 — 되짚을 링크만 답니다.
      </p>
    </div>
  );
}

function SourceRow({ source: s }: { source: AskSource }) {
  const label = s.kind === "number" ? s.label : `${s.company} (${s.symbol})`;
  return (
    <div className="text-[12px] leading-[1.7]">
      <div className="flex flex-wrap items-baseline gap-x-2">
        {s.marker && (
          <span className="font-mono text-[11px] text-muted-foreground">
            {s.marker}
          </span>
        )}
        <span className="font-medium">{label}</span>
        {s.value && <span className="font-mono">{s.value}</span>}
      </div>
      <div className="text-muted-foreground">
        {s.kind === "number"
          ? [s.dataset, s.document].filter(Boolean).join(" → ")
          : [s.period_label, ...(s.sections ?? [])].filter(Boolean).join(" · ")}
        {s.verify_url && (
          <>
            {" · "}
            <a
              href={s.verify_url}
              target="_blank"
              rel="noreferrer"
              className={cn("underline-offset-2 hover:underline")}
            >
              원문 확인
            </a>
          </>
        )}
      </div>
    </div>
  );
}
