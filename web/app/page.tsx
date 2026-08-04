"use client";

import { useCallback, useState } from "react";

import { NoteBody, type Heading } from "@/components/note/note-body";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { EvidenceRail } from "@/components/workbench/evidence-rail";
import { GenerateForm, type FormState } from "@/components/workbench/generate-form";
import { Hint } from "@/components/workbench/section-label";
import { useGeneration, type Phase } from "@/lib/use-generation";
import type { ViewModel } from "@/lib/api";

/**
 * 실적 리뷰 노트 작업대 — 검토자가 발간 여부를 판단하는 화면.
 *
 * 구성 원칙은 이전 화면(index.html:3-4)에서 그대로 가져왔다:
 * 왼쪽은 조작(입력·가정), 가운데는 결과(노트), 오른쪽은 근거(게이트·출처).
 * 근거를 접어두지 않는 이유는, 이 제품이 파는 것이 "글"이 아니라
 * "검증된 글"이기 때문이다.
 */
export default function Workbench() {
  const [form, setForm] = useState<FormState>({
    symbol: "",
    year: 2025,
    llm: false,
    assume: "",
  });
  const [headings, setHeadings] = useState<Heading[]>([]);
  const { phase, steps, vm, error, elapsed, run } = useGeneration();

  const busy = phase === "running";

  // NoteBody의 effect가 매 렌더마다 다시 돌지 않도록 안정된 참조를 준다
  const onHeadings = useCallback((h: Heading[]) => setHeadings(h), []);

  function submit(publish: boolean) {
    setHeadings([]);
    run({ ...form, publish });
  }

  return (
    <>
      <header className="sticky top-0 z-10 flex flex-wrap items-baseline gap-3 border-b bg-card px-8 py-5">
        <h1 className="text-[15px] font-semibold tracking-tight">AI Research Center</h1>
        <span className="text-[13px] text-muted-foreground">
          코스닥 미커버 종목 실적 리뷰 노트 · 사람이 검토 후 발간
        </span>
        {vm?.company && (
          <span className="text-[13px] text-muted-foreground">
            — {vm.company} ({vm.symbol}) · {vm.market} · FY{vm.year} · {vm.basis}재무제표
          </span>
        )}
      </header>

      <div className="grid items-start xl:grid-cols-[320px_minmax(0,1fr)_360px]">
        <div className="border-b px-7 py-8 xl:sticky xl:top-(--header-h) xl:border-r xl:border-b-0">
          <GenerateForm
            state={form}
            onChange={setForm}
            onSubmit={submit}
            busy={busy}
            steps={steps}
            elapsed={elapsed}
            vm={vm}
          />
        </div>

        <div className="px-8 py-8">
          <CenterColumn vm={vm} error={error} phase={phase} onHeadings={onHeadings} />
        </div>

        <div className="border-t px-7 py-8 xl:sticky xl:top-(--header-h) xl:max-h-[calc(100vh-var(--header-h))] xl:overflow-y-auto xl:border-t-0 xl:border-l">
          {vm && !vm.error && <EvidenceRail vm={vm} headings={headings} />}
        </div>
      </div>
    </>
  );
}

function CenterColumn({
  vm,
  error,
  phase,
  onHeadings,
}: {
  vm: ViewModel | null;
  error: string;
  phase: Phase;
  onHeadings: (h: Heading[]) => void;
}) {
  if (error) {
    return (
      <Alert variant="destructive" className="max-w-[860px]">
        <AlertTitle>생성 실패</AlertTitle>
        <AlertDescription>{error}</AlertDescription>
      </Alert>
    );
  }

  if (!vm) {
    if (phase === "running") {
      return (
        <p className="py-10 text-center text-[13px] text-muted-foreground">
          공시를 읽고 있습니다. 왼쪽에 진행 상황이 표시됩니다.
        </p>
      );
    }
    return (
      <p className="py-10 text-center text-[13px] text-muted-foreground">
        종목코드를 입력하면 DART 공시에서 실적 리뷰 노트를 생성합니다.
        <br />
        생성된 <span className="num cursor-default">모든 수치</span>는 클릭하면 출처와 산식을
        보여줍니다.
      </p>
    );
  }

  return (
    <div className="space-y-4">
      {vm.notice && (
        <Card className="max-w-[860px] border-warn">
          <CardContent className="py-4">
            <Badge className="bg-warn/15 text-warn border-transparent">● 안내</Badge>
            <Hint>{vm.notice}</Hint>
          </CardContent>
        </Card>
      )}

      {vm.published_path && (
        <Card className="max-w-[860px] border-ok">
          <CardContent className="py-4">
            <Badge className="bg-ok/15 text-ok border-transparent">● 발간 완료</Badge>
            <Hint>
              {vm.published_path}
              <br />
              추정이 이력에 저장됐습니다. 다음 발간에서 이 값 대비 변화가 표시됩니다.
            </Hint>
          </CardContent>
        </Card>
      )}

      {vm.error ? (
        <Alert variant="destructive" className="max-w-[860px]">
          <AlertTitle>생성 실패</AlertTitle>
          <AlertDescription>{vm.error}</AlertDescription>
        </Alert>
      ) : vm.gate_passed ? (
        <>
          {(vm.segment_chart || vm.trend_chart) && (
            <div className="note">
              {vm.segment_chart && (
                <>
                  <h2 className="!mt-0 !border-0 !pt-0">부문 구성</h2>
                  <div
                    className="chart"
                    dangerouslySetInnerHTML={{
                      __html: vm.segment_chart + vm.segment_legend,
                    }}
                  />
                </>
              )}
              {vm.trend_chart && (
                <>
                  <h2>3개년 추이</h2>
                  <div
                    className="chart"
                    dangerouslySetInnerHTML={{ __html: vm.trend_chart + vm.trend_legend }}
                  />
                  <Hint>정확한 수치는 아래 표에 있습니다. 차트는 크기 비교용입니다.</Hint>
                </>
              )}
            </div>
          )}
          <NoteBody html={vm.body_html} onHeadings={onHeadings} />
        </>
      ) : (
        // 차단된 초안은 **보여주지 않는다** — 검토자가 결과로 착각한다
        // (`app.py::_to_view`의 같은 판단).
        <Alert variant="destructive" className="max-w-[860px]">
          <AlertTitle>G0 게이트 차단 — 발간할 수 없습니다.</AlertTitle>
          <AlertDescription>
            {vm.gate_summary}
            <br />
            <br />
            차단된 초안은 표시하지 않습니다. 검토자가 결과로 착각할 수 있기 때문입니다. 오른쪽
            위반 내역을 확인하세요.
          </AlertDescription>
        </Alert>
      )}
    </div>
  );
}
