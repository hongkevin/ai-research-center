"use client";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Hint } from "@/components/workbench/section-label";
import {
  COLUMNS,
  COLUMN_LABEL,
  COLUMN_HINT,
  type CardSummary,
  type Column,
} from "@/lib/api";
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
  draft: "text-muted-foreground",
  review: "text-warn",
  handoff: "text-ok",
};

/**
 * **칸반이 둘이다.** 종목 카드와 피어 카드를 한 칸반에 섞으면 「4종목짜리」와
 * 「1종목짜리」가 같은 칸에서 나란히 서서 읽히지 않는다.
 *
 * 그렇다고 다른 화면으로 보내지는 않는다 — 둘 다 초안이고, 검토하고, 넘기는
 * 같은 수명을 살고(D68), RA가 알고 싶은 것은 **오늘 내가 굴리는 일 전체**다.
 * 그래서 한 화면, 칸반 둘이다.
 */
export function Board({
  cards,
  onOpen,
  onConfirm,
  onDelete,
  onComposePeer,
}: {
  cards: CardSummary[];
  onOpen: (id: string) => void;
  onConfirm: (id: string) => void;
  onDelete: (id: string) => void;
  onComposePeer?: () => void;
}) {
  const singles = cards.filter((c) => c.kind !== "peer");
  const peers = cards.filter((c) => c.kind === "peer");

  return (
    <div className="space-y-8">
      <Lane
        title="종목"
        cards={singles}
        onOpen={onOpen}
        onConfirm={onConfirm}
        onDelete={onDelete}
      />
      <div>
        <SectionTitle title="피어 그룹" count={peers.length} />
        {peers.length > 0 ? (
          <Columns
            cards={peers}
            onOpen={onOpen}
            onConfirm={onConfirm}
            onDelete={onDelete}
          />
        ) : (
          /* **빈 칸 세 개를 세우지 않는다.** 피어가 없을 때 빈 칸반을 그리면
             화면 절반이 비어 보이고, 그게 보드를 무겁게 만든다. */
          <div className="rounded-lg border border-dashed px-4 py-5">
            <p className="text-[13px]">
              커버 밖 종목을 한 표로 볼 수 있습니다.
            </p>
            <p className="mt-1 text-[12px] leading-[1.7] text-muted-foreground">
              커버하는 종목을 씨앗으로 주면 <strong>같이 움직이는</strong>{" "}
              종목을 찾아 드립니다 — 업종 분류로는 못 찾는 것들입니다.
            </p>
            {onComposePeer && (
              <Button
                size="sm"
                variant="outline"
                onClick={onComposePeer}
                className="mt-3 h-7 text-[12px]"
              >
                피어 그룹 만들기
              </Button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function Lane({
  title,
  cards,
  onOpen,
  onConfirm,
  onDelete,
}: {
  title: string;
  cards: CardSummary[];
  onOpen: (id: string) => void;
  onConfirm: (id: string) => void;
  onDelete: (id: string) => void;
}) {
  return (
    <div>
      <SectionTitle title={title} count={cards.length} />
      <Columns
        cards={cards}
        onOpen={onOpen}
        onConfirm={onConfirm}
        onDelete={onDelete}
      />
    </div>
  );
}

function SectionTitle({ title, count }: { title: string; count: number }) {
  return (
    <h2 className="mb-3 flex items-baseline gap-2 border-b pb-1.5 text-[12px] font-semibold">
      {title}
      <span className="font-mono text-[11px] font-normal text-muted-foreground">
        {count}
      </span>
    </h2>
  );
}

function Columns({
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
  return (
    <div className="grid gap-4 md:grid-cols-3">
      {COLUMNS.map((col) => {
        const inCol = cards.filter((c) => c.column === col);
        return (
          <section key={col} className="min-w-0">
            <h3 className="mb-2.5 flex items-baseline gap-1.5 text-[11px] font-semibold uppercase tracking-[0.09em] text-muted-foreground">
              <span className={TONE[col]}>{COLUMN_LABEL[col]}</span>
              <span className="font-mono normal-case tracking-normal">
                {inCol.length}
              </span>
            </h3>
            <p className="mb-2 text-[11.5px] text-muted-foreground">
              {COLUMN_HINT[col]}
            </p>
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
  // **생성 중은 칸이 아니라 카드의 상태다** (D51). 1.5초 머무는 곳은 칸이
  // 아니라 스피너다.
  const running = card.running;
  const blocked = card.attention.length > 0;
  const peer = card.kind === "peer";
  return (
    <div
      className={cn(
        "rounded-lg border bg-card p-2.5",
        blocked && "border-bad/40",
        running && "opacity-70",
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
            {/* **피어 카드는 종목 하나에 매이지 않는다.** 이름이 곧 그룹이다. */}
            {peer && <span className="mr-1 text-num">▤</span>}
            {card.company || card.symbol}
          </span>
          <span className="flex-none font-mono text-[11px] text-muted-foreground">
            {peer ? `${card.member_count}종목` : `FY${card.year}`}
          </span>
        </div>
        <div className="mt-0.5 font-mono text-[11px] text-muted-foreground">
          {peer ? (
            <span>
              {(card.member_symbols ?? []).slice(0, 4).join(" · ")}
              {(card.member_symbols?.length ?? 0) > 4 && " …"}
              {/* 표에 실제로 설 수 있는 종목 수. 나머지는 아직 카드가 없다. */}
              <span className="ml-1.5">
                · 표 <span className="text-num">{card.member_ready ?? 0}</span>/
                {card.member_count}
              </span>
            </span>
          ) : (
            <>
              {card.symbol}
              {running ? (
                <span className="ml-1.5 inline-flex items-center gap-1">
                  ·{" "}
                  <span className="inline-block size-2 animate-spin rounded-full border border-current border-t-transparent" />
                  만드는 중
                </span>
              ) : (
                <span className="ml-1.5">
                  · 수치 <span className="text-num">{card.registry_size}</span>건
                  · 단계 {card.stage_count}
                </span>
              )}
            </>
          )}
        </div>

        {card.attention.map((a, i) => (
          <div
            key={i}
            className="mt-1.5 rounded-md bg-bad/10 px-2 py-1 text-[11.5px]"
          >
            {a}
          </div>
        ))}
      </button>

      {/* 칸을 옮기는 것은 **사람**이다. 열어서 보기 시작하면 검토 중으로. */}
      {card.column === "draft" && !running && (
        <Button
          size="sm"
          variant="outline"
          className="mt-2 h-7 w-full text-[12px]"
          onClick={() => onConfirm(card.id)}
        >
          검토 시작
        </Button>
      )}
      {card.column === "handoff" && (
        <Badge className="mt-1.5 border-transparent bg-ok/15 text-ok">
          ● 넘김
        </Badge>
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
      「확인 필요」는 <b>기계가 이미 아는 것</b>에서만 자동으로 판정합니다 —
      게이트 차단, 검산 불일치, 단계 실패. <b>정상적으로 없는 것</b>(단일 부문
      회사의 부문 손익 등)은 올라오지 않습니다.
    </Hint>
  );
}
