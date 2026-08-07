"use client";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import type { PeerColumn, PeerMember, PeerTable as Table } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * 피어 비교표 — 여러 종목을 나란히.
 *
 * **모든 칸이 구성원 카드의 레지스트리에서 온 표시 문자열이다.** 여기서
 * 숫자를 만들지 않는다 — 평균도 중앙값도 순위도 없다. 내려면 출처가 있는
 * 새 수치라 레지스트리에 등록돼야 하고, 슬쩍 만들면 출처 없는 숫자가 표에
 * 앉는다 (D68).
 */
export function PeerTable({
  table,
  members,
  attention,
  onOpenCard,
}: {
  table: Table;
  members: PeerMember[];
  attention: string[];
  onOpenCard: (cardId: string) => void;
}) {
  const groups = [...new Set(table.rows.map((r) => r.group))];

  return (
    <div className="max-w-full space-y-4">
      {/* **기준 기간이 섞이면 표가 조용히 거짓말을 한다.** 한 종목이 연간이고
          다른 종목이 3분기 누적인데 매출이 나란히 서면 화면상 아무 이상이 없다. */}
      {table.mixed_basis && (
        <Alert variant="destructive">
          <AlertTitle>나란히 비교할 수 없습니다</AlertTitle>
          <AlertDescription>{table.note}</AlertDescription>
        </Alert>
      )}

      {attention
        .filter(() => !table.mixed_basis)
        .map((a, i) => (
          <Alert key={i} className="border-warn">
            <AlertDescription>{a}</AlertDescription>
          </Alert>
        ))}

      {table.rows.length > 0 ? (
        /* 표가 넓다. **본문이 가로로 스크롤되면 안 되므로** 표만 흐른다. */
        <div className="overflow-x-auto rounded-lg border">
          <table className="w-full min-w-[560px] border-collapse text-[13px]">
            <thead>
              <tr className="border-b bg-muted/40">
                <th className="sticky left-0 z-10 bg-muted/40 px-3 py-2 text-left font-medium">
                  <span className="text-[11px] text-muted-foreground">항목</span>
                </th>
                {table.columns.map((c) => (
                  <ColumnHead key={c.symbol} column={c} onOpen={onOpenCard} />
                ))}
              </tr>
            </thead>
            <tbody>
              {groups.map((g) => (
                <GroupRows key={g} group={g} table={table} />
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="rounded-lg border p-6">
          <p className="text-[14px] font-medium">아직 세울 표가 없습니다.</p>
          <p className="mt-1.5 text-[13px] leading-[1.8] text-muted-foreground">
            {table.note ||
              "구성원의 종목 카드를 먼저 만들면 그 수치가 여기 들어옵니다."}
          </p>
        </div>
      )}

      <Pending members={members} />

      {!table.mixed_basis && table.note && table.rows.length > 0 && (
        <p className="text-[11.5px] text-muted-foreground">{table.note}</p>
      )}
    </div>
  );
}

function ColumnHead({
  column: c,
  onOpen,
}: {
  column: PeerColumn;
  onOpen: (cardId: string) => void;
}) {
  return (
    <th className="min-w-[120px] px-3 py-2 text-right font-medium">
      {c.card_id ? (
        <button
          type="button"
          onClick={() => onOpen(c.card_id)}
          className="text-right hover:underline"
        >
          <span className="block truncate">{c.company || c.symbol}</span>
          <span className="block font-mono text-[10.5px] font-normal text-muted-foreground">
            {c.basis || c.symbol}
          </span>
        </button>
      ) : (
        <span className="block text-muted-foreground">
          <span className="block truncate">{c.company || c.symbol}</span>
          {/* 카드가 없는 종목도 열은 남긴다 — 빼 버리면 「왜 안 나오지」가 된다. */}
          <span className="block font-mono text-[10.5px] font-normal">
            카드 없음
          </span>
        </span>
      )}
    </th>
  );
}

function GroupRows({ group, table }: { group: string; table: Table }) {
  const rows = table.rows.filter((r) => r.group === group);
  return (
    <>
      <tr className="border-b bg-muted/20">
        <td
          colSpan={table.columns.length + 1}
          className="px-3 py-1 text-[10.5px] font-semibold uppercase tracking-[0.08em] text-muted-foreground"
        >
          {group}
        </td>
      </tr>
      {rows.map((r) => (
        <tr key={r.label} className="border-b last:border-b-0">
          <td className="sticky left-0 z-10 bg-card px-3 py-1.5 whitespace-nowrap">
            {r.label}
          </td>
          {r.cells.map((cell, i) => (
            <td
              key={i}
              className={cn(
                "px-3 py-1.5 text-right font-mono tabular-nums",
                cell.absent && "text-muted-foreground",
              )}
              /* 되짚기용 — 어느 카드의 어느 수치인가 (D44·D36) */
              title={cell.absent ? "이 종목에는 없는 항목입니다" : cell.key}
            >
              {cell.display}
            </td>
          ))}
        </tr>
      ))}
    </>
  );
}

/** 아직 카드가 없는 구성원. **커버 밖 종목을 나중에 만드는 것이 본체다.** */
function Pending({ members }: { members: PeerMember[] }) {
  const waiting = members.filter((m) => m.status !== "ready");
  if (waiting.length === 0) return null;
  return (
    <div className="rounded-lg border border-dashed p-4">
      <p className="text-[13px] font-medium">
        아직 표에 못 서는 종목 {waiting.length}개
      </p>
      <p className="mt-1 text-[12px] leading-[1.8] text-muted-foreground">
        이 종목들의 <strong>리포트를 만들면</strong> 수치가 표에 들어옵니다 —
        피어 카드는 숫자를 자기가 만들지 않고 종목 카드를 가리킵니다.
      </p>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {waiting.map((m) => (
          <span
            key={m.symbol}
            className="rounded border px-2 py-0.5 text-[11.5px]"
          >
            {m.company || m.symbol}
            {m.status === "failed" && (
              <span className="ml-1 text-bad">· 실패</span>
            )}
          </span>
        ))}
      </div>
    </div>
  );
}
