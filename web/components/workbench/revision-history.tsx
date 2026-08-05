"use client";

import { useState } from "react";
import { ChevronRight } from "lucide-react";

import { Diff } from "@/components/note/diff";
import { Card, CardContent } from "@/components/ui/card";
import { Hint, SectionLabel } from "@/components/workbench/section-label";
import type { Revision } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * 수정 이력 — 어느 버전에서 무엇이 왜 바뀌었나.
 *
 * 데이터는 진작 카드에 쌓이고 있었다(`Card.versions`). 화면이 안 보여줬을 뿐이다.
 *
 * 코멘트를 함께 남기는 것이 핵심이다. "무엇이 바뀌었나"는 diff가 답하지만
 * **"왜 바뀌었나"는 그때 남긴 말에만 있다.**
 */
export function RevisionHistory({ versions }: { versions: Revision[] }) {
  const [open, setOpen] = useState<string | null>(null);

  if (versions.length === 0) {
    return (
      <section>
        <SectionLabel>수정 이력</SectionLabel>
        <Hint>아직 수정이 없습니다. 「문서 수정」으로 코멘트를 남기면 여기에 쌓입니다.</Hint>
      </section>
    );
  }

  return (
    <section>
      <SectionLabel>수정 이력 {versions.length}건</SectionLabel>
      <Card>
        <CardContent className="py-3">
          {/* 최신이 위로 — 되짚을 때는 방금 한 것부터 본다 */}
          {[...versions].reverse().map((v) => {
            const isOpen = open === v.version;
            return (
              <div key={v.version} className="border-b py-1.5 last:border-b-0">
                <button
                  type="button"
                  onClick={() => setOpen(isOpen ? null : v.version)}
                  className="flex w-full items-baseline gap-2 text-left"
                >
                  <span className="flex-none font-mono text-[11.5px] text-num">{v.version}</span>
                  <span className="min-w-0 flex-1 truncate text-[12px]">{v.section}</span>
                  <ChevronRight
                    className={cn(
                      "size-3 flex-none text-muted-foreground transition-transform",
                      isOpen && "rotate-90",
                    )}
                  />
                </button>
                <div className="pl-[38px] text-[11.5px] text-muted-foreground">
                  {v.comment || "직접 편집"}
                </div>

                {isOpen && (
                  <div className="mt-1.5">
                    <Diff before={v.before} after={v.after} className="max-h-[220px]" />
                    <Hint>{new Date(v.created_at).toLocaleString("ko-KR")}</Hint>
                  </div>
                )}
              </div>
            );
          })}
        </CardContent>
      </Card>
    </section>
  );
}
