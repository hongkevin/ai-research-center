"use client";

import { useRef, useState } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  ask,
  type Answer,
  type AskContext,
  type AskHint,
  type AskSource,
} from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * 리서치 채팅 — 카드를 근거로 답하고 출처를 낸다.
 *
 * 왜 별도 탭인가: 인터뷰가 준 가장 큰 발견이 *"리포트 쓰는 시간보다 훨씬 많은
 * 비중은 클라이언트 리퀘스트"*(하루 10~15건)였다. 리포트 작성과 **무관하게**
 * 도는 일이라 카드 안에 두면 갈 곳이 없다.
 *
 * **일반 챗봇이 아니다.** 설계 원칙은 「모르면 모른다고 한다」다 — 답을
 * 지어내면 청구가 안 되고, 한 번 틀리면 다음부터 안 쓴다.
 *
 * 화면이 지켜야 할 것 셋:
 *
 * 1. **힌트는 검증 레인과 다른 블록에** 「미검증」 배지와 함께 그린다. 합치면
 *    기사에서 온 문장이 공시에서 온 문장의 신뢰도로 읽힌다 (D31·D45).
 * 2. **이어받은 것은 반드시 보여준다.** 조용히 이어받으면 다른 회사를 생각한
 *    사용자에게 틀린 답을 확신 있게 하게 된다.
 * 3. **대화 상태는 여기 있다.** 서버가 안 들고 있으므로 직전 답의 `context`를
 *    다음 요청에 그대로 되돌려준다.
 */

interface Turn {
  question: string;
  answer: Answer | null;
  error: string;
}

const EXAMPLES = [
  "영업이익률이 어떻게 됐어?",
  "부문별로 어디가 제일 많이 벌어?",
  "직전 분기 대비 뭐가 달라졌어?",
];

export function AskPanel({ cardCount }: { cardCount: number }) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);

  // **직전 답이 남긴 것만 이월한다** — 종목·주제·연도 셋뿐이다.
  const context: AskContext | null =
    turns.findLast((t) => t.answer)?.answer?.context ?? null;

  async function send(question: string) {
    const q = question.trim();
    if (!q || busy) return;
    setDraft("");
    setBusy(true);
    setTurns((ts) => [...ts, { question: q, answer: null, error: "" }]);
    try {
      const answer = await ask(q, context);
      setTurns((ts) =>
        ts.map((t, i) => (i === ts.length - 1 ? { ...t, answer } : t)),
      );
    } catch (e) {
      setTurns((ts) =>
        ts.map((t, i) =>
          i === ts.length - 1
            ? { ...t, error: e instanceof Error ? e.message : String(e) }
            : t,
        ),
      );
    } finally {
      setBusy(false);
      requestAnimationFrame(() =>
        bottom.current?.scrollIntoView({ behavior: "smooth" }),
      );
    }
  }

  if (cardCount === 0) {
    return (
      <div className="py-20 text-center">
        <p className="text-[15px] font-medium">먼저 리포트가 있어야 합니다.</p>
        <p className="mx-auto mt-2 max-w-[440px] text-[13px] leading-[1.8] text-muted-foreground">
          이 대화는 <strong>작성한 카드를 근거로만</strong> 답합니다. 근거가
          없으면 지어내지 않고 없다고 말합니다 — 그래서 카드가 하나도 없으면
          할 수 있는 말이 없습니다.
        </p>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-[860px]">
      {turns.length === 0 && (
        <div className="mb-8">
          <p className="text-[13px] leading-[1.8] text-muted-foreground">
            작성한 리포트 {cardCount}건을 근거로 답합니다. 공시에서 확인할 수
            없는 것은 <strong>없다고 말합니다.</strong> 투자 판단은 내지
            않습니다.
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            {EXAMPLES.map((e) => (
              <button
                key={e}
                type="button"
                onClick={() => void send(e)}
                className="rounded-full border px-3 py-1.5 text-[12px] text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
              >
                {e}
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="space-y-8">
        {turns.map((t, i) => (
          <TurnBlock key={i} turn={t} />
        ))}
        <div ref={bottom} />
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          void send(draft);
        }}
        className="sticky bottom-0 mt-8 border-t bg-background pt-4 pb-6"
      >
        <Textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            // 줄바꿈은 Shift+Enter. RA의 질문은 한 줄이 대부분이다.
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              void send(draft);
            }
          }}
          placeholder={
            context?.symbols.length
              ? "이어서 물어보십시오 — 「그럼 작년은?」처럼 종목을 다시 안 써도 됩니다"
              : "종목과 함께 물어보십시오"
          }
          rows={2}
          className="resize-none text-[14px]"
        />
        <div className="mt-2 flex items-center justify-between">
          <span className="text-[11px] text-muted-foreground">
            Enter로 보냅니다 · Shift+Enter 줄바꿈
          </span>
          <Button type="submit" size="sm" disabled={busy || !draft.trim()}>
            {busy ? "찾는 중…" : "물어보기"}
          </Button>
        </div>
      </form>
    </div>
  );
}

function TurnBlock({ turn }: { turn: Turn }) {
  const { question, answer, error } = turn;
  return (
    <div>
      <p className="text-[15px] font-medium">{question}</p>

      {!answer && !error && (
        <p className="mt-3 text-[13px] text-muted-foreground">
          카드에서 근거를 찾는 중…
        </p>
      )}

      {error && (
        <Alert variant="destructive" className="mt-3">
          <AlertTitle>답하지 못했습니다</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {answer && <AnswerBlock answer={answer} />}
    </div>
  );
}

function AnswerBlock({ answer }: { answer: Answer }) {
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
        <p className="text-[14px] leading-[1.9] whitespace-pre-wrap">
          {answer.facts}
        </p>
      )}

      {answer.analysis && (
        <p className="text-[14px] leading-[1.9] whitespace-pre-wrap text-muted-foreground">
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
