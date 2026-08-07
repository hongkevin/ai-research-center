"use client";

import { useCallback, useEffect, useState } from "react";
import { PenLineIcon } from "lucide-react";

import { cn } from "@/lib/utils";

import { AskWidget } from "@/components/ask/ask-widget";
import { MorningBrief } from "@/components/brief/morning";
import { Senti } from "@/components/senti/senti";
import { Board, BoardHint } from "@/components/board/board";
import { Coverage } from "@/components/profile/coverage";
import { PeerCompose } from "@/components/peer/peer-compose";
import { PeerEdit } from "@/components/peer/peer-edit";
import { PeerTable } from "@/components/peer/peer-table";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { StageRail } from "@/components/workbench/stage-rail";
import { StartChoice } from "@/components/workbench/start-choice";
import { UploadConfirm } from "@/components/workbench/upload-confirm";
import { NoteBody, type Heading } from "@/components/note/note-body";
import { SectionEditor } from "@/components/note/section-editor";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { EvidenceRail } from "@/components/workbench/evidence-rail";
import { symbolOf } from "@/components/workbench/company-search";

import {
  GenerateForm,
  type FormState,
} from "@/components/workbench/generate-form";
import { Hint } from "@/components/workbench/section-label";
import { Brand, BRAND_LINE } from "@/components/workbench/brand";
import { ThemeToggle } from "@/components/workbench/theme-toggle";
import { authEnabled, signOut, supabase } from "@/lib/supabase";
import { useGeneration } from "@/lib/use-generation";
import {
  confirmCard,
  deleteCard,
  downloadUrl,
  getCard,
  listCards,
  getCapabilities,
  getFilings,
  listSections,
  publishCard,
  type DocSection,
  type Filing,
  type Preliminary,
  type CardDetail,
  type Converted,
  type CardSummary,
  type Moves,
  type ViewModel,
  fmtPct,
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
  const [form, setForm] = useState<FormState>({
    symbol: "",
    year: 0,
    period: "ANNUAL",
    llm: false,
    search: false,
    prior_markdown: "",
    prior_name: "",
    assume: "",
  });
  // 서버에 기사 검색 키가 있는가. 없으면 체크박스가 이유를 적고 잠긴다.
  const [newsAvailable, setNewsAvailable] = useState(false);
  // 생성은 상주 패널이 아니라 행동이다 (D49).
  // 세 걸음: 어디서 시작할지 → (올렸으면) 확인 → 폼 (D50).
  const [composing, setComposing] = useState(false);
  const [step, setStep] = useState<"choose" | "confirm" | "form">("choose");
  const [uploaded, setUploaded] = useState<Converted | null>(null);
  // 지금 돌고 있는 카드. 진행 표시를 그 카드에만 붙인다.
  const [runningId, setRunningId] = useState("");
  const [filings, setFilings] = useState<Filing[]>([]);
  const [loadingFilings, setLoadingFilings] = useState(false);
  const [preliminary, setPreliminary] = useState<Preliminary | null>(null);
  const [headings, setHeadings] = useState<Heading[]>([]);
  const [cards, setCards] = useState<CardSummary[]>([]);
  const [open, setOpen] = useState<CardDetail | null>(null);
  // 보드와 채팅. **카드를 여는 것은 탭이 아니라 보드 안의 행동이다.**
  // **브리프가 첫 화면이다.** RA의 하루는 「어젯밤 사이 뭐가 있었나」로
  // 시작한다 — 보드는 그다음에 여는 것이다.
  // **순서가 일의 순서다.** 그리고 선은 「흐르는 것 vs 쌓이는 것」에 있다 —
  // 브리프·센티는 하루 지나면 버려지고, 커버리지·피어·리포트는 남는다.
  //
  //   me    내가 무엇을 보는가          (설정)
  //   brief 어젯밤 무엇이 달라졌나       (시간축)
  //   senti 지금 무슨 말이 도는가        (시간축·미검증)
  //   peer  이 종목이 동종 대비 어디인가  (횡단면축)
  //   board 이 종목을 어떻게 쓸 것인가    (깊이축)
  const [tab, setTab] = useState<
    "me" | "brief" | "senti" | "peer" | "board"
  >("brief");
  // 피어 그룹 만들기. 종목 리포트와 다른 흐름이라 다이얼로그가 따로다.
  const [composingPeer, setComposingPeer] = useState(false);
  // 피어 그룹 고치기 — 이름과 구성원. 한 번 만들고 끝나는 것이 아니다.
  const [editingPeer, setEditingPeer] = useState(false);
  const [sections, setSections] = useState<DocSection[]>([]);
  const [editing, setEditing] = useState<string | null>(null);
  const { phase, steps, error, elapsed, run } = useGeneration();

  const busy = phase === "running";
  const [signedIn, setSignedIn] = useState<boolean | null>(
    authEnabled ? null : true,
  );

  // 로그인이 켜져 있으면 세션이 있어야 화면을 연다. 껍데기는 공개지만
  // 데이터는 전부 `/api/*` 뒤에 있어서, 세션 없이는 빈 보드만 보인다.
  useEffect(() => {
    if (!authEnabled) return;
    const client = supabase();
    void client.auth.getSession().then(({ data }) => {
      if (data.session) setSignedIn(true);
      else
        location.replace(
          `/login/?next=${encodeURIComponent(location.pathname)}`,
        );
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

  // 회사가 확정되면 **DART에 뭐가 올라와 있는지** 물어 목록을 채우고 최신을
  // 기본값으로 잡는다. 기한으로 계산하던 추측을 걷어낸 자리다.
  useEffect(() => {
    const code = symbolOf(form.symbol);
    const valid = /^\d{6}$/.test(code);
    let alive = true;
    void (async () => {
      // effect 본문에서 바로 setState하면 연쇄 렌더가 생긴다. 두 갈래 모두
      // 마이크로태스크 뒤로 넘긴다.
      await Promise.resolve();
      if (!alive) return;
      setLoadingFilings(valid);
      const d = valid
        ? await getFilings(code)
        : { periodic: [], preliminary: [] };
      if (!alive) return;
      setLoadingFilings(false);
      setFilings(d.periodic);
      setPreliminary(d.preliminary[0] ?? null);
      const top = d.periodic[0];
      if (top) {
        setForm((f) => ({
          ...f,
          year: top.year,
          period: top.period as FormState["period"],
        }));
      }
    })();
    return () => {
      alive = false;
    };
  }, [form.symbol]);

  useEffect(() => {
    void getCapabilities().then((c) => setNewsAvailable(c.news_key));
  }, []);

  // 생성이 끝나면 열려 있는 그 카드를 다시 읽는다 — 레일 자리에 본문이 온다.
  useEffect(() => {
    if (phase === "running" || !runningId) return;
    const id = runningId;
    // **effect 본문에서 바로 setState하면 연쇄 렌더가 난다.** 비동기 뒤로 넘긴다
    // (같은 규칙을 이 파일에서 이미 두 번 밟았다).
    void (async () => {
      await openCard(id);
      setRunningId("");
    })();
  }, [phase, runningId]);

  // **생성 중인 카드는 `vm`이 빈 객체다.** 그걸 모르고 근거 패널을 그리면
  // `vm.changes.length`에서 터져 화면 전체가 죽는다 (D49에서 카드를 바로 열게
  // 만들면서 낸 회귀 — 브라우저가 "This page couldn't load"를 띄웠다).
  const ready = !!open && Object.keys(open.vm ?? {}).length > 0;
  // 탭이 갈렸으니 목록도 갈린다 — 피어는 횡단면, 리포트는 깊이다.
  const singleCards = cards.filter((c) => c.kind !== "peer");
  const peerCards = cards.filter((c) => c.kind === "peer");

  function openCompose() {
    setStep("choose");
    setUploaded(null);
    setComposing(true);
  }

  function closeCompose(open: boolean) {
    setComposing(open);
    if (!open) setStep("choose");
  }

  function submit(publish: boolean) {
    setHeadings([]);
    setOpen(null);
    // **폼을 닫는다.** 카드가 보드에 바로 나타나므로 사람은 기다리지 않고
    // 다른 카드를 본다 (D40이 노린 것인데 폼이 화면을 붙들고 있었다).
    setComposing(false);
    setRunningId("");
    void run(
      { ...form, symbol: symbolOf(form.symbol), publish },
      (id) => {
        setRunningId(id);
        // **그 카드를 바로 연다.** 안 그러면 생성 30초 동안 단계 레일이 어디에도
        // 없다 — 보드에는 「생성 중…」 한 줄뿐이다.
        if (id) void openCard(id);
      },
      // 같은 보고서가 이미 있으면 새로 만들지 않고 그 카드를 연다.
      (cardId) => {
        setComposing(false);
        void openCard(cardId);
      },
    );
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

  const [publishing, setPublishing] = useState<string>("");

  async function publish(id: string) {
    setPublishing("");
    const r = await publishCard(id);
    setPublishing(
      "error" in r ? r.error : `발간했습니다 — ${r.published_path}`,
    );
    await openCard(id);
    setCards(await listCards());
  }

  async function remove(id: string) {
    await deleteCard(id);
    setOpen((cur) => (cur?.id === id ? null : cur));
    setCards(await listCards());
  }

  // 세션을 확인하는 동안은 아무것도 그리지 않는다 — 빈 보드가 잠깐 보이면
  // 카드가 사라진 것처럼 읽힌다.
  if (signedIn === null) return null;

  return (
    <>
      <header className="sticky top-0 z-10 flex flex-wrap items-baseline gap-3 border-b bg-card px-8 py-5">
        <button
          type="button"
          onClick={() => setOpen(null)}
          className="shrink-0"
        >
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
              ({open.symbol}) · {open.vm.market} · FY{open.year} ·{" "}
              {open.vm.basis} ·{" "}
              <span className="font-mono">{open.version}</span>
            </span>
          </span>
        ) : (
          <span className="text-[13px] text-muted-foreground">
            {BRAND_LINE}
          </span>
        )}
        {/* **채팅은 보드 옆 탭이다.** 리포트 작성과 무관하게 도는 일이라
            (하루 10~15건의 클라이언트 리퀘스트) 카드 안에 두면 갈 곳이 없다.
            카드가 열려 있어도 탭을 누르면 그쪽으로 간다. */}
        {/* **순서가 일의 순서다** — 커버리지를 정하고, 매일 브리프를 보고,
            일이 있으면 보드에서 굴린다. 처음 여는 곳은 브리프다(매일 오는
            곳이라서). 커버 종목이 없으면 브리프가 커버리지로 보낸다. */}
        <nav className="-mb-5 flex items-end gap-5 self-end">
          {(
            [
              ["me", "커버리지"],
              ["brief", "모닝 브리프"],
              ["senti", "시장 센티"],
              ["peer", "피어그룹"],
              ["board", "리포트"],
            ] as const
          ).map(([key, label]) => (
            <button
              key={key}
              type="button"
              onClick={() => {
                setTab(key);
                if (key !== "board") setOpen(null);
              }}
              className={cn(
                "border-b-2 pb-2 text-[13px] transition-colors",
                tab === key
                  ? "border-foreground font-medium text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground",
              )}
            >
              {label}
              {key === "board" && singleCards.length > 0 && (
                <span className="ml-1.5 font-mono text-[11px] text-muted-foreground">
                  {singleCards.length}
                </span>
              )}
              {key === "peer" && peerCards.length > 0 && (
                <span className="ml-1.5 font-mono text-[11px] text-muted-foreground">
                  {peerCards.length}
                </span>
              )}
            </button>
          ))}
        </nav>

        <div className="ml-auto flex items-center gap-1">
          <ThemeToggle />
          {authEnabled && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() =>
                void signOut().then(() => location.replace("/login/"))
              }
              className="h-7 text-[12px] text-muted-foreground"
            >
              로그아웃
            </Button>
          )}
        </div>
      </header>

      {/* **보드가 집이다.** 폼이 왼쪽에 상주하던 것은 「입력 → 대기 → 툭」
          시절의 잔재다. 카드가 객체가 된 뒤로(D40) 생성은 상주 패널이 아니라
          **행동**이어야 한다 (D49). */}
      <Dialog open={composing} onOpenChange={closeCompose}>
        <DialogContent
          className={cn(
            "max-h-[86dvh] w-full overflow-y-auto",
            step === "choose"
              ? "max-w-[720px] sm:max-w-[720px]"
              : "max-w-[560px] sm:max-w-[560px]",
          )}
        >
          <DialogHeader>
            <DialogTitle>
              {step === "choose"
                ? "리포트 초안 작성"
                : step === "confirm"
                  ? "올린 문서를 확인하십시오"
                  : "대상과 범위"}
            </DialogTitle>
          </DialogHeader>

          {step === "choose" && (
            <StartChoice
              onContinue={(c) => {
                setUploaded(c);
                setStep("confirm");
              }}
              onFresh={() => setStep("form")}
            />
          )}

          {step === "confirm" && uploaded && (
            <UploadConfirm
              file={uploaded}
              onReject={() => {
                setUploaded(null);
                setStep("choose");
              }}
              onAccept={() => {
                // 읽어낸 종목을 폼에 채워 둔다 — 다시 치게 하면 업로드가
                // 편의가 아니라 일이 하나 는 것이다.
                const c = uploaded.company;
                setForm((f) => ({
                  ...f,
                  symbol: c ? `${c.short_name} (${c.symbol})` : f.symbol,
                  prior_markdown: uploaded.markdown,
                  prior_name: uploaded.source_name,
                }));
                setStep("form");
              }}
            />
          )}

          {step === "form" && (
            <GenerateForm
              state={form}
              onChange={setForm}
              onSubmit={submit}
              busy={busy}
              filings={filings}
              loadingFilings={loadingFilings}
              preliminary={preliminary}
              newsAvailable={newsAvailable}
            />
          )}
        </DialogContent>
      </Dialog>

      {/* **피어 그룹은 종목 리포트와 다른 물건이다.** 폼을 같이 쓰면 「종목
          하나」 전제가 섞인다 — 여기는 씨앗을 넣고 후보를 고르는 흐름이다. */}
      <Dialog open={composingPeer} onOpenChange={setComposingPeer}>
        <DialogContent className="max-h-[86dvh] w-full max-w-[640px] overflow-y-auto sm:max-w-[640px]">
          <DialogHeader>
            <DialogTitle>피어 그룹 만들기</DialogTitle>
          </DialogHeader>
          <PeerCompose
            onCreated={(id) => {
              setComposingPeer(false);
              void openCard(id);
            }}
          />
        </DialogContent>
      </Dialog>

      <div
        className={cn(
          "grid items-start",
          open && open.kind !== "peer" && "xl:grid-cols-[minmax(0,1fr)_360px]",
        )}
      >
        <div className={cn("px-8 py-8", editing && "pb-[calc(50dvh+2rem)]")}>
          {tab === "me" ? (
            <Coverage />
          ) : tab === "senti" ? (
            <Senti />
          ) : tab === "brief" ? (
            <MorningBrief onOpenCoverage={() => setTab("me")} />
          ) : open && open.kind === "peer" ? (
            /* **피어 카드는 본문이 없다.** 표가 본문이다 — 단계 레일도
               근거 패널도 붙지 않는다(그건 종목 카드의 것이다). */
            <>
              <div className="mb-3 flex items-center gap-2">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setOpen(null);
                    setEditingPeer(false);
                  }}
                  className="-ml-2 h-7 text-[12px] text-muted-foreground"
                >
                  ← 보드로
                </Button>
                <span className="text-[12px] text-muted-foreground">
                  {open.members?.length ?? 0}종목
                </span>
                <Button
                  size="sm"
                  variant={editingPeer ? "secondary" : "outline"}
                  onClick={() => setEditingPeer((v) => !v)}
                  className="h-7 text-[12px]"
                >
                  {editingPeer ? "닫기" : "그룹 수정"}
                </Button>
              </div>
              {editingPeer && (
                <div className="mb-4">
                  <PeerEdit
                    cardId={open.id}
                    name={open.company}
                    members={open.members ?? []}
                    onCancel={() => setEditingPeer(false)}
                    onDone={() => {
                      setEditingPeer(false);
                      void openCard(open.id);
                    }}
                  />
                </div>
              )}
              <PeerTable
                table={
                  open.peer_table ?? {
                    columns: [],
                    rows: [],
                    mixed_basis: false,
                    note: "",
                  }
                }
                members={open.members ?? []}
                attention={open.attention ?? []}
                moves={open.moves}
                onOpenCard={(id) => void openCard(id)}
              />
            </>
          ) : open ? (
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
                    onClick={() =>
                      startEditing(editing ? null : editable[0].title)
                    }
                    className="h-7 text-[12px]"
                  >
                    {editing ? "편집기 닫기" : "문서 수정"}
                  </Button>
                )}
                {/* **발간은 읽고 고친 뒤에 하는 일이다.** 한때 이 버튼이 초안
                    작성 폼에 있어서, 아무것도 안 만든 채로 「검토 완료」를 누를
                    수 있었다. */}
                {/* 이 제품이 내는 것이 원래 마크다운이다. 사람이 그대로
                    가져가 자기 도구에 붙일 수 있어야 한다 (D48). */}
                {/* 넘길 때 쓰는 형식으로 내려받는다 (D53). Word가 기본인
                    이유: 증권사에서 리포트가 오가는 형식이다. */}
                {ready && open.vm.gate_passed && (
                  <span className="flex items-center gap-1 text-[12px]">
                    <a
                      href={downloadUrl(open.id, "docx")}
                      className="rounded border px-2 py-1 hover:border-num/50 hover:text-num"
                    >
                      Word 내려받기
                    </a>
                    {/* **엑셀은 모델에 붙여넣기 위한 것이다** (D56).
                        노트의 「11조 3,145억원」은 사람이 읽는 문자열이라
                        모델에서 못 쓴다 — 이 파일은 숫자가 숫자다. */}
                    <a
                      href={downloadUrl(open.id, "xlsx")}
                      className="rounded border px-2 py-1 hover:border-num/50 hover:text-num"
                    >
                      Excel 내려받기
                    </a>
                    <a
                      href={downloadUrl(open.id, "md")}
                      className="rounded border px-2 py-1 text-muted-foreground hover:border-num/50 hover:text-num"
                    >
                      .md
                    </a>
                    <a
                      href={`/api/cards/${open.id}.md`}
                      target="_blank"
                      rel="noopener"
                      className="px-1 text-muted-foreground hover:text-foreground"
                    >
                      원문 보기 ↗
                    </a>
                  </span>
                )}
                {ready && open.vm.gate_passed && !open.published_path && (
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => void publish(open.id)}
                    className="h-7 text-[12px] hover:border-ok hover:text-ok"
                  >
                    검토 완료 · 발간
                  </Button>
                )}
                <span className="text-[11.5px] text-muted-foreground">
                  제목 옆 「수정」으로도 열 수 있습니다
                </span>
              </div>
              {publishing && (
                <p className="mb-3 text-[12px] text-muted-foreground">
                  {publishing}
                </p>
              )}
              {!ready && (
                <div className="max-w-[860px] rounded-lg border p-6">
                  <p className="text-[14px] font-medium">
                    {open.company || open.symbol} 초안을 만들고 있습니다.
                  </p>
                  <p className="mt-1.5 text-[13px] text-muted-foreground">
                    오른쪽에서 어느 단계까지 왔는지 볼 수 있습니다. 이 화면을
                    떠나도 생성은 계속되고 보드에 카드로 남습니다.
                  </p>
                </div>
              )}
              {ready && open.vm.gate_passed && editable.length === 0 && (
                <Alert className="mb-4 max-w-[860px] border-warn">
                  <AlertTitle>이 카드는 편집할 수 없습니다</AlertTitle>
                  <AlertDescription>
                    편집기가 붙기 전에 만들어진 카드라{" "}
                    <b>원문이 저장돼 있지 않습니다.</b> 수정하려면 같은 종목으로
                    다시 생성해 주십시오 — 새로 만든 카드에는 제목 옆에
                    「수정」이 나타납니다.
                  </AlertDescription>
                </Alert>
              )}
              {/* **주가 띠.** 리포트는 분기에 한 번 찍은 사진이라, 그 옆에
                  「지금 어떻게 움직이고 있나」가 없으면 클라이언트 질문에
                  못 들어간다. */}
              {open.moves?.[0] && <PriceStrip moves={open.moves[0]} />}
              {ready && (
                <CenterColumn
                  vm={open.vm}
                  error=""
                  onHeadings={onHeadings}
                  onEditSection={startEditing}
                  editableSections={editable.map((s) => s.title)}
                />
              )}
            </>
          ) : error ? (
            <Alert variant="destructive" className="max-w-[860px]">
              <AlertTitle>생성 실패</AlertTitle>
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : (
            <>
              <div className="mb-5 flex items-baseline justify-between gap-3">
                <span className="text-[13px] text-muted-foreground">
                  {tab === "peer"
                    ? peerCards.length > 0
                      ? `피어 그룹 ${peerCards.length}개 · 커버 종목이 동종 대비 어디인지 봅니다`
                      : "아직 만든 피어 그룹이 없습니다."
                    : singleCards.length > 0
                      ? `리포트 ${singleCards.length}건`
                      : "아직 작성한 리포트가 없습니다."}
                </span>
                <span className="flex items-center gap-2">
                  {tab === "peer" ? (
                    <Button size="sm" onClick={() => setComposingPeer(true)}>
                      피어 그룹 만들기
                    </Button>
                  ) : (
                    <Button size="sm" onClick={() => openCompose()}>
                      <PenLineIcon className="size-3.5" />새 리포트
                    </Button>
                  )}
                </span>
              </div>
              {(tab === "peer" ? peerCards : singleCards).length > 0 ? (
                <>
                  <Board
                    cards={tab === "peer" ? peerCards : singleCards}
                    kind={tab === "peer" ? "peer" : "single"}
                    onOpen={openCard}
                    onConfirm={confirm}
                    onDelete={remove}
                    onComposePeer={() => setComposingPeer(true)}
                  />
                  <BoardHint />
                </>
              ) : (
                /* **비어 있을 때는 큰 버튼 하나만.** 처음 온 사람에게
                   보여줄 것은 빈 칸반이 아니라 할 일이다. */
                <div className="py-20 text-center">
                  <p className="text-[15px] font-medium">
                    종목 하나로 시작합니다.
                  </p>
                  <p className="mx-auto mt-2 max-w-[420px] text-[13px] leading-[1.8] text-muted-foreground">
                    공시에서 수치를 읽어 초안을 만듭니다. 쓰던 리포트가 있으면
                    함께 올려 그 구성으로 쓰고 직전 추정과 비교합니다.
                  </p>
                  <Button className="mt-6" onClick={() => openCompose()}>
                    <PenLineIcon className="size-4" />첫 리포트 작성
                  </Button>
                </div>
              )}
            </>
          )}
        </div>

        <div className="border-t px-7 py-8 xl:sticky xl:top-(--header-h) xl:max-h-[calc(100vh-var(--header-h))] xl:overflow-y-auto xl:border-t-0 xl:border-l">
          {/* **단계 레일은 카드 것이다.** 폼 안에 있을 때는 마지막 생성 하나만
              보였다 — 두 건을 동시에 돌리면 나머지는 진단을 볼 수 없었다. */}
          {open && (
            <StageRail
              stages={open.vm.stages ?? []}
              steps={open.id === runningId ? steps : []}
              running={busy && open.id === runningId}
              elapsed={elapsed}
            />
          )}
          {ready && !open.vm.error && (
            <EvidenceRail
              vm={open.vm}
              headings={headings}
              versions={open.versions}
              cardId={open.id}
              onRecomputed={() => void openCard(open.id)}
            />
          )}
        </div>
      </div>

      <AskWidget cardCount={cards.length} />

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

/** 카드 위 주가 띠 — 1일·5일·1개월·6개월·1년. */
function PriceStrip({ moves }: { moves: Moves }) {
  if (!moves.items.some((m) => m.change_pct != null)) return null;
  return (
    <div className="mb-4 flex max-w-[860px] flex-wrap items-baseline gap-x-5 gap-y-1 rounded-lg border px-3.5 py-2">
      <span className="text-[11px] text-muted-foreground">주가</span>
      {moves.last_close != null && (
        <span className="font-mono text-[13px]">
          {moves.last_close.toLocaleString()}
          <span className="ml-1 text-[10.5px] text-muted-foreground">
            {moves.last_date.slice(4, 6)}/{moves.last_date.slice(6, 8)}
          </span>
        </span>
      )}
      {moves.items.map((m) => (
        <span
          key={m.key}
          className="font-mono text-[12px] tabular-nums"
          title={
            m.change_pct == null
              ? "비교할 자료가 없습니다"
              : `${m.from_date} → ${m.to_date} (${m.days}거래일)`
          }
        >
          <span className="text-[10.5px] text-muted-foreground">{m.label} </span>
          <span
            className={cn(
              m.change_pct == null
                ? "text-muted-foreground"
                : m.change_pct > 0
                  ? "text-bad"
                  : m.change_pct < 0
                    ? "text-num"
                    : "text-muted-foreground",
            )}
          >
            {fmtPct(m.change_pct)}
          </span>
        </span>
      ))}
    </div>
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
            <Badge className="bg-warn/15 text-warn border-transparent">
              ● 안내
            </Badge>
            <Hint>{vm.notice}</Hint>
          </CardContent>
        </Card>
      )}

      {vm.published_path && (
        <Card className="max-w-[860px] border-ok">
          <CardContent className="py-4">
            <Badge className="bg-ok/15 text-ok border-transparent">
              ● 발간 완료
            </Badge>
            <Hint>
              {vm.published_path}
              <br />
              추정이 이력에 저장됐습니다. 다음 발간에서 이 값 대비 변화가
              표시됩니다.
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
                    dangerouslySetInnerHTML={{ __html: vm.segment_chart }}
                  />
                  {/* 색 막대만 있으면 「감으로만 보인다」 — 금액과 비중을
                      붙인다. 값은 서버가 레지스트리에서 꺼낸 문자열이라
                      본문의 같은 숫자와 갈라질 수 없다. */}
                  {vm.segment_items.length > 0 ? (
                    <table className="seg-table">
                      <thead>
                        <tr>
                          <th>부문</th>
                          <th className="num">매출</th>
                          <th className="num">비중</th>
                        </tr>
                      </thead>
                      <tbody>
                        {vm.segment_items.map((s) => (
                          <tr key={s.name}>
                            <td>
                              <span
                                className="seg-chip"
                                style={{ background: s.color }}
                              />
                              {s.name}
                            </td>
                            <td className="num">{s.amount || "—"}</td>
                            <td className="num">{s.share || "—"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  ) : (
                    <div
                      dangerouslySetInnerHTML={{ __html: vm.segment_legend }}
                    />
                  )}
                </>
              )}
              {vm.quarter_chart && (
                <>
                  <h2>분기 추이</h2>
                  {/* 매출은 막대, 이익률은 선. **비율을 막대로 그리면**
                      8%와 9%가 거의 같아 보인다 — 크기가 아니라 수준이다. */}
                  <div
                    className="chart"
                    dangerouslySetInnerHTML={{ __html: vm.quarter_chart }}
                  />
                  {vm.quarter_margin_chart && (
                    <div
                      className="chart"
                      dangerouslySetInnerHTML={{
                        __html: vm.quarter_margin_chart,
                      }}
                    />
                  )}
                  <Hint>{vm.quarter_note}</Hint>
                </>
              )}
              {vm.trend_chart && (
                <>
                  <h2>매출·영업이익 추이</h2>
                  <div
                    className="chart"
                    dangerouslySetInnerHTML={{
                      __html: vm.trend_chart + vm.trend_legend,
                    }}
                  />
                  <Hint>
                    {vm.trend_note
                      ? vm.trend_note
                      : "정확한 수치는 아래 표에 있습니다. 차트는 크기 비교용입니다."}
                  </Hint>
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
            차단된 초안은 표시하지 않습니다. 검토자가 결과로 착각할 수 있기
            때문입니다. 오른쪽 위반 내역을 확인하세요.
          </AlertDescription>
        </Alert>
      )}
    </div>
  );
}
