"use client";

import { useCallback, useEffect, useState } from "react";

import { Board, BoardHint } from "@/components/board/board";
import { NoteBody, type Heading } from "@/components/note/note-body";
import { RevisePanel } from "@/components/note/revise-panel";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { EvidenceRail } from "@/components/workbench/evidence-rail";
import { GenerateForm, type FormState } from "@/components/workbench/generate-form";
import { Hint } from "@/components/workbench/section-label";
import { ThemeToggle } from "@/components/workbench/theme-toggle";
import { useGeneration } from "@/lib/use-generation";
import {
  confirmCard,
  deleteCard,
  getCard,
  listCards,
  type CardDetail,
  type CardSummary,
  type ViewModel,
} from "@/lib/api";

/**
 * 작업대 — 보드가 홈이고, 카드를 열면 그 리포트가 나온다.
 *
 * 구성 원칙은 이전 화면에서 그대로 가져왔다: 왼쪽은 조작, 가운데는 결과,
 * 오른쪽은 근거. 근거를 접어두지 않는 이유는 이 제품이 파는 것이 "글"이 아니라
 * "검증된 글"이기 때문이다.
 *
 * 달라진 것은 **가운데가 하나의 리포트가 아니라 보드**라는 점이다. 생성은
 * 화면을 붙들지 않고 카드를 만들고, 사람은 기다리는 대신 다른 카드를 본다.
 */
export default function Workbench() {
  const [form, setForm] = useState<FormState>({ symbol: "", year: 2025, llm: false, assume: "" });
  const [headings, setHeadings] = useState<Heading[]>([]);
  const [cards, setCards] = useState<CardSummary[]>([]);
  const [open, setOpen] = useState<CardDetail | null>(null);
  const { phase, steps, vm, error, elapsed, run } = useGeneration();

  const busy = phase === "running";
  const onHeadings = useCallback((h: Heading[]) => setHeadings(h), []);

  // 보드를 주기적으로 읽는다. 생성이 백그라운드에서 끝나므로 콜백을 엮는 것보다
  // 짧은 폴링이 단순하고 튼튼하다 — 목록은 본문을 빼고 오므로 가볍다.
  useEffect(() => {
    let alive = true;
    const tick = async () => {
      const next = await listCards();
      if (alive) setCards(next);
    };
    void tick();
    const t = setInterval(tick, 3000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  function submit(publish: boolean) {
    setHeadings([]);
    setOpen(null);
    run({ ...form, publish });
  }

  async function openCard(id: string) {
    setHeadings([]);
    setOpen(await getCard(id));
  }

  async function confirm(id: string) {
    await confirmCard(id);
    setCards(await listCards());
  }

  async function remove(id: string) {
    await deleteCard(id);
    setOpen((cur) => (cur?.id === id ? null : cur));
    setCards(await listCards());
  }

  // 열린 카드가 있으면 그것을, 없으면 방금 생성한 것을 본다
  const shown: ViewModel | null = open ? open.vm : vm;

  return (
    <>
      <header className="sticky top-0 z-10 flex flex-wrap items-baseline gap-3 border-b bg-card px-8 py-5">
        <button
          type="button"
          onClick={() => setOpen(null)}
          className="text-[15px] font-semibold tracking-tight hover:text-num"
        >
          AI Research Center
        </button>
        <span className="text-[13px] text-muted-foreground">
          코스닥 미커버 종목 실적 리뷰 노트 · 사람이 검토 후 발간
        </span>
        {open && (
          <span className="text-[13px] text-muted-foreground">
            — {open.company || open.symbol} ({open.symbol}) · FY{open.year} ·{" "}
            <span className="font-mono">{open.version}</span>
          </span>
        )}
        <div className="ml-auto">
          <ThemeToggle />
        </div>
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
            vm={shown}
          />
        </div>

        <div className="px-8 py-8">
          {open ? (
            <>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setOpen(null)}
                className="mb-3 -ml-2 h-7 text-[12px] text-muted-foreground"
              >
                ← 보드로
              </Button>
              {open.vm.gate_passed && (
                <div className="mb-4">
                  <RevisePanel
                    cardId={open.id}
                    version={open.version}
                    onAccepted={() => void openCard(open.id)}
                  />
                </div>
              )}
              <CenterColumn vm={open.vm} error="" onHeadings={onHeadings} />
            </>
          ) : error ? (
            <Alert variant="destructive" className="max-w-[860px]">
              <AlertTitle>생성 실패</AlertTitle>
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : (
            <>
              <Board cards={cards} onOpen={openCard} onConfirm={confirm} onDelete={remove} />
              <BoardHint />
            </>
          )}
        </div>

        <div className="border-t px-7 py-8 xl:sticky xl:top-(--header-h) xl:max-h-[calc(100vh-var(--header-h))] xl:overflow-y-auto xl:border-t-0 xl:border-l">
          {open && !open.vm.error && <EvidenceRail vm={open.vm} headings={headings} />}
        </div>
      </div>
    </>
  );
}

function CenterColumn({
  vm,
  error,
  onHeadings,
}: {
  vm: ViewModel;
  error: string;
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
                    dangerouslySetInnerHTML={{ __html: vm.segment_chart + vm.segment_legend }}
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
