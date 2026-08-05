"use client";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Hint } from "@/components/workbench/section-label";
import { COLUMNS, COLUMN_LABEL, type CardSummary, type Column } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * 작업 보드 — 진행 중인 리포트들이 칸에 놓인다.
 *
 * 왜 보드인가: 생성물이 메모리에 30분만 있던 동안 화면은 "한 번에 끝내야 하는"
 * 모양이었다(입력 → 대기 → 툭). 리포트가 지속되는 카드가 되면 나갔다 돌아올 수
 * 있고, 여러 건이 동시에 뜬다 — RA는 애널리스트 3~4명을 동시에 보조하고
 * 어닝시즌에는 다종목을 한꺼번에 처리한다.
 *
 * **칸은 카드가 실제로 멈출 수 있는 자리여야 한다.** 기계가 1.5초에 안 멈추고
 * 통과하면 모든 카드가 마지막 칸에 쌓여 보드가 아니게 된다. 그래서 「확인 필요」가
 * 이 보드의 존재 이유다 — 검산이 어긋났거나 게이트가 막은 카드만 거기 선다.
 *
 * **칸 배정은 자동이다.** 옮기는 것이 일이 되면 아무도 안 옮기고 보드는 버려진다.
 * 사람은 「확인함」만 누른다. 임의의 칸으로 끌어 옮기는 것은 열어뒀지만 아직
 * 만들지 않았다 (D40).
 */

const TONE: Record<Column, string> = {
  running: "text-muted-foreground",
  attention: "text-bad",
  review: "text-warn",
  published: "text-ok",
};

export function Board({
  cards,
  onOpen,
  onConfirm,
  onDelete,
}: {
  cards: CardSummary[];
  onOpen: (id: string) => void;
  onConfirm: (id: string) => void;
  onDelete: (id: string) => void;
}) {
  if (cards.length === 0) {
    return (
      <p className="py-16 text-center text-[13px] text-muted-foreground">
        왼쪽에서 종목을 생성하면 카드가 여기에 쌓입니다.
        <br />
        생성은 <b>1.5초</b>에 검증된 수치를 채우고, 문장은 그 뒤에 붙습니다.
      </p>
    );
  }

  return (
    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
      {COLUMNS.map((col) => {
        const inCol = cards.filter((c) => c.column === col);
        return (
          <section key={col} className="min-w-0">
            <h2 className="mb-2.5 flex items-baseline gap-1.5 text-[11px] font-semibold uppercase tracking-[0.09em] text-muted-foreground">
              <span className={TONE[col]}>{COLUMN_LABEL[col]}</span>
              <span className="font-mono normal-case tracking-normal">{inCol.length}</span>
            </h2>
            <div className="space-y-2">
              {inCol.map((c) => (
                <CardTile
                  key={c.id}
                  card={c}
                  onOpen={onOpen}
                  onConfirm={onConfirm}
                  onDelete={onDelete}
                />
              ))}
            </div>
          </section>
        );
      })}
    </div>
  );
}

function CardTile({
  card,
  onOpen,
  onConfirm,
  onDelete,
}: {
  card: CardSummary;
  onOpen: (id: string) => void;
  onConfirm: (id: string) => void;
  onDelete: (id: string) => void;
}) {
  const running = card.column === "running";
  return (
    <div
      className={cn(
        "rounded-lg border bg-card p-2.5",
        card.column === "attention" && "border-bad/40",
      )}
    >
      <button
        type="button"
        disabled={running}
        onClick={() => onOpen(card.id)}
        className={cn("w-full text-left", !running && "cursor-pointer")}
      >
        <div className="flex items-baseline justify-between gap-2">
          <span className="truncate text-[13px] font-medium">
            {card.company || card.symbol}
          </span>
          <span className="flex-none font-mono text-[11px] text-muted-foreground">
            FY{card.year}
          </span>
        </div>
        <div className="mt-0.5 font-mono text-[11px] text-muted-foreground">
          {card.symbol}
          {running ? (
            <span className="ml-1.5">· 생성 중…</span>
          ) : (
            <span className="ml-1.5">
              · 수치 <span className="text-num">{card.registry_size}</span>건 · 단계{" "}
              {card.stage_count}
            </span>
          )}
        </div>

        {card.attention.map((a, i) => (
          <div key={i} className="mt-1.5 rounded-md bg-bad/10 px-2 py-1 text-[11.5px]">
            {a}
          </div>
        ))}
      </button>

      {card.column === "attention" && (
        <Button
          size="sm"
          variant="outline"
          className="mt-2 h-7 w-full text-[12px]"
          onClick={() => onConfirm(card.id)}
        >
          확인함
        </Button>
      )}
      {card.column === "published" && (
        <Badge className="mt-1.5 border-transparent bg-ok/15 text-ok">● 발간됨</Badge>
      )}
      {!running && (
        <button
          type="button"
          onClick={() => onDelete(card.id)}
          className="mt-1 text-[11px] text-muted-foreground hover:text-bad"
        >
          삭제
        </button>
      )}
    </div>
  );
}

export function BoardHint() {
  return (
    <Hint>
      「확인 필요」는 <b>기계가 이미 아는 것</b>에서만 자동으로 판정합니다 — 게이트 차단,
      검산 불일치, 단계 실패. <b>정상적으로 없는 것</b>(단일 부문 회사의 부문 손익 등)은
      올라오지 않습니다.
    </Hint>
  );
}
