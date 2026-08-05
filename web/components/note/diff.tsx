"use client";

import { cn } from "@/lib/utils";

/**
 * 낱말 단위 LCS diff.
 *
 * **무엇이 바뀌었는지가 이 루프의 산출물이다.** 수정 제안을 볼 때와 지난
 * 버전을 되짚을 때가 같은 것을 봐야 하므로 한 곳에 둔다.
 */
export function diffWords(before: string, after: string) {
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

export function Diff({
  before,
  after,
  className = "max-h-[180px]",
}: {
  before: string;
  after: string;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "overflow-y-auto rounded-md border bg-card p-3 text-[13px] leading-relaxed",
        className,
      )}
    >
      {diffWords(before, after).map((w, i) => (
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
  );
}
