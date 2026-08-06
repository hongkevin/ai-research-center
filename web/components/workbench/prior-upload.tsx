"use client";

import { useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Hint, SectionLabel } from "@/components/workbench/section-label";
import { convertFile, type Converted } from "@/lib/api";

/**
 * 직전 노트 올리기 (D48).
 *
 * **RA는 백지에서 시작하지 않는다.** 커버 중인 종목이면 자기가 쓴 노트가 이미
 * 있고, 그게 기준선이자 형식이다.
 *
 * **변환 결과를 사람이 보고 넘긴다.** 오래된 PDF는 글자가 부분적으로 깨져
 * 나오는데(2009년 리포트의 「매춗액」) 통계로는 정상 문서와 안 갈린다. 자동
 * 판정을 믿게 만들면 조용히 쓰레기가 들어간다.
 */
export function PriorUpload({
  value,
  name,
  onChange,
  disabled,
}: {
  value: string;
  name: string;
  onChange: (markdown: string, name: string) => void;
  disabled?: boolean;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [preview, setPreview] = useState<Converted | null>(null);
  const [open, setOpen] = useState(false);

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
    setPreview(got);
    setOpen(true);
  }

  function accept() {
    if (preview) onChange(preview.markdown, preview.source_name);
    setOpen(false);
  }

  function clear() {
    onChange("", "");
    setPreview(null);
    setOpen(false);
    if (input.current) input.current.value = "";
  }

  return (
    <>
      <SectionLabel>직전 노트 (선택)</SectionLabel>
      <input
        ref={input}
        type="file"
        accept=".pdf,.docx,.md,.markdown,.txt"
        className="hidden"
        onChange={(e) => void pick(e.target.files?.[0])}
      />

      {value ? (
        <Card className="mt-1.5">
          <CardContent className="flex items-center justify-between gap-2 py-2.5">
            <span className="min-w-0 truncate text-[12.5px]">{name}</span>
            <span className="flex flex-none gap-1">
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setOpen(true)}
                className="h-6 px-2 text-[11.5px]"
              >
                보기
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={clear}
                className="h-6 px-2 text-[11.5px] hover:text-bad"
              >
                해제
              </Button>
            </span>
          </CardContent>
        </Card>
      ) : (
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={busy || disabled}
          onClick={() => input.current?.click()}
          className="mt-1.5 w-full"
        >
          {busy ? "읽는 중…" : "PDF · Word · MD 올리기"}
        </Button>
      )}

      <Hint>
        {error ? (
          <span className="text-bad">{error}</span>
        ) : value ? (
          <>
            이 노트의 <b>구성</b>에 맞춰 쓰고, 추정치는 <b>기준선</b>으로
            비교합니다. 문서의 숫자는 본문에 들어가지 않습니다.
          </>
        ) : (
          "쓰던 리포트를 올리면 그 구성으로 쓰고, 직전 추정과 비교합니다. 한글(HWP)은 PDF로 내보내 주십시오."
        )}
      </Hint>

      {/* 변환 결과 확인 — smallpdf가 하는 것과 같은 자리다 */}
      {open && preview && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-6"
          onClick={() => setOpen(false)}
        >
          <div
            className="flex max-h-[80dvh] w-full max-w-[760px] flex-col rounded-lg border bg-background"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-baseline justify-between border-b px-4 py-3">
              <span className="text-[13px] font-medium">
                {preview.source_name}
              </span>
              <span className="font-mono text-[11.5px] text-muted-foreground">
                {preview.kind.toUpperCase()}
                {preview.pages > 0 && ` · ${preview.pages}쪽`} ·{" "}
                {preview.chars.toLocaleString()}자
              </span>
            </div>

            {preview.warnings.length > 0 && (
              <div className="border-b border-bad/30 bg-bad/10 px-4 py-2.5 text-[12px] text-bad">
                {preview.warnings.map((w) => (
                  <div key={w}>⚠ {w}</div>
                ))}
              </div>
            )}

            {preview.outline.length > 0 && (
              <div className="border-b px-4 py-2.5 text-[12px]">
                <span className="text-muted-foreground">읽어낸 차례 · </span>
                {preview.outline.slice(0, 8).join(" / ")}
              </div>
            )}

            <pre className="min-h-0 flex-1 overflow-auto px-4 py-3 font-mono text-[11.5px] leading-[1.7] whitespace-pre-wrap">
              {preview.markdown.slice(0, 20000)}
            </pre>

            <div className="flex items-center justify-between gap-3 border-t px-4 py-3">
              <span className="text-[11.5px] text-muted-foreground">
                글자가 깨져 보이면 쓰지 마십시오. 원본을 다른 방법으로 내보내면
                됩니다.
              </span>
              <span className="flex flex-none gap-2">
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => setOpen(false)}
                >
                  닫기
                </Button>
                <Button size="sm" onClick={accept}>
                  이 문서를 직전 노트로
                </Button>
              </span>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
