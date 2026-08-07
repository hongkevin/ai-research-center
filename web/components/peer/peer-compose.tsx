"use client";

import { useState } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { CompanySearch, symbolOf } from "@/components/workbench/company-search";
import {
  createPeerCard,
  suggestPeers,
  type PeerCandidate,
  type PeerSuggestion,
} from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * 피어 그룹 만들기 — **자동 후보 + 사람 확정.**
 *
 * 완전 자동은 안 된다: 상관은 테마 변화에 따라 흔들리고(같은 씨앗의 top15가
 * 기간이 바뀌면 4/15까지 겹침이 떨어진다), 리포트 도구에서 **잘못된 피어는
 * 잘못된 숫자보다 눈에 잘 띈다.**
 *
 * 완전 수동도 안 된다: 인터뷰의 고통이 정확히 *"어떤 종목을 봐야 할지
 * 모르겠다"*였다. 빈 검색창을 주면 이미 아는 종목만 넣고, 한화시스템·
 * 코츠테크놀로지 같은 것은 영영 못 찾는다.
 *
 * 그래서 씨앗 1~2개를 받아 후보를 **상관계수와 함께** 내고, 체크한 것만
 * 카드에 박는다. **섹터 이름은 우리가 붙이지 않는다** — 상관은 산업이 아니라
 * 지금 같이 움직이는 테마를 찾는다(현대건설 씨앗 → 원전 테마).
 */
export function PeerCompose({
  onCreated,
  initialSeeds = [],
  initialName = "",
}: {
  onCreated: (id: string) => void;
  /** 커버리지에서 넘어온 씨앗. **다시 치게 하지 않는다** — 가장 큰 마찰이었다 */
  initialSeeds?: { symbol: string; company: string }[];
  initialName?: string;
}) {
  const [seedInput, setSeedInput] = useState("");
  const [seeds, setSeeds] =
    useState<{ symbol: string; company: string }[]>(initialSeeds);
  const [suggestion, setSuggestion] = useState<PeerSuggestion | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [name, setName] = useState(initialName);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  function addSeed() {
    const code = symbolOf(seedInput);
    if (!/^\d{6}$/.test(code) || seeds.some((s) => s.symbol === code)) return;
    setSeeds((s) => [...s, { symbol: code, company: seedInput }]);
    setSeedInput("");
    setSuggestion(null);
  }

  async function findPeers() {
    setBusy(true);
    setError("");
    try {
      const got = await suggestPeers(seeds.map((s) => s.symbol));
      setSuggestion(got);
      // **기본 선택은 없다.** 사람이 고르는 것이 이 화면의 존재 이유다.
      setPicked(new Set());
      if (!name) setName(`${got.seeds[0]?.company || got.seeds[0]?.symbol} 피어`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function create() {
    setBusy(true);
    setError("");
    try {
      const symbols = [...seeds.map((s) => s.symbol), ...picked];
      const { card_id } = await createPeerCard(name.trim(), symbols);
      onCreated(card_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-5">
      <div>
        <p className="text-[13px] leading-[1.8] text-muted-foreground">
          <strong>커버하는 종목</strong>을 1~2개 넣으십시오. 그것과{" "}
          <strong>같이 움직이는</strong> 종목을 찾아 드립니다 — 업종 분류로는
          못 찾는 것들입니다.
        </p>
      </div>

      <div className="flex gap-2">
        <CompanySearch value={seedInput} onChange={setSeedInput} />
        <Button variant="outline" onClick={addSeed} disabled={!seedInput.trim()}>
          씨앗 추가
        </Button>
      </div>

      {seeds.length > 0 && (
        <div className="flex flex-wrap items-center gap-2">
          {seeds.map((s) => (
            <span
              key={s.symbol}
              className="flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[12px]"
            >
              {s.company || s.symbol}
              <button
                type="button"
                onClick={() => {
                  setSeeds((v) => v.filter((x) => x.symbol !== s.symbol));
                  setSuggestion(null);
                }}
                className="text-muted-foreground hover:text-foreground"
              >
                ×
              </button>
            </span>
          ))}
          <Button size="sm" onClick={() => void findPeers()} disabled={busy}>
            {busy ? "찾는 중…" : "같이 움직이는 종목 찾기"}
          </Button>
        </div>
      )}

      {seeds.length === 1 && !suggestion && (
        <p className="text-[12px] text-muted-foreground">
          씨앗을 <strong>둘</strong> 넣으면 훨씬 또렷해집니다 — 하나일 때는
          top15에 다른 테마가 섞입니다.
        </p>
      )}

      {error && (
        <Alert variant="destructive">
          <AlertTitle>후보를 찾지 못했습니다</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {suggestion && (
        <Candidates
          suggestion={suggestion}
          picked={picked}
          onToggle={(sym) =>
            setPicked((p) => {
              const next = new Set(p);
              if (next.has(sym)) next.delete(sym);
              else next.add(sym);
              return next;
            })
          }
        />
      )}

      {suggestion && (
        <div className="flex items-end gap-2 border-t pt-4">
          <label className="flex-1">
            <span className="text-[12px] text-muted-foreground">그룹 이름</span>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="예: 방산 4종"
              className="mt-1"
            />
          </label>
          <Button
            onClick={() => void create()}
            disabled={busy || seeds.length + picked.size < 2}
          >
            {seeds.length + picked.size}종목으로 카드 만들기
          </Button>
        </div>
      )}
    </div>
  );
}

function Candidates({
  suggestion,
  picked,
  onToggle,
}: {
  suggestion: PeerSuggestion;
  picked: Set<string>;
  onToggle: (symbol: string) => void;
}) {
  return (
    <div className="space-y-3">
      {/* **찾지 못했으면 찾지 못했다고 말한다.** 목록은 늘 채워져 나오므로
          이 경고가 없으면 사람이 그것을 섹터로 읽는다. */}
      {!suggestion.meaningful && suggestion.note && (
        <Alert className="border-warn">
          <AlertTitle>이 목록을 섹터로 읽지 마십시오</AlertTitle>
          <AlertDescription>{suggestion.note}</AlertDescription>
        </Alert>
      )}

      <div className="max-h-[320px] overflow-y-auto rounded-md border">
        {suggestion.candidates.map((c) => (
          <CandidateRow
            key={c.symbol}
            candidate={c}
            checked={picked.has(c.symbol)}
            onToggle={() => onToggle(c.symbol)}
          />
        ))}
        {suggestion.candidates.length === 0 && (
          <p className="p-4 text-[13px] text-muted-foreground">
            같이 움직이는 종목을 찾지 못했습니다.
          </p>
        )}
      </div>

      <p className="text-[11.5px] leading-[1.7] text-muted-foreground">
        최근 거래일 기준 <strong>시장 요인을 뺀</strong> 상관입니다. 같이
        움직인다는 뜻이지 <strong>같은 산업이라는 뜻이 아닙니다</strong> —
        건설 종목을 넣으면 원전 테마가 나올 수 있습니다. 확정 전에 보십시오.
        {suggestion.candidates.length > 0 && (
          <>
            {" "}
            (후보 {suggestion.candidates.length}종목 · 유니버스{" "}
            {suggestion.universe.toLocaleString()}종목 · 내부 상관{" "}
            {suggestion.cohesion.toFixed(2)})
          </>
        )}
      </p>
    </div>
  );
}

function CandidateRow({
  candidate: c,
  checked,
  onToggle,
}: {
  candidate: PeerCandidate;
  checked: boolean;
  onToggle: () => void;
}) {
  return (
    <label
      className={cn(
        "flex cursor-pointer items-center gap-3 border-b px-3 py-2 last:border-b-0",
        checked ? "bg-accent/50" : "hover:bg-accent/30",
      )}
    >
      <Checkbox checked={checked} onCheckedChange={onToggle} />
      <span className="flex-1 text-[13px]">
        {c.company || c.symbol}
        <span className="ml-1.5 font-mono text-[11px] text-muted-foreground">
          {c.symbol}
        </span>
      </span>
      {/* **상관계수를 늘 보여준다.** 이게 없으면 순서만 남고, 0.65와 0.31이
          같은 무게로 읽힌다. */}
      <span className="font-mono text-[12px] tabular-nums">
        {c.correlation.toFixed(2)}
      </span>
      <span className="w-16 text-right text-[11px] text-muted-foreground">
        {c.overlap}일
      </span>
    </label>
  );
}
