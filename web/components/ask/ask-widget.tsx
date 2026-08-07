"use client";

import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { AnswerBlock, type Turn } from "@/components/ask/answer-block";
import { Textarea } from "@/components/ui/textarea";
import { ask, type AskContext } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * 리서치 채팅 — **우측 하단 위젯.**
 *
 * 처음엔 보드 옆 탭으로 만들었는데 틀렸다. RA가 물어보는 순간은 대개
 * **카드를 보다가**인데, 탭이면 보던 것을 떠나야 한다. 위젯은 무엇 위에나
 * 뜨고 닫으면 하던 일로 돌아온다.
 *
 * **세션 하나 = 리퀘스트 하나.** 인터뷰에서 나온 하루 10~15건이 서로 다른
 * 클라이언트의 서로 다른 질문이라, 한 줄로 이어 붙이면 맥락이 섞인다.
 * 대화 상태(`context`)도 세션마다 따로 들고 있다 — 서버는 대화를 안 들고
 * 있으므로 여기서 갈라 두면 그것으로 끝난다.
 *
 * 세션은 **브라우저에 남긴다.** 서버에 두면 사용자 축·정리 정책·용량이
 * 따라오는데, 리퀘스트 이력을 장기기억으로 쌓는 것은 별도 결정이라
 * 그때 서버로 올린다.
 */

const STORAGE_KEY = "arc.ask.sessions.v1";
const MAX_SESSIONS = 12;

interface Session {
  id: string;
  title: string;
  turns: Turn[];
  context: AskContext | null;
}

function newSession(): Session {
  return {
    id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
    title: "새 질문",
    turns: [],
    context: null,
  };
}

function load(): Session[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : null;
    return Array.isArray(parsed) && parsed.length ? parsed : [];
  } catch {
    return [];
  }
}

export function AskWidget({ cardCount }: { cardCount: number }) {
  const [open, setOpen] = useState(false);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [activeId, setActiveId] = useState("");
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [listOpen, setListOpen] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // **effect 본문에서 바로 setState하면 연쇄 렌더가 난다.** 이 저장소에서
    // 이미 두 번 밟은 규칙이라(page.tsx) 마이크로태스크 뒤로 넘긴다.
    let alive = true;
    void Promise.resolve().then(() => {
      if (!alive) return;
      const saved = load();
      const list = saved.length ? saved : [newSession()];
      setSessions(list);
      setActiveId(list[0].id);
    });
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    if (!sessions.length) return;
    try {
      window.localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify(sessions.slice(0, MAX_SESSIONS)),
      );
    } catch {
      /* 용량이 차면 저장만 못 한다 — 대화는 계속된다 */
    }
  }, [sessions]);

  const active = sessions.find((s) => s.id === activeId) ?? sessions[0];

  function update(id: string, fn: (s: Session) => Session) {
    setSessions((v) => v.map((s) => (s.id === id ? fn(s) : s)));
  }

  async function send(question: string) {
    const q = question.trim();
    if (!q || busy || !active) return;
    const id = active.id;
    setDraft("");
    setBusy(true);
    update(id, (s) => ({
      ...s,
      // **첫 질문이 세션의 이름이 된다.** 목록에서 리퀘스트를 알아보는
      // 유일한 단서라 따로 짓게 하지 않는다.
      title: s.turns.length === 0 ? q.slice(0, 28) : s.title,
      turns: [...s.turns, { question: q, answer: null, error: "" }],
    }));
    try {
      const answer = await ask(q, active.context);
      update(id, (s) => ({
        ...s,
        context: answer.context,
        turns: s.turns.map((t, i) =>
          i === s.turns.length - 1 ? { ...t, answer } : t,
        ),
      }));
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      update(id, (s) => ({
        ...s,
        turns: s.turns.map((t, i) =>
          i === s.turns.length - 1 ? { ...t, error: message } : t,
        ),
      }));
    } finally {
      setBusy(false);
      requestAnimationFrame(() =>
        bottom.current?.scrollIntoView({ behavior: "smooth" }),
      );
    }
  }

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="fixed right-6 bottom-6 z-40 flex h-12 items-center gap-2 rounded-full border bg-card px-4 text-[13px] font-medium shadow-lg transition-colors hover:bg-accent"
        aria-label="물어보기 열기"
      >
        물어보기
        {sessions.some((s) => s.turns.length > 0) && (
          <span className="font-mono text-[11px] text-muted-foreground">
            {sessions.filter((s) => s.turns.length > 0).length}
          </span>
        )}
      </button>
    );
  }

  return (
    <div className="fixed right-6 bottom-6 z-40 flex h-[min(640px,80dvh)] w-[min(460px,calc(100vw-3rem))] flex-col rounded-xl border bg-card shadow-2xl">
      <header className="flex items-center gap-1.5 border-b px-3 py-2">
        <button
          type="button"
          onClick={() => setListOpen((v) => !v)}
          className="min-w-0 flex-1 truncate text-left text-[13px] font-medium hover:underline"
        >
          {active?.title ?? "물어보기"}
          <span className="ml-1 text-[11px] text-muted-foreground">
            ▾ {sessions.length}
          </span>
        </button>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => {
            const s = newSession();
            setSessions((v) => [s, ...v].slice(0, MAX_SESSIONS));
            setActiveId(s.id);
            setListOpen(false);
          }}
          className="h-7 text-[12px]"
        >
          새 질문
        </Button>
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="px-1.5 text-[15px] text-muted-foreground hover:text-foreground"
          aria-label="닫기"
        >
          ×
        </button>
      </header>

      {listOpen && (
        <div className="max-h-[220px] overflow-y-auto border-b">
          {sessions.map((s) => (
            <div
              key={s.id}
              className={cn(
                "flex items-center gap-2 border-b px-3 py-1.5 last:border-b-0",
                s.id === activeId && "bg-accent/50",
              )}
            >
              <button
                type="button"
                onClick={() => {
                  setActiveId(s.id);
                  setListOpen(false);
                }}
                className="min-w-0 flex-1 truncate text-left text-[12.5px]"
              >
                {s.title}
                <span className="ml-1.5 text-[11px] text-muted-foreground">
                  {s.turns.length}턴
                </span>
              </button>
              <button
                type="button"
                onClick={() =>
                  setSessions((v) => {
                    const rest = v.filter((x) => x.id !== s.id);
                    const list = rest.length ? rest : [newSession()];
                    if (s.id === activeId) setActiveId(list[0].id);
                    return list;
                  })
                }
                className="text-[12px] text-muted-foreground hover:text-bad"
                aria-label="이 질문 지우기"
              >
                ×
              </button>
            </div>
          ))}
        </div>
      )}

      <div className="flex-1 space-y-5 overflow-y-auto px-3 py-3">
        {cardCount === 0 ? (
          <p className="text-[12.5px] leading-[1.8] text-muted-foreground">
            이 대화는 <strong>작성한 카드를 근거로만</strong> 답합니다. 아직
            카드가 없어 할 수 있는 말이 없습니다.
          </p>
        ) : active?.turns.length === 0 ? (
          <p className="text-[12.5px] leading-[1.8] text-muted-foreground">
            카드 {cardCount}건을 근거로 답합니다. 공시에서 확인할 수 없는 것은{" "}
            <strong>없다고 말합니다.</strong> 투자 판단은 내지 않습니다.
          </p>
        ) : null}
        {active?.turns.map((t, i) => (
          <div key={i}>
            <p className="text-[13.5px] font-medium">{t.question}</p>
            {!t.answer && !t.error && (
              <p className="mt-2 text-[12.5px] text-muted-foreground">
                카드에서 근거를 찾는 중…
              </p>
            )}
            {t.error && <p className="mt-2 text-[12.5px] text-bad">{t.error}</p>}
            {t.answer && <AnswerBlock answer={t.answer} compact />}
          </div>
        ))}
        <div ref={bottom} />
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          void send(draft);
        }}
        className="border-t p-2.5"
      >
        <Textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              void send(draft);
            }
          }}
          placeholder={
            active?.context?.symbols.length
              ? "이어서 — 종목을 다시 안 써도 됩니다"
              : "종목과 함께 물어보십시오"
          }
          rows={2}
          className="resize-none text-[13px]"
        />
        <div className="mt-1.5 flex items-center justify-between">
          <span className="text-[11px] text-muted-foreground">
            Enter 전송 · Shift+Enter 줄바꿈
          </span>
          <Button type="submit" size="sm" disabled={busy || !draft.trim()}>
            {busy ? "찾는 중…" : "보내기"}
          </Button>
        </div>
      </form>
    </div>
  );
}
