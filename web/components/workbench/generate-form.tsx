"use client";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { CompanySearch } from "@/components/workbench/company-search";
import { Hint, KeyValue, SectionLabel } from "@/components/workbench/section-label";
import { StageRail } from "@/components/workbench/stage-rail";
import type { ViewModel } from "@/lib/api";
import type { Step } from "@/lib/use-generation";

export interface FormState {
  symbol: string;
  year: number;
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
}: {
  state: FormState;
  onChange: (s: FormState) => void;
  onSubmit: (publish: boolean) => void;
  busy: boolean;
  steps: Step[];
  elapsed: number;
  vm: ViewModel | null;
}) {
  const set = <K extends keyof FormState>(k: K, v: FormState[K]) =>
    onChange({ ...state, [k]: v });

  return (
    <div className="space-y-8">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          onSubmit(false);
        }}
      >
        <SectionLabel>생성</SectionLabel>

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

        <Label htmlFor="year" className="text-xs text-muted-foreground mt-5 block">
          사업연도
        </Label>
        <Input
          id="year"
          type="number"
          min={2015}
          max={2030}
          value={state.year}
          onChange={(e) => set("year", Number(e.target.value))}
          disabled={busy}
          className="mt-1"
        />

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
          {busy ? "생성 중…" : "노트 생성"}
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
          <b>생성</b>은 미리보기라 이력에 남지 않습니다. <b>발간</b>해야 추정이 저장되고 다음
          발간의 변화 추적 기준이 됩니다.
        </Hint>

      </form>

      {/* 진행 표시와 단계 기록은 같은 것의 두 상태다 — 흘러가다가 그 자리에 남는다 */}
      <StageRail
        stages={vm && !vm.error ? vm.stages : []}
        steps={steps}
        running={busy}
        elapsed={elapsed}
      />

      {vm && !vm.error && (
        <section>
          <SectionLabel>커버리지</SectionLabel>
          <Card>
            <CardContent className="py-4">
              <KeyValue label="매핑된 지표">{vm.metrics_found}개</KeyValue>
              <KeyValue label="레지스트리">{vm.registry_size}건</KeyValue>
              {vm.metrics_missing.length > 0 && (
                <KeyValue label="미확인 계정">{vm.metrics_missing.join(", ")}</KeyValue>
              )}
              {vm.llm_used ? (
                <>
                  <KeyValue label="서술">{vm.llm_model}</KeyValue>
                  {vm.llm_cost != null && (
                    <KeyValue label="건당 비용">${vm.llm_cost.toFixed(4)}</KeyValue>
                  )}
                </>
              ) : (
                <KeyValue label="서술">결정론 문장</KeyValue>
              )}
            </CardContent>
          </Card>
        </section>
      )}
    </div>
  );
}
