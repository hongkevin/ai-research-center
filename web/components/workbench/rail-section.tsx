"use client";

import { useState } from "react";
import { ChevronRight } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * 접히는 구획.
 *
 * 패널을 더하기만 하고 접은 적이 없어서 오른쪽 열에 여섯 개가 전부 펼쳐진 채
 * 경쟁했다 — 목차·수정 이력·게이트·가정·변화·출처 53건. 다 보이면 아무것도
 * 안 보인다.
 *
 * **지금 하는 일에 필요한 것만 펼친다.** 나머지는 제목과 개수만 남긴다 —
 * 개수가 보이면 접혀 있어도 무엇이 있는지는 안다.
 */
export function RailSection({
  title,
  count,
  defaultOpen = false,
  tone,
  children,
}: {
  title: string;
  count?: number | string;
  defaultOpen?: boolean;
  /** 제목에 색을 줄 때 (차단처럼 즉시 눈에 띄어야 하는 것) */
  tone?: string;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-1.5 py-1 text-[11px] font-semibold uppercase tracking-[0.09em] text-muted-foreground hover:text-foreground"
      >
        <ChevronRight
          className={cn("size-3 flex-none transition-transform", open && "rotate-90")}
        />
        <span className={tone}>{title}</span>
        {count !== undefined && (
          <span className="font-mono normal-case tracking-normal">{count}</span>
        )}
      </button>
      {open && <div className="mt-1.5 mb-3">{children}</div>}
    </section>
  );
}
