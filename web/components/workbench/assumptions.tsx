"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { RailSection } from "@/components/workbench/rail-section";
import { Hint } from "@/components/workbench/section-label";
import { recompute, type EstimateYear } from "@/lib/api";

/**
 * 추정 가정 — **이 제품에서 사람의 판단이 들어가는 유일한 구멍** (D24).
 *
 * 한때 초안 작성 폼에 있었는데 틀린 자리였다. 성장률은 과거를 보기 전에 정할
 * 수 없다 — 「최근 2개년 평균이 43.66%였다」를 모르는 상태에서 15를 넣으라는
 * 것이었다. 틀린 것을 물어서가 아니라 **틀린 때에 물어서** 어색했다.
 *
 * **연차는 사람이 늘린다.** 기계는 한 해만 세운다 — D34 실측에서 1년차
 * 영업이익 오차가 이미 중앙값 55.9%라, 그 위에 2년차를 기계가 얹으면
 * 그럴듯해 보이는 노이즈가 는다. 반면 RA가 「초기 2년 20%, 후기 10%」라고
 * 하면 기계는 예측하지 않고 산술만 한다.
 */

const KEYS = ["revenue_growth", "operating_margin", "net_margin"] as const;

export function Assumptions({
  cardId,
  years,
  onRecomputed,
}: {
  cardId: string;
  years: EstimateYear[];
  onRecomputed: () => void;
}) {
  // 화면에서 편집 중인 값. 연차 인덱스 → 키 → 문자열.
  const [draft, setDraft] = useState<Record<number, Record<string, string>>>({});
  const [extra, setExtra] = useState(0); // 사람이 추가한 연차 수
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  if (years.length === 0) {
    return (
      <RailSection title="추정 가정" count={0}>
        <Hint>이 종목은 기준선을 세울 과거 실적이 부족합니다.</Hint>
      </RailSection>
    );
  }

  const shown = years.length + extra;
  const baseOf = (i: number) => years[Math.min(i, years.length - 1)];
  const valueOf = (i: number, key: string) => {
    const d = draft[i]?.[key];
    if (d !== undefined) return d;
    const a = baseOf(i).assumptions.find((x) => x.key === key);
    return a ? String(a.value) : "";
  };
  const labelOf = (key: string) =>
    years[0].assumptions.find((a) => a.key === key)?.label ?? key;
  const yearOf = (i: number) => years[0].fiscal_year + i;

  async function apply() {
    setBusy(true);
    setError("");
    const pack = (i: number) =>
      Object.fromEntries(
        KEYS.filter((k) => valueOf(i, k) !== "").map((k) => [k, Number(valueOf(i, k))]),
      );
    const r = await recompute(
      cardId,
      pack(0),
      Array.from({ length: shown - 1 }, (_, i) => pack(i + 1)),
    );
    setBusy(false);
    if ("error" in r) {
      setError(r.error);
      return;
    }
    setDraft({});
    setExtra(0);
    onRecomputed();
  }

  return (
    <RailSection title="추정 가정" count={`${shown}개년`} defaultOpen>
      <Card>
        <CardContent className="py-3">
          {Array.from({ length: shown }, (_, i) => (
            <div key={i} className="mb-3 border-b pb-2.5 last:border-b-0">
              <div className="mb-1 flex items-baseline justify-between">
                <span className="font-mono text-[11.5px] text-num">{yearOf(i)}E</span>
                {i === 0 && (
                  <span className="text-[11px] text-muted-foreground">기준선</span>
                )}
                {i > 0 && (
                  <span className="text-[11px] text-muted-foreground">
                    {yearOf(i - 1)} 추정 위에
                  </span>
                )}
              </div>
              {KEYS.map((key) => (
                <div key={key} className="flex items-center justify-between gap-2 py-0.5">
                  <span className="min-w-0 truncate text-[12px]">{labelOf(key)}</span>
                  <span className="flex flex-none items-center gap-1">
                    <Input
                      type="number"
                      step="0.1"
                      value={valueOf(i, key)}
                      disabled={busy}
                      onChange={(e) =>
                        setDraft((d) => ({ ...d, [i]: { ...d[i], [key]: e.target.value } }))
                      }
                      className="h-7 w-20 px-1.5 text-right text-[12.5px]"
                    />
                    <span className="w-3 text-[11px] text-muted-foreground">%</span>
                  </span>
                </div>
              ))}
              {i === 0 && (
                <div className="mt-0.5 text-[11px] text-muted-foreground">
                  {years[0].assumptions.find((a) => a.key === "revenue_growth")?.basis}
                </div>
              )}
            </div>
          ))}

          <div className="flex gap-1.5">
            <Button
              size="sm"
              variant="outline"
              disabled={busy || shown >= 5}
              onClick={() => setExtra((n) => n + 1)}
              className="h-7 flex-1 text-[12px]"
            >
              + 연차
            </Button>
            {shown > 1 && (
              <Button
                size="sm"
                variant="ghost"
                disabled={busy}
                onClick={() => setExtra((n) => Math.max(0, n - 1))}
                className="h-7 text-[12px]"
              >
                −
              </Button>
            )}
          </div>

          <Button size="sm" disabled={busy} onClick={apply} className="mt-2 w-full">
            {busy ? "다시 계산 중…" : "가정 적용해 다시 계산"}
          </Button>
          <Hint>
            <b>기계는 첫 해만 세웁니다.</b> 2년차부터는 여기 넣은 가정으로만 갑니다 —
            직전 해 추정 위에 쌓입니다. 숫자가 바뀌므로 문장은 결정론으로 다시
            만들어지고 버전이 오릅니다.
          </Hint>
          {error && <Hint>{error}</Hint>}
        </CardContent>
      </Card>
    </RailSection>
  );
}
