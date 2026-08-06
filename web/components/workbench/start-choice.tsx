"use client";

import { useRef, useState } from "react";

import { convertFile, type Converted } from "@/lib/api";

/**
 * 초안을 어디서 시작하는가 — **이어서 쓸 것인가, 새로 쓸 것인가.**
 *
 * RA가 커버 중인 종목이면 대부분 이어 쓰는 쪽이다. 그래서 두 갈래를 나란히
 * 두되 이어쓰기를 왼쪽에 놓는다.
 *
 * **올리면 바로 쓰지 않는다.** 먼저 읽어서 「어느 회사인가」를 되돌려 준다.
 * 종목코드를 다시 치게 하면 업로드가 편의가 아니라 일이 하나 는 것이다.
 * 다만 읽어낸 종목은 실측 적중 92%라 **사람이 확인**한다 (D50).
 */
export function StartChoice({
  onContinue,
  onFresh,
}: {
  onContinue: (c: Converted) => void;
  onFresh: () => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function pick(file: File | undefined) {
    if (!file) return;
    setBusy(true);
    setError("");
    const got = await convertFile(file);
    setBusy(false);
    if ("error" in got) {
      setError(got.error);
      return;
    }
    onContinue(got);
  }

  return (
    <div>
      <input
        ref={input}
        type="file"
        accept=".pdf,.docx,.md,.markdown,.txt"
        className="hidden"
        onChange={(e) => void pick(e.target.files?.[0])}
      />

      <div className="grid gap-3 sm:grid-cols-2">
        <button
          type="button"
          disabled={busy}
          onClick={() => input.current?.click()}
          className="group rounded-lg border p-5 text-left transition-colors hover:border-num/50 hover:bg-num/[0.04] disabled:opacity-60"
        >
          <div className="text-[14px] font-medium">
            {busy ? "읽는 중…" : "직전 리포트에서 이어쓰기"}
          </div>
          <p className="mt-1.5 text-[12.5px] leading-[1.75] text-muted-foreground">
            쓰던 리포트를 올리면 <b>회사와 기준 보고서를 읽어</b> 채워 둡니다.
            그 구성으로 쓰고, 직전 추정과 이번 공시 기준선을 나란히 놓습니다.
          </p>
          <p className="mt-2.5 font-mono text-[11px] text-muted-foreground">
            PDF · DOCX · MD
          </p>
        </button>

        <button
          type="button"
          onClick={onFresh}
          className="rounded-lg border p-5 text-left transition-colors hover:border-num/50 hover:bg-num/[0.04]"
        >
          <div className="text-[14px] font-medium">새 종목으로 시작</div>
          <p className="mt-1.5 text-[12.5px] leading-[1.75] text-muted-foreground">
            종목명이나 코드로 찾습니다. 공시에서 수치를 읽어 초안의 뼈대를
            만듭니다.
          </p>
          <p className="mt-2.5 font-mono text-[11px] text-muted-foreground">
            KOSPI · KOSDAQ · KONEX
          </p>
        </button>
      </div>

      {error && <p className="mt-3 text-[12.5px] text-bad">{error}</p>}
    </div>
  );
}
