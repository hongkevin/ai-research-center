"use client";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import type { Heading } from "@/components/note/note-body";
import { Assumptions } from "@/components/workbench/assumptions";
import { RailSection } from "@/components/workbench/rail-section";
import { Hint } from "@/components/workbench/section-label";
import { RevisionHistory } from "@/components/workbench/revision-history";
import type { Revision, ViewModel } from "@/lib/api";

/**
 * 오른쪽 열 — 근거.
 *
 * **접어두지 않는다.** 이 제품이 파는 것은 "글"이 아니라 "검증된 글"이라,
 * 게이트 결과와 수치 출처가 화면에 상시로 있어야 논증이 성립한다
 * (index.html:3-4의 구성 원칙).
 */
export function EvidenceRail({
  vm,
  headings,
  versions,
  cardId,
  onRecomputed,
}: {
  vm: ViewModel;
  headings: Heading[];
  versions?: Revision[];
  cardId?: string;
  onRecomputed?: () => void;
}) {
  const blocked = !vm.gate_passed;
  return (
    <div className="space-y-1">
      {vm.gate_passed && headings.length > 0 && (
        <RailSection title="목차" count={headings.length}>
          <Card>
            <CardContent className="py-3">
              <nav className="text-[12.5px] leading-[1.9]">
                {headings.map((h) => (
                  <a
                    key={h.id}
                    href={`#${h.id}`}
                    onClick={(e) => {
                      e.preventDefault();
                      document
                        .getElementById(h.id)
                        ?.scrollIntoView({
                          behavior: "smooth",
                          block: "start",
                        });
                    }}
                    className={`block text-muted-foreground hover:text-foreground ${
                      h.level === 3 ? "pl-3 text-[12px] opacity-80" : ""
                    }`}
                  >
                    {h.text}
                  </a>
                ))}
              </nav>
            </CardContent>
          </Card>
        </RailSection>
      )}

      {/* **RA가 새 분기 노트를 열고 가장 먼저 묻는 것** — 지난번 내 노트에서
          뭐가 바뀌었지. 목차 바로 밑에 둔다. */}
      {vm.changes.length > 0 && (
        <RailSection
          title="직전 노트 대비"
          count={vm.changes.length}
          defaultOpen
        >
          <Card>
            <CardContent className="py-3">
              <Hint>{vm.changes_basis}</Hint>
              <table className="mt-2 w-full text-[12px]">
                <tbody>
                  {vm.changes.map((c) => (
                    <tr key={c.name} className="border-b last:border-b-0">
                      <td className="py-1 pr-2 align-top">
                        {c.direction === "변경" && (
                          <Badge className="mr-1 border-transparent bg-bad/15 px-1 py-0 text-[10px] text-bad">
                            변경
                          </Badge>
                        )}
                        {c.name}
                      </td>
                      <td className="py-1 text-right align-top font-mono text-[11.5px] whitespace-nowrap text-muted-foreground">
                        {c.previous}
                      </td>
                      <td className="py-1 pl-1.5 text-right align-top font-mono text-[11.5px] whitespace-nowrap text-num">
                        {c.current}
                      </td>
                      <td
                        className={`w-14 py-1 pl-2 text-right align-top font-mono text-[11.5px] whitespace-nowrap ${
                          c.direction === "증가" ? "text-ok" : "text-bad"
                        }`}
                      >
                        {c.change}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </CardContent>
          </Card>
        </RailSection>
      )}

      {versions !== undefined && <RevisionHistory versions={versions} />}

      <RailSection
        title="발간 게이트"
        count={blocked ? `차단 ${vm.violations.length}` : "통과"}
        defaultOpen={blocked}
        tone={blocked ? "text-bad" : "text-ok"}
      >
        <Card>
          <CardContent className="py-3">
            {vm.gate_passed ? (
              <>
                <Badge className="bg-ok/15 text-ok border-transparent">
                  ● G0 통과
                </Badge>
                <Hint>
                  수치 정합성 · 컴플라이언스 · 필수 섹션 · 3중 디스클레이머를
                  모두 만족합니다.
                </Hint>
              </>
            ) : (
              <>
                <Badge className="bg-bad/15 text-bad border-transparent">
                  ● 차단 {vm.violations.length}건
                </Badge>
                <div className="mt-2.5 space-y-1.5">
                  {vm.violations.map((v, i) => (
                    <div
                      key={i}
                      className="rounded-md bg-bad/10 px-2.5 py-1.5 text-[12.5px]"
                    >
                      <b className="text-bad">{v.rule}</b>
                      {v.line ? ` · line ${v.line}` : ""}
                      <br />
                      {v.detail}
                    </div>
                  ))}
                </div>
              </>
            )}
          </CardContent>
        </Card>
      </RailSection>

      {/* 편집은 Assumptions가 맡는다 — 기준선과 근거를 보여준 다음에 고친다. */}
      {cardId && (
        <Assumptions
          cardId={cardId}
          years={vm.estimate_years ?? []}
          onRecomputed={onRecomputed ?? (() => {})}
        />
      )}

      {vm.revisions.length > 0 && (
        <RailSection title="추정 변화" count={vm.revisions.length}>
          <Card>
            <CardContent className="py-3">
              {vm.revisions.map((r, i) => (
                <div
                  key={i}
                  className="flex justify-between text-[12.5px] py-1"
                >
                  <span>{r.label}</span>
                  <span
                    className={
                      r.direction === "하향"
                        ? "text-bad"
                        : r.direction === "상향"
                          ? "text-ok"
                          : ""
                    }
                  >
                    {r.direction} {r.change}%
                  </span>
                </div>
              ))}
              <Hint>
                직전 발간 대비. 조정 방향과 시점은 추정치 자체만큼 중요한
                기록입니다.
              </Hint>
            </CardContent>
          </Card>
        </RailSection>
      )}

      {vm.bindings.length > 0 && (
        <RailSection title="수치 출처" count={vm.bindings.length}>
          <Card>
            <CardContent className="py-3">
              {vm.bindings.map((b) => (
                <div
                  key={b.key}
                  className="border-b py-1.5 text-[12px] last:border-b-0"
                >
                  <div className="flex justify-between gap-2">
                    <b className="font-semibold">{b.label}</b>
                    <span>{b.value}</span>
                  </div>
                  {b.formula && (
                    <div className="font-mono text-[11px] text-muted-foreground">
                      {b.formula}
                    </div>
                  )}
                  {b.document && (
                    <div className="font-mono text-[11px] text-muted-foreground">
                      {b.source} · {b.document}
                    </div>
                  )}
                </div>
              ))}
            </CardContent>
          </Card>
        </RailSection>
      )}
    </div>
  );
}
