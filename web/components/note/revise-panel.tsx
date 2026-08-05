"use client";

import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Hint, SectionLabel } from "@/components/workbench/section-label";
import {
  acceptRevision,
  listSections,
  proposeRevision,
  type DocSection,
  type Proposal,
} from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * 코멘트 → LLM 수정 → diff → 채택. **일을 하는 자리다.**
 *
 * 왜 안전한가: LLM은 `{{num:key}}` 플레이스홀더만 쓰고 값은 프롬프트에 들어가지도
 * 않는다. 그래서 이 루프가 문서를 고쳐도 **숫자는 구조적으로 바뀔 수 없다** —
 * diff는 문장에만 생긴다. 약속이 아니라 구조다.
 *
 * 채택은 서버가 **G0를 다시 돌린 뒤에만** 받아준다. 게이트를 건너뛰면 리뷰 루프가
 * 불변식을 우회하는 뒷문이 된다.
 */

/** 낱말 단위 LCS diff. 무엇이 바뀌었는지가 이 루프의 산출물이다. */
function diffWords(before: string, after: string) {
  const a = before.split(/(\s+)/);
  const b = after.split(/(\s+)/);
  const n = a.length;
  const m = b.length;
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const out: { text: string; kind: "same" | "del" | "add" }[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      out.push({ text: a[i], kind: "same" });
      i++;
      j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) out.push({ text: a[i++], kind: "del" });
    else out.push({ text: b[j++], kind: "add" });
  }
  while (i < n) out.push({ text: a[i++], kind: "del" });
  while (j < m) out.push({ text: b[j++], kind: "add" });
  return out;
}

export function RevisePanel({
  cardId,
  version,
  onAccepted,
}: {
  cardId: string;
  version: string;
  onAccepted: () => void;
}) {
  const [sections, setSections] = useState<DocSection[]>([]);
  const [section, setSection] = useState("");
  const [comment, setComment] = useState("");
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    listSections(cardId)
      .then((d) => {
        if (!alive) return;
        const editable = d.sections.filter((s) => s.editable);
        setSections(editable);
        setSection((cur) => cur || editable[0]?.title || "");
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [cardId]);

  async function propose() {
    setBusy(true);
    setError("");
    setProposal(null);
    try {
      setProposal(await proposeRevision(cardId, section, comment));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function accept() {
    if (!proposal) return;
    setBusy(true);
    setError("");
    try {
      await acceptRevision(cardId, proposal);
      setProposal(null);
      setComment("");
      onAccepted();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="max-w-[860px] rounded-lg border p-4">
      <div className="flex items-baseline justify-between">
        <SectionLabel className="mb-0">검토 코멘트</SectionLabel>
        <span className="font-mono text-[11px] text-muted-foreground">{version}</span>
      </div>

      <div className="mt-2.5 flex flex-wrap gap-1">
        {sections.map((s) => (
          <button
            key={s.title}
            type="button"
            onClick={() => setSection(s.title)}
            className={cn(
              "rounded-md border px-2 py-0.5 text-[11.5px]",
              s.title === section ? "border-num text-num" : "text-muted-foreground",
            )}
          >
            {s.title}
          </button>
        ))}
      </div>

      <Textarea
        rows={2}
        value={comment}
        onChange={(e) => setComment(e.target.value)}
        disabled={busy}
        placeholder="예) 첫 문장이 결론이 아니라 나열로 시작합니다. 가장 중요한 한 가지를 앞으로 빼 주세요."
        className="mt-2.5 text-[13px]"
      />
      <Button
        size="sm"
        disabled={busy || !comment.trim() || !section}
        onClick={propose}
        className="mt-2"
      >
        {busy ? "고쳐 쓰는 중…" : "수정 제안 받기"}
      </Button>

      {error && <Hint>{error}</Hint>}

      {proposal && (
        <div className="mt-4">
          <div className="flex flex-wrap items-center gap-1.5">
            {proposal.numbers_unchanged ? (
              <Badge className="border-transparent bg-ok/15 text-ok">
                ● 수치 {proposal.numbers.length}개 변경 없음
              </Badge>
            ) : (
              <Badge className="border-transparent bg-bad/15 text-bad">● 수치 구성이 바뀜</Badge>
            )}
            {!proposal.changed && (
              <Badge className="border-transparent bg-warn/15 text-warn">● 바뀐 것 없음</Badge>
            )}
            <span className="font-mono text-[11px] text-muted-foreground">
              {proposal.model}
              {proposal.cost_usd != null && ` · $${proposal.cost_usd.toFixed(4)}`}
            </span>
          </div>

          {proposal.problems.map((p, i) => (
            <div key={i} className="mt-1.5 rounded-md bg-warn/10 px-2 py-1 text-[11.5px]">
              {p}
            </div>
          ))}

          <div className="mt-2.5 rounded-md border bg-card p-3 text-[13px] leading-relaxed">
            {diffWords(proposal.before, proposal.after).map((w, i) => (
              <span
                key={i}
                className={cn(
                  w.kind === "del" && "bg-bad/15 text-bad line-through",
                  w.kind === "add" && "bg-ok/15 text-ok",
                )}
              >
                {w.text}
              </span>
            ))}
          </div>

          <div className="mt-2.5 flex gap-2">
            <Button size="sm" disabled={busy || !proposal.changed} onClick={accept}>
              채택 — 버전 올리기
            </Button>
            <Button size="sm" variant="ghost" disabled={busy} onClick={() => setProposal(null)}>
              버림
            </Button>
          </div>
          <Hint>
            채택하면 서버가 <b>G0를 다시 돌린 뒤에만</b> 받습니다. 막히면 저장되지 않습니다.
          </Hint>
        </div>
      )}
    </section>
  );
}
