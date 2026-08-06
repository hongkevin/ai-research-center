"use client";

import { useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Hint } from "@/components/workbench/section-label";
import { fillModel, type FillSummary } from "@/lib/api";

/**
 * 엑셀 모델에 공시 실적 채워 넣기 (D62).
 *
 * **남의 파일에 쓰는 일이다.** 그래서 화면이 셋을 약속한다:
 * 수식은 안 건드린다 · 원본은 그대로 두고 사본을 준다 · 무엇을 어디에
 * 썼는지 전부 보여준다.
 */
export function ModelFill({ cardId }: { cardId: string }) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [summary, setSummary] = useState<FillSummary | null>(null);
  const [unit, setUnit] = useState("1000000");

  async function pick(file: File | undefined) {
    if (!file) return;
    setBusy(true);
    setError("");
    setSummary(null);
    const got = await fillModel(cardId, file, Number(unit) || 1);
    setBusy(false);
    if ("error" in got) {
      setError(got.error);
      return;
    }
    setSummary(got.summary);
  }

  return (
    <div>
      <input
        ref={input}
        type="file"
        accept=".xlsx,.xlsm"
        className="hidden"
        onChange={(e) => void pick(e.target.files?.[0])}
      />

      <div className="flex items-center gap-2">
        <select
          value={unit}
          onChange={(e) => setUnit(e.target.value)}
          disabled={busy}
          className="h-7 rounded-md border bg-background px-2 text-[12px]"
        >
          <option value="1000000000">십억원 모델</option>
          <option value="1000000">백만원 모델</option>
          <option value="100000000">억원 모델</option>
          <option value="1">원 모델</option>
        </select>
        <Button
          size="sm"
          variant="outline"
          disabled={busy}
          onClick={() => input.current?.click()}
          className="h-7 flex-1 text-[12px]"
        >
          {busy ? "채우는 중…" : "모델 올려서 채우기"}
        </Button>
      </div>

      <Hint>
        {error ? (
          <span className="text-bad">{error}</span>
        ) : (
          <>
            <b>수식은 건드리지 않습니다.</b> 원본은 그대로 두고 채운 사본을
            내려받습니다. 행 라벨(매출액·영업이익)과 연도 머리행(2025A)이 있는
            시트를 찾습니다.
          </>
        )}
      </Hint>

      {summary && (
        <div className="mt-2 rounded-md border p-3 text-[12px]">
          <div className="font-medium">
            {summary.written.length}칸을 채웠습니다
            {summary.skipped.length > 0 &&
              ` · ${summary.skipped.length}칸 건너뜀`}
          </div>
          <table className="mt-2 w-full">
            <tbody>
              {summary.written.slice(0, 12).map((w) => (
                <tr
                  key={`${w.sheet}${w.cell}`}
                  className="border-b last:border-b-0"
                >
                  <td className="py-0.5 font-mono text-[11px] text-muted-foreground">
                    {w.sheet}!{w.cell}
                  </td>
                  <td className="py-0.5">
                    {w.label}{" "}
                    <span className="text-muted-foreground">{w.year}</span>
                  </td>
                  <td className="py-0.5 text-right font-mono text-num">
                    {w.after.toLocaleString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {summary.skipped.length > 0 && (
            <div className="mt-2 text-[11.5px] text-muted-foreground">
              건너뛴 칸:{" "}
              {summary.skipped
                .slice(0, 6)
                .map((s) => `${s.cell} (${s.reason})`)
                .join(" · ")}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
