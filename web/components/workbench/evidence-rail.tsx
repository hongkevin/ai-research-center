"use client";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import type { Heading } from "@/components/note/note-body";
import { Hint, KeyValue, SectionLabel } from "@/components/workbench/section-label";
import type { ViewModel } from "@/lib/api";

/**
 * 오른쪽 열 — 근거.
 *
 * **접어두지 않는다.** 이 제품이 파는 것은 "글"이 아니라 "검증된 글"이라,
 * 게이트 결과와 수치 출처가 화면에 상시로 있어야 논증이 성립한다
 * (index.html:3-4의 구성 원칙).
 */
export function EvidenceRail({ vm, headings }: { vm: ViewModel; headings: Heading[] }) {
  return (
    <div className="space-y-8">
      {vm.gate_passed && headings.length > 0 && (
        <section>
          <SectionLabel>목차</SectionLabel>
          <Card>
            <CardContent className="py-4">
              <nav className="text-[12.5px] leading-[1.9]">
                {headings.map((h) => (
                  <a
                    key={h.id}
                    href={`#${h.id}`}
                    onClick={(e) => {
                      e.preventDefault();
                      document
                        .getElementById(h.id)
                        ?.scrollIntoView({ behavior: "smooth", block: "start" });
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
        </section>
      )}

      <section>
        <SectionLabel>발간 게이트</SectionLabel>
        <Card>
          <CardContent className="py-4">
            {vm.gate_passed ? (
              <>
                <Badge className="bg-ok/15 text-ok border-transparent">● G0 통과</Badge>
                <Hint>
                  수치 정합성 · 컴플라이언스 · 필수 섹션 · 3중 디스클레이머를 모두 만족합니다.
                </Hint>
              </>
            ) : (
              <>
                <Badge className="bg-bad/15 text-bad border-transparent">
                  ● 차단 {vm.violations.length}건
                </Badge>
                <div className="mt-2.5 space-y-1.5">
                  {vm.violations.map((v, i) => (
                    <div key={i} className="rounded-md bg-bad/10 px-2.5 py-1.5 text-[12.5px]">
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
      </section>

      {vm.assumptions.length > 0 && (
        <section>
          <SectionLabel>추정 가정</SectionLabel>
          <Card>
            <CardContent className="py-4">
              {vm.assumptions.map((a) => (
                <div key={a.key} className="mb-1.5">
                  <KeyValue
                    label={
                      <>
                        {a.label}
                        {a.override && (
                          <Badge className="ml-1.5 bg-warn/15 text-warn border-transparent px-1.5 py-0 text-[10px]">
                            입력
                          </Badge>
                        )}
                      </>
                    }
                  >
                    {a.value}
                    {a.unit}
                  </KeyValue>
                  <Hint>{a.basis}</Hint>
                </div>
              ))}
              {vm.estimate_warnings.map((w, i) => (
                <Alert key={i} className="mt-2 bg-warn/10 border-transparent py-2">
                  <AlertDescription className="text-[12.5px]">{w}</AlertDescription>
                </Alert>
              ))}
            </CardContent>
          </Card>
        </section>
      )}

      {vm.revisions.length > 0 && (
        <section>
          <SectionLabel>추정 변화</SectionLabel>
          <Card>
            <CardContent className="py-4">
              {vm.revisions.map((r, i) => (
                <div key={i} className="flex justify-between text-[12.5px] py-1">
                  <span>{r.label}</span>
                  <span
                    className={
                      r.direction === "하향" ? "text-bad" : r.direction === "상향" ? "text-ok" : ""
                    }
                  >
                    {r.direction} {r.change}%
                  </span>
                </div>
              ))}
              <Hint>
                직전 발간 대비. 조정 방향과 시점은 추정치 자체만큼 중요한 기록입니다.
              </Hint>
            </CardContent>
          </Card>
        </section>
      )}

      {vm.bindings.length > 0 && (
        <section>
          <SectionLabel>수치 출처 {vm.bindings.length}건</SectionLabel>
          <Card>
            <CardContent className="py-4">
              {vm.bindings.map((b) => (
                <div key={b.key} className="border-b py-1.5 text-[12px] last:border-b-0">
                  <div className="flex justify-between gap-2">
                    <b className="font-semibold">{b.label}</b>
                    <span>{b.value}</span>
                  </div>
                  {b.formula && (
                    <div className="font-mono text-[11px] text-muted-foreground">{b.formula}</div>
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
        </section>
      )}
    </div>
  );
}
