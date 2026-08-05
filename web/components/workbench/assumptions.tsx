"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { RailSection } from "@/components/workbench/rail-section";
import { Hint } from "@/components/workbench/section-label";
import { recompute, type Assumption } from "@/lib/api";

/**
 * 추정 가정 — **이 제품에서 사람의 판단이 들어가는 유일한 구멍** (D24).
 *
 * 한때 초안 작성 폼에 있었는데 틀린 자리였다. 성장률은 과거를 보기 전에 정할
 * 수 없다 — 「최근 2개년 평균이 12.3%였다」를 모르는 상태에서 15를 넣으라는
 * 것이었다. 틀린 것을 물어서가 아니라 **틀린 때에 물어서** 어색했다.
 *
 * 그래서 카드에 있다. 기준선과 그 근거를 보여준 다음에 고친다.
 */
export function Assumptions({
  cardId,
  assumptions,
  onRecomputed,
}: {
  cardId: string;
  assumptions: Assumption[];
  onRecomputed: () => void;
}) {
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  if (assumptions.length === 0) {
    return (
      <RailSection title="추정 가정" count={0}>
        <Hint>이 종목은 기준선을 세울 과거 실적이 부족합니다.</Hint>
      </RailSection>
    );
  }

  const dirty = Object.entries(draft).some(([k, v]) => {
    const base = assumptions.find((a) => a.key === k);
    return base !== undefined && v.trim() !== "" && Number(v) !== base.value;
  });

  async function apply() {
    setBusy(true);
    setError("");
    const payload: Record<string, number> = {};
    for (const a of assumptions) {
      const v = draft[a.key];
      payload[a.key] = v !== undefined && v.trim() !== "" ? Number(v) : a.value;
    }
    const r = await recompute(cardId, payload);
    setBusy(false);
    if ("error" in r) {
      setError(r.error);
      return;
    }
    setDraft({});
    onRecomputed();
  }

  return (
    <RailSection title="추정 가정" count={assumptions.length} defaultOpen>
      <Card>
        <CardContent className="py-3">
          {assumptions.map((a) => (
            <div key={a.key} className="mb-2.5">
              <div className="flex items-center justify-between gap-2">
                <span className="min-w-0 truncate text-[12.5px]">{a.label}</span>
                <span className="flex flex-none items-center gap-1">
                  <Input
                    type="number"
                    step="0.1"
                    value={draft[a.key] ?? String(a.value)}
                    disabled={busy}
                    onChange={(e) => setDraft((d) => ({ ...d, [a.key]: e.target.value }))}
                    className="h-7 w-20 px-1.5 text-right text-[12.5px]"
                  />
                  <span className="w-4 text-[11.5px] text-muted-foreground">{a.unit}</span>
                </span>
              </div>
              {/* **근거를 함께 보여준다.** 무엇을 근거로 이 값이 나왔는지 모르면
                  고칠지 말지를 판단할 수 없다. */}
              <div className="text-[11px] text-muted-foreground">
                {a.override ? "사용자 지정" : a.basis}
              </div>
            </div>
          ))}

          <Button size="sm" disabled={busy || !dirty} onClick={apply} className="mt-1 w-full">
            {busy ? "다시 계산 중…" : "가정 바꿔 다시 계산"}
          </Button>
          <Hint>
            숫자가 바뀌므로 <b>문장은 결정론으로 다시 만들어집니다</b> — 옛 문장이 새
            숫자를 설명한다고 둘 수 없습니다. 버전이 오릅니다.
          </Hint>
          {error && <Hint>{error}</Hint>}
        </CardContent>
      </Card>
    </RailSection>
  );
}
