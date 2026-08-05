"use client";

import { useState } from "react";
import { X } from "lucide-react";

import { Diff } from "@/components/note/diff";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Hint } from "@/components/workbench/section-label";
import {
  proposeRevision,
  saveSection,
  type DocSection,
  type Proposal,
  type SaveResult,
} from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * 섹션 편집기 — **일을 하는 자리.**
 *
 * 화면 아래에 붙어 스크롤을 따라온다. 문서 위쪽에 고정돼 있으면 읽던 자리와
 * 편집하는 자리 사이를 왕복해야 한다.
 *
 * 두 가지 방식이 같은 저장 경로를 쓴다:
 *
 * * **코멘트** — LLM이 고쳐 쓴다. 플레이스홀더만 쓰므로 **숫자는 구조적으로
 *   바뀔 수 없다.**
 * * **직접 편집** — 사람이 원문을 고친다. 숫자를 그냥 타이핑하면 **G0가 막고
 *   이유를 말한다.** 불변식이 처음으로 사람에게 보이는 자리다.
 */

type Mode = "comment" | "edit";

export function SectionEditor({
  cardId,
  version,
  section,
  sections,
  onPick,
  onClose,
  onSaved,
}: {
  cardId: string;
  version: string;
  section: DocSection;
  /** 고칠 수 있는 섹션 전부. 편집기 안에서 갈아탈 수 있어야 한다 —
   *  제목 위 「수정」에만 의존하면 있는 줄도 모른다(실측). */
  sections: DocSection[];
  onPick: (title: string) => void;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [mode, setMode] = useState<Mode>("comment");
  const [comment, setComment] = useState("");
  const [draft, setDraft] = useState(section.text);
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [result, setResult] = useState<SaveResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // 섹션이 바뀌면 이 컴포넌트가 통째로 다시 마운트된다 (부모가 key를 준다).
  // 이전 섹션의 초안이 남으면 엉뚱한 곳에 저장되므로 초기화가 필수인데,
  // effect로 되돌리는 것보다 key로 리마운트하는 쪽이 React의 정석이다.

  async function propose() {
    setBusy(true);
    setError("");
    setProposal(null);
    try {
      setProposal(await proposeRevision(cardId, section.title, comment));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function save(after: string, note: string) {
    setBusy(true);
    setError("");
    const r = await saveSection(cardId, section.title, after, note);
    setResult(r);
    setBusy(false);
    if (r.ok) {
      setProposal(null);
      setComment("");
      onSaved();
    }
  }

  // **화면의 절반을 넘지 않는다.** shadcn Textarea는 `field-sizing-content`라
  // 내용만큼 늘어나서, 긴 섹션을 열면 시트가 문서를 통째로 덮어버린다. 상한을
  // 걸고 안쪽이 스크롤되게 한다 — 고치는 자리와 읽는 자리가 함께 보여야
  // 편집이 성립한다.
  return (
    <div className="fixed inset-x-0 bottom-0 z-40 flex max-h-[50dvh] flex-col border-t bg-card shadow-[0_-8px_24px_rgba(0,0,0,.12)]">
      <div className="mx-auto w-full max-w-[900px] overflow-y-auto px-5 py-3">
        {/* 섹션 갈아타기. 편집기를 닫고 다른 제목을 찾아 올라가는 왕복을 없앤다. */}
        <div className="mb-2 flex flex-wrap gap-1">
          {sections.map((s) => (
            <button
              key={s.title}
              type="button"
              onClick={() => onPick(s.title)}
              className={cn(
                "rounded-md border px-2 py-0.5 text-[11.5px]",
                s.title === section.title ? "border-num text-num" : "text-muted-foreground",
              )}
            >
              {s.title}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-2">
          <span className="truncate text-[13px] font-medium">{section.title}</span>
          <span className="font-mono text-[11px] text-muted-foreground">{version}</span>

          <div className="ml-2 flex gap-1">
            {(["comment", "edit"] as Mode[]).map((m) => (
              <button
                key={m}
                type="button"
                onClick={() => setMode(m)}
                className={cn(
                  "rounded-md border px-2 py-0.5 text-[11.5px]",
                  mode === m ? "border-num text-num" : "text-muted-foreground",
                )}
              >
                {m === "comment" ? "코멘트" : "직접 편집"}
              </button>
            ))}
          </div>

          <Button
            size="icon"
            variant="ghost"
            onClick={onClose}
            aria-label="닫기"
            className="ml-auto size-7"
          >
            <X className="size-3.5" />
          </Button>
        </div>

        {mode === "comment" ? (
          <>
            <Textarea
              rows={2}
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              disabled={busy}
              placeholder="예) 첫 문장이 결론이 아니라 나열로 시작합니다. 가장 중요한 한 가지를 앞으로 빼 주세요."
              className="mt-2 max-h-[18dvh] overflow-y-auto text-[13px]"
            />
            <Button
              size="sm"
              disabled={busy || !comment.trim()}
              onClick={propose}
              className="mt-2"
            >
              {busy ? "고쳐 쓰는 중…" : "수정 제안 받기"}
            </Button>

            {proposal && (
              <div className="mt-3">
                <div className="mb-1.5 flex flex-wrap items-center gap-1.5">
                  {proposal.numbers_unchanged ? (
                    <Badge className="border-transparent bg-ok/15 text-ok">
                      ● 수치 {proposal.numbers.length}개 변경 없음
                    </Badge>
                  ) : (
                    <Badge className="border-transparent bg-bad/15 text-bad">
                      ● 수치 구성이 바뀜
                    </Badge>
                  )}
                  {!proposal.changed && (
                    <Badge className="border-transparent bg-warn/15 text-warn">
                      ● 바뀐 것 없음
                    </Badge>
                  )}
                  <span className="font-mono text-[11px] text-muted-foreground">
                    {proposal.model}
                    {proposal.cost_usd != null && ` · $${proposal.cost_usd.toFixed(4)}`}
                  </span>
                </div>
                {proposal.problems.map((p, i) => (
                  <div key={i} className="mb-1.5 rounded-md bg-warn/10 px-2 py-1 text-[11.5px]">
                    {p}
                  </div>
                ))}
                <Diff before={proposal.before} after={proposal.after} />
                <div className="mt-2 flex gap-2">
                  <Button
                    size="sm"
                    disabled={busy || !proposal.changed}
                    onClick={() => save(proposal.after, proposal.comment)}
                  >
                    채택 — 버전 올리기
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setProposal(null)}>
                    버림
                  </Button>
                </div>
              </div>
            )}
          </>
        ) : (
          <>
            <Textarea
              rows={7}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              disabled={busy}
              // 시트가 절반을 넘지 않도록 여기서도 상한을 건다
              className="mt-2 max-h-[26dvh] overflow-y-auto font-mono text-[12.5px]"
            />
            <Hint>
              <code>{"{{num:키}}"}</code>는 레지스트리의 수치입니다. 지우면 그 숫자가
              사라지고, <b>숫자를 직접 타이핑하면 G0가 막습니다</b> — 이 문서의 모든 수치는
              출처로 되짚을 수 있어야 하기 때문입니다.
            </Hint>
            <div className="mt-2 flex gap-2">
              <Button
                size="sm"
                disabled={busy || draft.trim() === section.text.trim()}
                onClick={() => save(draft, "직접 편집")}
              >
                {busy ? "저장 중…" : "저장 — 버전 올리기"}
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setDraft(section.text)}>
                되돌리기
              </Button>
            </div>
            {draft.trim() !== section.text.trim() && (
              <div className="mt-2">
                <Diff before={section.text} after={draft} />
              </div>
            )}
          </>
        )}

        {error && <Hint>{error}</Hint>}
        {result && !result.ok && (
          <div className="mt-2 rounded-md border border-bad bg-bad/10 px-2.5 py-2 text-[12px]">
            <b className="text-bad">{result.error}</b>
            {result.violations?.map((v, i) => (
              <div key={i} className="mt-1 font-mono text-[11px]">
                {v.rule} — {v.detail.slice(0, 110)}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
