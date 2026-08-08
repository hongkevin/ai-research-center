"use client";

import { useState } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
  /** 이번에 받은 답. 출처·힌트가 다 붙어 있다 */
  answer: Answer | null;
  /**
   * 서버에서 되살린 답. **본문만 있다.**
   *
   * 저장하는 것이 본문뿐인 것은 빠뜨린 게 아니라 정한 것이다 — 출처 줄을
   * 통째로 복제하면 카드가 바뀐 뒤에도 옛말을 하게 되고, 그게 정확히 D51에서
   * 밟은 실수다. 출처가 필요하면 다시 물으면 된다.
   */
  saved: string;
  error: string;
}

export function AnswerBlock({
  answer,
  compact = false,
  onMakeReport,
}: {
  answer: Answer;
  compact?: boolean;
  /** 근거가 없을 때 그 종목의 리포트를 만들러 보낸다 */
  onMakeReport?: (symbol: string) => void;
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

      {/* **답을 못 한 이유는 `text`에만 있다** (D85).
          전에는 `facts`·`analysis`만 그려서, 근거 없음·LLM 실패·문장이 전부
          검증 탈락·LLM 꺼짐이 화면에서 **똑같은 침묵**으로 보였다.

          이 제품의 슬로건이 「모르면 모른다고 한다」인데 **모른다고 말하는 그
          문장이 버려지고 있었다.** 그리고 그 문장만이 다음 행동을 지시한다. */}
      {!answer.facts && !answer.analysis && answer.text && (
        <div className="rounded-lg border border-dashed px-3.5 py-3">
          <p className={cn("leading-[1.8]", text)}>{answer.text}</p>
          {onMakeReport && answer.context.symbols.length > 0 && (
            <Button
              size="sm"
              variant="outline"
              className="mt-2.5"
              onClick={() => onMakeReport(answer.context.symbols[0])}
            >
              이 종목 리포트 만들기
            </Button>
          )}
        </div>
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

      {/* **출처 없는 문장을 표시한다** (D85). `guard.py`가 *"화면이 이 목록을
          보여주면 검토자가 어디를 의심할지 안다"*고 적어 놓고 화면은 안 쓰고
          있었다 — 출처 없는 문장이 출처 있는 문장과 같은 검은 글씨로 나갔다.
          클라이언트에게 그대로 붙여넣는 용도라 이건 컴플라이언스 문제다. */}
      {answer.unsourced.length > 0 && (
        <div className="rounded-md border border-warn/60 px-3 py-2">
          <p className="text-[11.5px] font-medium text-warn">
            출처가 안 붙은 문장 — 그대로 인용하지 마십시오
          </p>
          <ul className="mt-1 list-disc space-y-0.5 pl-4 text-[12px] leading-[1.7]">
            {answer.unsourced.map((u, i) => (
              <li key={i}>{u}</li>
            ))}
          </ul>
        </div>
      )}

      {/* 검증에서 버려진 문장. **버렸다는 사실을 숨기지 않는다** — 답이 짧아진
          이유가 여기 있다. */}
      {answer.rejected.length > 0 && (
        <details className="text-[11.5px] text-muted-foreground">
          <summary className="cursor-pointer">
            근거가 없어 버린 문장 {answer.rejected.length}개
          </summary>
          <ul className="mt-1 list-disc space-y-0.5 pl-4 leading-[1.7]">
            {answer.rejected.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        </details>
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
 * 저장해 둔 답.
 *
 * 숫자는 **서버가 끼워 준 것**이다 — 저장된 본문에는 `{{num:key}}`만 있고
 * 치환은 경계에서 한 번 일어난다. 그래야 저장된 대화가 나중에 맥락으로
 * 조립돼도 LLM이 값을 보지 않는다.
 *
 * 출처를 안 그리는 이유는 안 저장하기 때문이다(`Turn.saved` 주석). 그 사실을
 * 숨기지 않고 밝힌다 — 「출처가 없는 답」과 「출처를 안 실은 기록」은 다르다.
 */
export function SavedAnswer({
  text,
  compact = false,
}: {
  text: string;
  compact?: boolean;
}) {
  return (
    <div className="mt-3 space-y-2">
      <p
        className={cn(
          "leading-[1.9] whitespace-pre-wrap",
          compact ? "text-[12.5px]" : "text-[14px]",
        )}
      >
        {text}
      </p>
      <p className="text-[11px] text-muted-foreground">
        저장된 기록입니다 — 출처는 다시 물으면 나옵니다.
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
