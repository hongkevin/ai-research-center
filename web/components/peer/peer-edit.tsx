"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { CompanySearch, symbolOf } from "@/components/workbench/company-search";
import { renameCard, setPeerMembers, type PeerMember } from "@/lib/api";

/**
 * 피어 그룹 고치기 — 이름과 구성원.
 *
 * **그룹은 한 번 만들고 끝나는 것이 아니다.** 새로 상장한 종목이 들어오고,
 * 커버가 바뀌고, 처음에 넣은 것이 알고 보니 다른 테마였다. 고칠 수 없으면
 * 매번 새로 만들게 되고, 그러면 [D68](../../docs/decisions.md#d68)의
 * 「고정한 그룹」이 성립하지 않는다 — 고정할 수 있어야 이력이 이어진다.
 *
 * 종목을 넣으면 **수치는 자동으로 채워진다.** 리포트를 쓰라고 하지 않는다 —
 * 비교표에 필요한 것은 서술이 아니라 재무제표다.
 */
export function PeerEdit({
  cardId,
  name,
  members,
  onDone,
  onCancel,
}: {
  cardId: string;
  name: string;
  members: PeerMember[];
  onDone: () => void;
  onCancel: () => void;
}) {
  const [title, setTitle] = useState(name);
  const [rows, setRows] = useState<{ symbol: string; company: string }[]>(
    members.map((m) => ({ symbol: m.symbol, company: m.company })),
  );
  const [adding, setAdding] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  function add() {
    const code = symbolOf(adding);
    if (!/^\d{6}$/.test(code) || rows.some((r) => r.symbol === code)) return;
    setRows((v) => [...v, { symbol: code, company: adding }]);
    setAdding("");
  }

  async function save() {
    setBusy(true);
    setError("");
    try {
      if (title.trim() && title.trim() !== name) {
        await renameCard(cardId, title.trim());
      }
      await setPeerMembers(
        cardId,
        rows.map((r) => r.symbol),
      );
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4 rounded-lg border p-4">
      <label className="block">
        <span className="text-[12px] text-muted-foreground">그룹 이름</span>
        <Input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          className="mt-1"
        />
      </label>

      <div>
        <span className="text-[12px] text-muted-foreground">
          종목 {rows.length}개
        </span>
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {rows.map((r) => (
            <span
              key={r.symbol}
              className="flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[12px]"
            >
              {r.company || r.symbol}
              <button
                type="button"
                onClick={() =>
                  setRows((v) => v.filter((x) => x.symbol !== r.symbol))
                }
                className="text-muted-foreground hover:text-bad"
                aria-label={`${r.company || r.symbol} 빼기`}
              >
                ×
              </button>
            </span>
          ))}
          {rows.length === 0 && (
            <span className="text-[12px] text-muted-foreground">
              종목을 하나 이상 넣으십시오.
            </span>
          )}
        </div>
      </div>

      <div className="flex gap-2">
        <CompanySearch value={adding} onChange={setAdding} />
        <Button variant="outline" onClick={add} disabled={!adding.trim()}>
          넣기
        </Button>
      </div>

      {error && <p className="text-[12px] text-bad">{error}</p>}

      <div className="flex items-center justify-between border-t pt-3">
        <span className="text-[11.5px] text-muted-foreground">
          새로 넣은 종목은 <strong>수치가 자동으로 채워집니다</strong> — 리포트를
          쓸 필요가 없습니다.
        </span>
        <span className="flex gap-2">
          <Button variant="ghost" size="sm" onClick={onCancel} disabled={busy}>
            취소
          </Button>
          <Button
            size="sm"
            onClick={() => void save()}
            disabled={busy || rows.length === 0}
          >
            {busy ? "저장 중…" : "저장"}
          </Button>
        </span>
      </div>
    </div>
  );
}
