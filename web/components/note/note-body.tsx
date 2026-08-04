"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { Popover, PopoverContent } from "@/components/ui/popover";

/**
 * 노트 본문 + 수치 출처 팝오버.
 *
 * **본문 HTML은 서버가 만든다.** `render/html.py`의 `render_html()`이 수치마다
 * `<span class="num" data-key … data-url>`을 붙여 내보내고, 여기서는 그것을
 * 그대로 주입한다. React가 숫자를 다시 포맷하면 레지스트리를 거치는 경로가
 * 둘이 되어 제품의 불변식이 깨진다 — 그러면 G0가 지키는 것이 화면에서 무너진다.
 *
 * 그래서 `dangerouslySetInnerHTML`을 쓴다. 값은 서버에서 이미 이스케이프된다
 * (`tests/test_web.py::test_value_is_html_escaped`).
 *
 * 팝오버는 클릭한 `<span>` **자체**를 앵커로 넘긴다. Base UI의 Positioner가
 * 엘리먼트를 직접 받으므로, 화면 밖으로 나갈 때 뒤집는 계산까지 맡길 수 있다 —
 * 원래 구현이 손으로 하던 일이다(index.html:456-462).
 */

interface NumberInfo {
  label: string;
  value: string;
  key: string;
  formula: string;
  inputs: string;
  source: string;
  doc: string;
  api: string;
  retrieved: string;
  url: string;
}

export interface Heading {
  id: string;
  text: string;
  level: number;
}

export function NoteBody({
  html,
  onHeadings,
}: {
  html: string;
  onHeadings?: (headings: Heading[]) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [info, setInfo] = useState<NumberInfo | null>(null);
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);

  // 목차는 **렌더된 결과에서** 뽑는다. 서버가 섹션 목록을 따로 들고 있으면
  // 템플릿과 어긋날 수 있다 (index.html:353-354의 판단을 그대로 가져왔다).
  useEffect(() => {
    const root = ref.current;
    if (!root || !onHeadings) return;
    const found: Heading[] = [];
    root.querySelectorAll("h2, h3").forEach((h, i) => {
      const id = `sec-${i}`;
      h.id = id;
      found.push({ id, text: h.textContent ?? "", level: h.tagName === "H3" ? 3 : 2 });
    });
    onHeadings(found);
  }, [html, onHeadings]);

  // 위임으로 붙인다 — 본문이 통째로 바뀌어도 핸들러를 다시 달 필요가 없다.
  const onClick = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    const el = (e.target as HTMLElement).closest<HTMLElement>(".num");
    if (!el) {
      setInfo(null);
      return;
    }
    const d = el.dataset;
    setAnchor(el);
    setInfo({
      label: d.label ?? d.key ?? "",
      value: el.textContent ?? "",
      key: d.key ?? "",
      formula: d.formula ?? "",
      inputs: d.inputs ?? "",
      source: d.source ?? "",
      doc: d.doc ?? "",
      api: d.api ?? "",
      retrieved: d.retrieved ?? "",
      url: d.url ?? "",
    });
  }, []);

  return (
    <>
      <div
        ref={ref}
        className="note"
        onClick={onClick}
        dangerouslySetInnerHTML={{ __html: html }}
      />

      <Popover open={info !== null} onOpenChange={(o) => !o && setInfo(null)}>
        <PopoverContent
          anchor={anchor}
          align="start"
          className="w-[380px] max-w-[calc(100vw-2rem)] text-xs"
        >
          {info && <NumberDetail info={info} />}
        </PopoverContent>
      </Popover>
    </>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <>
      <dt className="text-muted-foreground font-normal">{label}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </>
  );
}

function NumberDetail({ info }: { info: NumberInfo }) {
  return (
    <div>
      <div className="font-semibold text-[13.5px]">{info.label}</div>
      <div className="text-[15px] font-semibold mt-0.5">{info.value}</div>

      <dl className="grid grid-cols-[62px_1fr] gap-x-2.5 gap-y-1 mt-2.5">
        <Row label="키">
          <code className="font-mono text-[11.5px]">{info.key}</code>
        </Row>
        {info.formula && (
          <Row label="산식">
            <code className="font-mono text-[11.5px]">{info.formula}</code>
          </Row>
        )}
        {info.inputs && (
          <Row label="입력">
            <code className="font-mono text-[11.5px]">{info.inputs}</code>
          </Row>
        )}
        {info.source && <Row label="출처">{info.source}</Row>}
        {info.doc && (
          <Row label="공시">
            <code className="font-mono text-[11.5px]">{info.doc}</code>
          </Row>
        )}
        {/* 원래 화면은 조회 경로와 조회 시각에 **같은 이름**을 붙였다
            (index.html:451-452). 두 줄이 "조회"로 보여 구분되지 않았다. */}
        {info.api && (
          <Row label="조회 경로">
            <code className="font-mono text-[11.5px]">{info.api}</code>
          </Row>
        )}
        {info.retrieved && <Row label="조회 시각">{info.retrieved}</Row>}
      </dl>

      {info.url && (
        <a
          href={info.url}
          target="_blank"
          rel="noopener"
          className="mt-2 inline-block text-num hover:underline"
        >
          원문 공시 열기 →
        </a>
      )}
    </div>
  );
}
