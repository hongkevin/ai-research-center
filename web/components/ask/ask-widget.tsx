"use client";

import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  AnswerBlock,
  SavedAnswer,
  type Turn,
} from "@/components/ask/answer-block";
import { Textarea } from "@/components/ui/textarea";
import {
  ask,
  createChat,
  deleteChat,
  getChat,
  listChats,
  type AskContext,
  type ChatSession,
} from "@/lib/api";
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
 *
 * 세션은 **서버에 있다** (D83). 전에는 `localStorage`였고 그때는 그게 맞는
 * 판단이었지만 — *"리퀘스트 이력을 장기기억으로 쌓는 것은 별도 결정"* — 그
 * 결정이 났다. 브라우저에 두면 지우는 순간 사라지고, 기기 간 동기화가 없고,
 * 무엇보다 **나중에 맥락으로 못 쓴다.**
 *
 * 옮기면서 지킨 것 둘:
 *
 * * **저장이 죽어도 채팅은 돈다.** 세션을 못 만들면 세션 없이 묻는다 —
 *   답은 나오고 기록만 안 남는다. 그 반대(기록은 되는데 답이 안 나온다)가
 *   훨씬 나쁘다
 * * **되살린 답에는 출처가 없다.** 저장하는 것이 본문뿐이라서다
 *   (`Turn.saved` 주석). 숨기지 않고 화면에 밝힌다
 */

function reason(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export function AskWidget({
  cardCount,
  onMakeReport,
}: {
  cardCount: number;
  /** 근거가 없을 때 그 종목의 리포트를 만들러 보낸다 (D85) */
  onMakeReport?: (symbol: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [activeId, setActiveId] = useState("");
  // 본문을 가져와 둔 대화. **`activeId`와 다르면 아직 안 읽었다는 뜻이다.**
  const [loadedId, setLoadedId] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [context, setContext] = useState<AskContext | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  // 저장소를 못 쓰는 상태. **채팅을 막지 않고 알리기만 한다.**
  const [storeError, setStoreError] = useState("");
  const [listOpen, setListOpen] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);

  // 목록만 먼저 받는다. **본문은 안 받는다** — 위젯은 대개 닫힌 채로 있고,
  // 닫힌 버튼이 쓰는 것은 「대화가 몇 개인가」뿐이다.
  useEffect(() => {
    // **effect 본문에서 바로 setState하면 연쇄 렌더가 난다.** 이 저장소에서
    // 이미 두 번 밟은 규칙이라(page.tsx) 비동기 뒤로 넘긴다.
    let alive = true;
    void (async () => {
      try {
        const list = await listChats();
        if (!alive) return;
        setSessions(list);
        // **직전 대화를 열지 않는다** (D86). 리퀘스트 하나 = 세션 하나가
        // 원칙인데, 열면 앞 세션이 활성이라 A고객 스레드에 B고객 질문이
        // 붙었다. 더 나쁜 것은 그 세션의 종목 맥락까지 이어받아 **다른
        // 종목 기준으로 답할 수 있다**는 점이다.
        //
        // 빈 채로 연다 — 첫 질문에서 세션이 만들어진다. 옛 대화는 목록에서
        // 고르면 된다.
        setActiveId("");
      } catch (e) {
        // 세션 없이도 물을 수 있다 — 기록만 안 남는다
        if (alive) setStoreError(reason(e));
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  // 열었을 때 그 대화의 본문을 가져온다. **여기서 대화를 만들지는 않는다** —
  // 화면을 한 번 봤다는 이유로 빈 대화가 쌓이면 목록이 쓰레기가 된다.
  useEffect(() => {
    if (!open || !activeId || loadedId === activeId) return;
    let alive = true;
    void (async () => {
      setLoading(true);
      try {
        const chat = await getChat(activeId);
        if (!alive) return;
        setTurns(
          chat.turns.map((t) => ({
            question: t.question,
            answer: null,
            saved: t.answer,
            error: "",
          })),
        );
        setContext(chat.context);
        setStoreError("");
      } catch (e) {
        if (!alive) return;
        setTurns([]);
        setContext(null);
        setStoreError(reason(e));
      } finally {
        if (alive) {
          // **실패해도 읽은 것으로 친다.** 아니면 같은 요청을 계속 다시 던진다.
          setLoadedId(activeId);
          setLoading(false);
        }
      }
    })();
    return () => {
      alive = false;
    };
  }, [open, activeId, loadedId]);

  const active = sessions.find((s) => s.id === activeId);

  function selectSession(id: string) {
    setActiveId(id);
    setTurns([]);
    setContext(null);
    setListOpen(false);
  }

  async function startSession() {
    setListOpen(false);
    setTurns([]);
    setContext(null);
    try {
      const created = await createChat();
      setSessions((v) => [created, ...v]);
      setActiveId(created.id);
      setLoadedId(created.id); // 방금 만들었으니 읽을 것이 없다
      setStoreError("");
    } catch (e) {
      setStoreError(reason(e));
    }
  }

  async function removeSession(id: string) {
    const rest = sessions.filter((s) => s.id !== id);
    setSessions(rest);
    try {
      await deleteChat(id);
    } catch (e) {
      setStoreError(reason(e));
    }
    if (id !== activeId) return;
    if (rest.length) selectSession(rest[0].id);
    else await startSession();
  }

  async function send(question: string) {
    const q = question.trim();
    if (!q || busy) return;
    setDraft("");
    setBusy(true);
    setTurns((v) => [...v, { question: q, answer: null, saved: "", error: "" }]);

    // **첫 질문에서야 대화를 만든다.** 실패하면 세션 없이 그냥 묻는다 —
    // 답이 나오는 것이 기록보다 중요하다.
    let id = activeId;
    if (!id) {
      try {
        const created = await createChat();
        id = created.id;
        setSessions((v) => [created, ...v]);
        setActiveId(id);
        setLoadedId(id);
      } catch (e) {
        setStoreError(reason(e));
      }
    }

    try {
      const answer = await ask(q, context, id);
      setContext(answer.context);
      setTurns((v) =>
        v.map((t, i) => (i === v.length - 1 ? { ...t, answer } : t)),
      );
      // 목록을 다시 받아오지 않는다 — 서버의 제목 규칙(첫 질문)을 여기서
      // 그대로 흉내 낼 수 있고, 그 편이 요청 하나를 아낀다.
      setSessions((v) =>
        v.map((s) =>
          s.id === id
            ? {
                ...s,
                title: s.turn_count === 0 ? q.slice(0, 40) : s.title,
                turn_count: s.turn_count + 1,
              }
            : s,
        ),
      );
    } catch (e) {
      const message = reason(e);
      setTurns((v) =>
        v.map((t, i) => (i === v.length - 1 ? { ...t, error: message } : t)),
      );
    } finally {
      setBusy(false);
      requestAnimationFrame(() =>
        bottom.current?.scrollIntoView({ behavior: "smooth" }),
      );
    }
  }

  if (!open) {
    const busySessions = sessions.filter((s) => s.turn_count > 0).length;
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="fixed right-6 bottom-6 z-40 flex h-12 items-center gap-2 rounded-full border bg-card px-4 text-[13px] font-medium shadow-lg transition-colors hover:bg-accent"
        aria-label="물어보기 열기"
      >
        물어보기
        {busySessions > 0 && (
          <span className="font-mono text-[11px] text-muted-foreground">
            {busySessions}
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
          {active?.title || "물어보기"}
          <span className="ml-1 text-[11px] text-muted-foreground">
            ▾ {sessions.length}
          </span>
        </button>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => void startSession()}
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
                onClick={() => selectSession(s.id)}
                className="min-w-0 flex-1 truncate text-left text-[12.5px]"
              >
                {s.title || "새 질문"}
                <span className="ml-1.5 text-[11px] text-muted-foreground">
                  {s.turn_count}턴
                </span>
              </button>
              <button
                type="button"
                onClick={() => void removeSession(s.id)}
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
        {/* **저장이 죽어도 채팅은 돈다.** 막지 않고 알리기만 한다. */}
        {storeError && (
          <p className="text-[12px] leading-[1.8] text-warn">
            대화를 저장하지 못하고 있습니다 — {storeError}. 답은 그대로
            나오지만 <strong>기록이 남지 않습니다.</strong>
          </p>
        )}
        {cardCount === 0 ? (
          <p className="text-[12.5px] leading-[1.8] text-muted-foreground">
            이 대화는 <strong>작성한 카드를 근거로만</strong> 답합니다. 아직
            카드가 없어 할 수 있는 말이 없습니다.
          </p>
        ) : loading ? (
          <p className="text-[12.5px] text-muted-foreground">
            저장된 대화를 불러오는 중…
          </p>
        ) : turns.length === 0 ? (
          <p className="text-[12.5px] leading-[1.8] text-muted-foreground">
            카드 {cardCount}건을 근거로 답합니다. 공시에서 확인할 수 없는 것은{" "}
            <strong>없다고 말합니다.</strong> 투자 판단은 내지 않습니다.
          </p>
        ) : null}
        {turns.map((t, i) => (
          <div key={i}>
            <p className="text-[13.5px] font-medium">{t.question}</p>
            {!t.answer && !t.saved && !t.error && (
              <p className="mt-2 text-[12.5px] text-muted-foreground">
                카드에서 근거를 찾는 중…
              </p>
            )}
            {t.error && <p className="mt-2 text-[12.5px] text-bad">{t.error}</p>}
            {t.answer ? (
              <AnswerBlock
                answer={t.answer}
                compact
                onMakeReport={onMakeReport}
              />
            ) : (
              t.saved && <SavedAnswer text={t.saved} compact />
            )}
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
            context?.symbols.length
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
