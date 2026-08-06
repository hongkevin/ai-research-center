"use client";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { Converted } from "@/lib/api";

/**
 * 올린 문서에서 읽어낸 것을 **확인받는 자리**.
 *
 * 종목 적중률이 92%다. 나머지 8%를 조용히 통과시키면 엉뚱한 회사의 초안이
 * 나온다. 그리고 오래된 PDF는 글자가 부분적으로 깨져도 통계로 안 갈리므로
 * (2009년 리포트의 「매춗액」) 원문 일부를 함께 보여 준다 (D50).
 */
export function UploadConfirm({
  file,
  onAccept,
  onReject,
}: {
  file: Converted;
  onAccept: () => void;
  onReject: () => void;
}) {
  const c = file.company;
  return (
    <div>
      <p className="text-[12.5px] text-muted-foreground">
        {file.source_name} · {file.kind.toUpperCase()}
        {file.pages > 0 && ` · ${file.pages}쪽`} · {file.chars.toLocaleString()}
        자
      </p>

      {file.warnings.length > 0 && (
        <div className="mt-3 rounded-md border border-bad/30 bg-bad/10 px-3 py-2.5 text-[12px] text-bad">
          {file.warnings.map((w) => (
            <div key={w}>⚠ {w}</div>
          ))}
        </div>
      )}

      <div className="mt-4 rounded-lg border p-4">
        {c ? (
          <>
            <div className="flex items-baseline gap-2">
              <span className="text-[15px] font-medium">{c.short_name}</span>
              <span className="font-mono text-[12px] text-num">{c.symbol}</span>
              <Badge variant="secondary" className="text-[10.5px]">
                {c.market}
              </Badge>
            </div>
            <p className="mt-1 text-[12px] text-muted-foreground">{c.name}</p>
          </>
        ) : (
          <p className="text-[13px]">
            이 문서에서 종목을 읽지 못했습니다.
            <span className="text-muted-foreground">
              {" "}
              다음 화면에서 직접 고르시면 됩니다.
            </span>
          </p>
        )}
      </div>

      {file.outline.length > 0 && (
        <div className="mt-3 rounded-lg border p-4">
          <div className="text-[11px] font-semibold tracking-[0.09em] text-muted-foreground uppercase">
            읽어낸 구성
          </div>
          <ol className="mt-2 space-y-0.5 text-[12.5px] leading-[1.7]">
            {file.outline.slice(0, 8).map((s, i) => (
              <li key={s} className="truncate">
                <span className="mr-1.5 font-mono text-[11px] text-muted-foreground">
                  {i + 1}
                </span>
                {s}
              </li>
            ))}
          </ol>
          <p className="mt-2 text-[11.5px] text-muted-foreground">
            이 차례에 맞춰 씁니다. 숫자는 공시에서만 나옵니다.
          </p>
        </div>
      )}

      <details className="mt-3">
        <summary className="cursor-pointer text-[12px] text-muted-foreground hover:text-foreground">
          변환된 원문 보기 — 글자가 깨져 보이면 쓰지 마십시오
        </summary>
        <pre className="mt-2 max-h-[240px] overflow-auto rounded-md border p-3 font-mono text-[11px] leading-[1.7] whitespace-pre-wrap">
          {file.markdown.slice(0, 8000)}
        </pre>
      </details>

      <div className="mt-5 flex justify-end gap-2">
        <Button variant="ghost" size="sm" onClick={onReject}>
          다른 문서로
        </Button>
        <Button size="sm" onClick={onAccept}>
          {c ? `${c.short_name}로 이어쓰기` : "이 문서로 계속"}
        </Button>
      </div>
    </div>
  );
}
