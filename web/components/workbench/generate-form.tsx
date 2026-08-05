"use client";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { CompanySearch } from "@/components/workbench/company-search";
import { Hint, SectionLabel } from "@/components/workbench/section-label";
import { StageRail } from "@/components/workbench/stage-rail";
import type { ViewModel } from "@/lib/api";
import type { Step } from "@/lib/use-generation";
import { hasPeriodicInfo, periodKey, type PeriodCode } from "@/lib/periods";
import type { Filing, Preliminary } from "@/lib/api";

export interface FormState {
  symbol: string;
  year: number;
  /** 어느 정기보고서인가. 엔진은 진작 이 축을 알고 있었다(PeriodType). */
  period: PeriodCode;
  llm: boolean;
  assume: string;
}

/**
 * 왼쪽 열 — 조작.
 *
 * **생성과 발간은 다르다.** 생성은 미리보기라 이력에 남지 않고, 발간해야
 * 추정이 스냅샷으로 저장돼 다음 발간의 변화 추적 기준이 된다. 버튼을 둘로
 * 나눈 이유이고, 아래 안내문이 그 차이를 설명한다 (`app.py::_generate`).
 */
export function GenerateForm({
  state,
  onChange,
  onSubmit,
  busy,
  steps,
  elapsed,
  vm,
  filings,
  preliminary,
  collapsed = false,
  onExpand,
}: {
  state: FormState;
  onChange: (s: FormState) => void;
  onSubmit: (publish: boolean) => void;
  busy: boolean;
  steps: Step[];
  elapsed: number;
  vm: ViewModel | null;
  /** DART가 준 실제 정기보고서 목록. 회사를 고르기 전에는 비어 있다. */
  filings: Filing[];
  preliminary: Preliminary | null;
  /** 카드를 보는 중에는 접는다 — 생성 폼은 보드에서만 필요하다. */
  collapsed?: boolean;
  onExpand?: () => void;
}) {
  const set = <K extends keyof FormState>(k: K, v: FormState[K]) =>
    onChange({ ...state, [k]: v });

  if (collapsed) {
    return (
      <div className="space-y-6">
        <Button variant="outline" size="sm" onClick={onExpand} className="w-full">
          + 새 초안
        </Button>
        <StageRail stages={vm?.stages ?? []} steps={steps} running={busy} elapsed={elapsed} />
      </div>
    );
  }

  return (
    <div className="space-y-8">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          onSubmit(false);
        }}
      >
        <SectionLabel>새 초안</SectionLabel>

        <Label htmlFor="symbol" className="text-xs text-muted-foreground">
          회사명 또는 종목코드
        </Label>
        <div className="mt-1">
          <CompanySearch
            value={state.symbol}
            onChange={(v) => set("symbol", v)}
            disabled={busy}
          />
        </div>

        <Label htmlFor="report" className="text-xs text-muted-foreground mt-5 block">
          정기보고서
        </Label>
        {/* **계산이 아니라 DART가 준 목록이다.** 기한으로 추측하면 결산월이
            다른 회사·정정신고·미제출을 전부 틀린다. */}
        <select
          id="report"
          value={periodKey(state)}
          disabled={busy || filings.length === 0}
          onChange={(e) => {
            const [y, p] = e.target.value.split(":");
            onChange({ ...state, year: Number(y), period: p as PeriodCode });
          }}
          className="mt-1 h-9 w-full rounded-md border bg-transparent px-2.5 text-[13px] disabled:opacity-50"
        >
          {filings.length === 0 ? (
            <option>회사를 먼저 고르십시오</option>
          ) : (
            filings.map((f) => (
              <option key={f.rcept_no} value={`${f.year}:${f.period}`}>
                {f.label} · {f.filed_at} 제출
              </option>
            ))
          )}
        </select>

        {/* 더 최신 실적이 이미 나와 있으면 말해준다. 모르고 옛 보고서로 쓰는
            것과, 알고도 그걸 쓰는 것은 다르다. */}
        {preliminary && (
          <div className="mt-1.5 rounded-md border border-warn/50 bg-warn/10 px-2.5 py-1.5 text-[11.5px]">
            <b className="text-warn">{preliminary.filed_at} 잠정실적</b>이 이미
            나왔습니다. ARC는 아직 정기보고서만 읽습니다 —{" "}
            <a href={preliminary.url} target="_blank" rel="noopener" className="text-num hover:underline">
              공시 원문
            </a>
          </div>
        )}

        {!hasPeriodicInfo(state) && (
          <Hint>
            분기보고서에는 <b>주식수·배당·감사의견·인력·지분·출자</b>가 없습니다(연간
            공시). 부문 손익과 재무제표는 그대로 나옵니다.
          </Hint>
        )}

        <Label htmlFor="assume" className="text-xs text-muted-foreground mt-5 block">
          추정 가정 덮어쓰기
        </Label>
        <Textarea
          id="assume"
          rows={3}
          value={state.assume}
          onChange={(e) => set("assume", e.target.value)}
          disabled={busy}
          placeholder={"revenue_growth=15\noperating_margin=38"}
          className="mt-1 font-mono text-[12.5px]"
        />
        <Hint>
          비우면 과거 실적의 기계적 연장을 기준선으로 씁니다. 지정한 가정은 노트에{" "}
          <b>사용자 입력</b>으로 표시됩니다.
        </Hint>

        <div className="flex items-center gap-2 mt-5">
          <Checkbox
            id="llm"
            checked={state.llm}
            onCheckedChange={(c) => set("llm", c === true)}
            disabled={busy}
          />
          <Label htmlFor="llm" className="text-sm">
            LLM 서술 사용
          </Label>
        </div>
        <Hint>끄면 결정론 문장으로 생성합니다. 수치는 어느 쪽이든 동일합니다.</Hint>

        <Button type="submit" disabled={busy} className="w-full mt-6">
          {busy ? "작성 중…" : "초안 작성"}
        </Button>
        <Button
          type="button"
          variant="outline"
          disabled={busy}
          onClick={() => onSubmit(true)}
          className="w-full mt-2 hover:text-ok hover:border-ok"
        >
          검토 완료 · 발간
        </Button>
        <Hint>
          <b>초안 작성</b>은 미리보기라 이력에 남지 않습니다. <b>발간</b>해야 추정이 저장되고
          다음 발간의 변화 추적 기준이 됩니다 (D27).
        </Hint>

      </form>

      {/* 진행 표시와 단계 기록은 같은 것의 두 상태다 — 흘러가다가 그 자리에 남는다 */}
      <StageRail
        stages={vm && !vm.error ? vm.stages : []}
        steps={steps}
        running={busy}
        elapsed={elapsed}
      />

    </div>
  );
}
