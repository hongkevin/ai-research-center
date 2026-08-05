"use client";

import { useCallback, useEffect, useState } from "react";

import { cn } from "@/lib/utils";

import { Board, BoardHint } from "@/components/board/board";
import { NoteBody, type Heading } from "@/components/note/note-body";
import { SectionEditor } from "@/components/note/section-editor";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { EvidenceRail } from "@/components/workbench/evidence-rail";
import { symbolOf } from "@/components/workbench/company-search";
import { GenerateForm, type FormState } from "@/components/workbench/generate-form";
import { Hint } from "@/components/workbench/section-label";
import { Brand, BRAND_LINE } from "@/components/workbench/brand";
import { ThemeToggle } from "@/components/workbench/theme-toggle";
import { authEnabled, signOut, supabase } from "@/lib/supabase";
import { useGeneration } from "@/lib/use-generation";
import {
  confirmCard,
  deleteCard,
  getCard,
  listCards,
  listSections,
  type DocSection,
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
  const [sections, setSections] = useState<DocSection[]>([]);
  const [editing, setEditing] = useState<string | null>(null);
  const { phase, steps, vm, error, elapsed, run } = useGeneration();

  const busy = phase === "running";
  const [signedIn, setSignedIn] = useState<boolean | null>(authEnabled ? null : true);

  // 로그인이 켜져 있으면 세션이 있어야 화면을 연다. 껍데기는 공개지만
  // 데이터는 전부 `/api/*` 뒤에 있어서, 세션 없이는 빈 보드만 보인다.
  useEffect(() => {
    if (!authEnabled) return;
    const client = supabase();
    void client.auth.getSession().then(({ data }) => {
      if (data.session) setSignedIn(true);
      else location.replace(`/login/?next=${encodeURIComponent(location.pathname)}`);
    });
    const { data: sub } = client.auth.onAuthStateChange((_e, session) => {
      setSignedIn(Boolean(session));
    });
    return () => sub.subscription.unsubscribe();
  }, []);
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
    run({ ...form, symbol: symbolOf(form.symbol), publish });
  }

  // 편집기를 열면 그 섹션을 화면 위쪽으로 끌어온다. 시트가 아래 절반을
  // 차지하므로, 가만두면 고치는 대상이 시트 뒤에 있을 수 있다.
  function startEditing(title: string | null) {
    setEditing(title);
    if (!title) return;
    requestAnimationFrame(() => {
      const h = [...document.querySelectorAll(".note h2")].find((el) =>
        (el.textContent ?? "").trim().startsWith(title),
      );
      h?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }

  async function openCard(id: string) {
    setHeadings([]);
    setOpen(await getCard(id));
    try {
      setSections((await listSections(id)).sections);
    } catch {
      setSections([]);
    }
  }

  const editable = sections.filter((s) => s.editable);
  const editingSection = editable.find((s) => s.title === editing) ?? null;

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

  // 세션을 확인하는 동안은 아무것도 그리지 않는다 — 빈 보드가 잠깐 보이면
  // 카드가 사라진 것처럼 읽힌다.
  if (signedIn === null) return null;

  return (
    <>
      <header className="sticky top-0 z-10 flex flex-wrap items-baseline gap-3 border-b bg-card px-8 py-5">
        <button type="button" onClick={() => setOpen(null)} className="shrink-0">
          <Brand className="text-[15px]" />
        </button>

        {/* 카드를 열면 설명 자리를 종목 사실이 가져간다 — 둘이 같은 줄에서
            경쟁하면 헤더가 다시 뚱뚱해진다. 「연결/별도」는 이 바닥에서 먼저
            확인하는 것이라 반드시 남긴다(React 이관 때 빠뜨렸던 자리다). */}
        {open ? (
          <span className="truncate text-[13px]">
            <span className="font-medium">{open.company || open.symbol}</span>
            <span className="text-muted-foreground">
              {" "}
              ({open.symbol}) · {open.vm.market} · FY{open.year} · {open.vm.basis} ·{" "}
              <span className="font-mono">{open.version}</span>
            </span>
          </span>
        ) : (
          <span className="text-[13px] text-muted-foreground">{BRAND_LINE}</span>
        )}
        <div className="ml-auto flex items-center gap-1">
          <ThemeToggle />
          {authEnabled && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => void signOut().then(() => location.replace("/login/"))}
              className="h-7 text-[12px] text-muted-foreground"
            >
              로그아웃
            </Button>
          )}
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
            collapsed={open !== null}
            onExpand={() => setOpen(null)}
          />
        </div>

        <div className={cn("px-8 py-8", editing && "pb-[calc(50dvh+2rem)]")}>
          {open ? (
            <>
              <div className="mb-3 flex items-center gap-2">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setOpen(null)}
                  className="-ml-2 h-7 text-[12px] text-muted-foreground"
                >
                  ← 보드로
                </Button>
                {editable.length > 0 && (
                  <Button
                    size="sm"
                    variant={editing ? "secondary" : "default"}
                    onClick={() => startEditing(editing ? null : editable[0].title)}
                    className="h-7 text-[12px]"
                  >
                    {editing ? "편집기 닫기" : "문서 수정"}
                  </Button>
                )}
                <span className="text-[11.5px] text-muted-foreground">
                  제목 옆 「수정」으로도 열 수 있습니다
                </span>
              </div>
              {open.vm.gate_passed && editable.length === 0 && (
                <Alert className="mb-4 max-w-[860px] border-warn">
                  <AlertTitle>이 카드는 편집할 수 없습니다</AlertTitle>
                  <AlertDescription>
                    편집기가 붙기 전에 만들어진 카드라 <b>원문이 저장돼 있지 않습니다.</b>{" "}
                    수정하려면 같은 종목으로 다시 생성해 주십시오 — 새로 만든 카드에는
                    제목 옆에 「수정」이 나타납니다.
                  </AlertDescription>
                </Alert>
              )}
              <CenterColumn
                vm={open.vm}
                error=""
                onHeadings={onHeadings}
                onEditSection={startEditing}
                editableSections={editable.map((s) => s.title)}
              />
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
          {open && !open.vm.error && (
            <EvidenceRail vm={open.vm} headings={headings} versions={open.versions} />
          )}
        </div>
      </div>

      {open && editingSection && (
        <SectionEditor
          key={editingSection.title}
          cardId={open.id}
          version={open.version}
          section={editingSection}
          sections={editable}
          onPick={startEditing}
          onClose={() => setEditing(null)}
          onSaved={() => void openCard(open.id)}
        />
      )}
    </>
  );
}

function CenterColumn({
  vm,
  error,
  onHeadings,
  onEditSection,
  editableSections,
}: {
  vm: ViewModel;
  error: string;
  onHeadings: (h: Heading[]) => void;
  onEditSection?: (title: string) => void;
  editableSections?: string[];
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
          <NoteBody
            html={vm.body_html}
            onHeadings={onHeadings}
            onEditSection={onEditSection}
            editableSections={editableSections}
          />
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
