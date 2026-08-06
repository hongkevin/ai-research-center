"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { CompanySearch } from "@/components/workbench/company-search";
import { Hint, SectionLabel } from "@/components/workbench/section-label";
import { hasPeriodicInfo, periodKey, type PeriodCode } from "@/lib/periods";
import type { Filing, Preliminary } from "@/lib/api";

export interface FormState {
  symbol: string;
  year: number;
  /** 어느 정기보고서인가. 엔진은 진작 이 축을 알고 있었다(PeriodType). */
  period: PeriodCode;
  llm: boolean;
  /** 최근 기사를 찾아 「최근 이슈」 절을 붙일 것인가 (D45). */
  search: boolean;
  /** 업로드한 직전 노트 (D48). 숫자는 본문에 안 들어간다. */
  prior_markdown: string;
  prior_name: string;
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
  filings,
  loadingFilings,
  preliminary,
  newsAvailable = false,
}: {
  state: FormState;
  onChange: (s: FormState) => void;
  onSubmit: (publish: boolean) => void;
  busy: boolean;
  /** DART가 준 실제 정기보고서 목록. 회사를 고르기 전에는 비어 있다. */
  filings: Filing[];
  /** DART 조회는 시간이 걸린다. 아무 말 없이 잠겨 있으면 고장으로 읽힌다. */
  loadingFilings: boolean;
  preliminary: Preliminary | null;
  /** 서버에 기사 검색 키가 있는가. 없으면 체크박스를 잠근다. */
  newsAvailable?: boolean;
}) {
  const set = <K extends keyof FormState>(k: K, v: FormState[K]) =>
    onChange({ ...state, [k]: v });

  const prior = state.prior_name;

  // 평소엔 최신만 보인다. 과거 재현이 필요할 때만 목록을 연다.
  const [picking, setPicking] = useState(false);
  const current = filings.find(
    (f) => f.year === state.year && f.period === state.period,
  );

  return (
    <div>
      {prior && (
        <div className="mb-5 flex items-center justify-between gap-2 rounded-md border px-3 py-2">
          <span className="min-w-0 truncate text-[12px]">
            <span className="text-muted-foreground">이어쓰기 · </span>
            {prior}
          </span>
          <button
            type="button"
            onClick={() =>
              onChange({ ...state, prior_markdown: "", prior_name: "" })
            }
            className="flex-none text-[11.5px] text-muted-foreground hover:text-bad"
          >
            해제
          </button>
        </div>
      )}
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

        <Label className="text-xs text-muted-foreground mt-5 block">
          정기보고서
        </Label>

        {/* **최신을 사실로 보여준다.** 최신 보고서가 이미 prior·prior2를 담고
            있어 과거를 고를 이유가 거의 없고, 잘못 고르면 철 지난 노트가
            조용히 나온다. 다만 선택을 없애지는 않는다 — D34의 시점 정합성
            재현(과거 시점 데이터만으로 만든 노트가 유효했는가)이 이 문으로
            들어온다. */}
        {filings.length === 0 ? (
          <p className="mt-1 text-[12.5px] text-muted-foreground">
            {loadingFilings ? "공시 목록을 읽는 중…" : "회사를 먼저 고르십시오"}
          </p>
        ) : picking ? (
          <select
            autoFocus
            value={periodKey(state)}
            disabled={busy}
            onChange={(e) => {
              const [y, p] = e.target.value.split(":");
              onChange({ ...state, year: Number(y), period: p as PeriodCode });
              setPicking(false);
            }}
            className="mt-1 h-9 w-full rounded-md border bg-transparent px-2.5 text-[13px]"
          >
            {filings.map((f) => (
              <option key={f.rcept_no} value={`${f.year}:${f.period}`}>
                {f.label} · {f.filed_at} 제출
              </option>
            ))}
          </select>
        ) : (
          <div className="mt-1">
            {/* 왼쪽 열이 320px이라 한 줄에 다 넣으면 「제출」이 쪼개진다.
                이름과 조작을 위, 사실을 아래로 나눈다. */}
            <div className="flex items-baseline justify-between gap-2">
              <span className="min-w-0 truncate text-[13px] font-medium">
                {current?.label ?? "—"}
              </span>
              <button
                type="button"
                disabled={busy}
                onClick={() => setPicking(true)}
                className="flex-none whitespace-nowrap text-[11.5px] text-muted-foreground hover:text-foreground"
              >
                다른 보고서
              </button>
            </div>
            {current && (
              <div className="text-[11.5px] text-muted-foreground">
                <span className="whitespace-nowrap">
                  {current.filed_at} 제출
                </span>
                {current.url && (
                  <>
                    {" · "}
                    <a
                      href={current.url}
                      target="_blank"
                      rel="noopener"
                      className="whitespace-nowrap text-num hover:underline"
                    >
                      공시↗
                    </a>
                  </>
                )}
              </div>
            )}
          </div>
        )}

        {/* 더 최신 실적이 이미 나와 있으면 말해준다. 모르고 옛 보고서로 쓰는
            것과, 알고도 그걸 쓰는 것은 다르다. */}
        {preliminary && (
          <div className="mt-1.5 rounded-md border border-warn/50 bg-warn/10 px-2.5 py-1.5 text-[11.5px]">
            <b className="text-warn">{preliminary.filed_at} 잠정실적</b>이 이미
            나왔습니다. ARC는 아직 정기보고서만 읽습니다 —{" "}
            <a
              href={preliminary.url}
              target="_blank"
              rel="noopener"
              className="text-num hover:underline"
            >
              공시 원문↗
            </a>
          </div>
        )}

        {!hasPeriodicInfo(state) && (
          <Hint>
            분기보고서에는 <b>주식수·배당·감사의견·인력·지분·출자</b>가
            없습니다(연간 공시). 부문 손익과 재무제표는 그대로 나옵니다.
          </Hint>
        )}

        {/* 「문장까지 작성」이라고만 써 두면 누가 쓰는지가 안 보인다.
            AI가 하는 일과 하지 않는 일을 라벨에서 갈라 준다 — 수치는
            어느 쪽이든 코드가 만든다는 게 이 제품의 전제다. */}
        <SectionLabel>AI 작성</SectionLabel>
        <div className="flex items-center gap-2 mt-1.5">
          <Checkbox
            id="llm"
            checked={state.llm}
            onCheckedChange={(c) => set("llm", c === true)}
            disabled={busy}
          />
          <Label htmlFor="llm" className="text-sm">
            AI로 서술 작성
          </Label>
        </div>
        <Hint>
          끄면 표와 수치만 만듭니다. <b>수치는 어느 쪽이든 같습니다</b> — AI는
          문장만 쓰고 숫자는 코드가 만듭니다.
        </Hint>

        <div className="flex items-center gap-2 mt-3">
          <Checkbox
            id="search"
            checked={state.search && state.llm && newsAvailable}
            onCheckedChange={(c) => set("search", c === true)}
            disabled={busy || !state.llm || !newsAvailable}
          />
          <Label
            htmlFor="search"
            className={`text-sm ${!state.llm || !newsAvailable ? "text-muted-foreground" : ""}`}
          >
            AI로 최근 기사 반영
          </Label>
        </div>
        <Hint>
          {!newsAvailable ? (
            <>
              기사 검색 키가 없어 켤 수 없습니다 (<code>NAVER_CLIENT_ID</code> ·{" "}
              <code>NAVER_CLIENT_SECRET</code>).
            </>
          ) : !state.llm ? (
            "서술 작성을 켜야 기사를 문단으로 만들 수 있습니다."
          ) : (
            <>
              공시에 없는 사건(수주·규제·소송 등)을 <b>「최근 이슈」 절</b>로
              붙입니다. 공시 밖이라 <b>숫자는 가려서</b> 싣고 기사 링크를 함께
              답니다.
            </>
          )}
        </Hint>

        <Button type="submit" disabled={busy} className="w-full mt-6">
          {busy ? "작성 중…" : "초안 작성"}
        </Button>
        <Hint>
          공시에서 수치를 읽어 초안을 만듭니다. 읽고 고친 뒤 카드에서
          발간합니다.
        </Hint>
      </form>
    </div>
  );
}
