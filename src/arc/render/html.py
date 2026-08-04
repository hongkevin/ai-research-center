"""조립본(마크다운) → HTML. **모든 수치가 출처를 달고 나온다.**

CLI는 `render_text()`로 평문을 만든다. 화면에서는 그것으로 부족하다 —
이 제품의 차별점이 "숫자마다 출처를 클릭해 확인할 수 있다"인데, 평문으로
치환하면 그 정보가 사라진다.

그래서 치환 시점에 값을 `<span>`으로 감싸고 출처를 데이터 속성에 넣는다.
`render_text()`와 **같은 레지스트리, 같은 조사 교정**을 쓰므로 화면과 파일의
숫자가 갈라질 수 없다.

    {{num:revenue_2025a}}  →  <span class="num" data-key="revenue_2025a"
                                 data-formula="…" data-source="…">5,363억원</span>

G0를 통과한 조립본만 이 함수에 들어온다 — 치환 전 검사가 게이트의 전제다
(verify/g0.py 참조).
"""

from __future__ import annotations

import html as _html

from markdown_it import MarkdownIt

from arc.llm.josa import replace_particle
from arc.llm.number_registry import PLACEHOLDER_RE, NumberRegistry


def _attrs(entry) -> str:
    """감사 패널이 읽을 데이터 속성. 값이 없는 항목은 넣지 않는다."""
    prov = entry.provenance
    pairs = [
        ("data-key", entry.key),
        ("data-label", entry.label or entry.key),
        ("data-unit", entry.unit),
        ("data-formula", entry.formula or ""),
        ("data-inputs", ", ".join(entry.inputs)),
        ("data-source", (prov.describe if prov else "")),
        ("data-doc", (prov.source_ref or "") if prov else ""),
        # 사람이 여는 링크와 우리가 호출한 곳을 나눈다 — API 엔드포인트를
        # 링크로 걸면 검토자가 눌러도 아무것도 안 나온다.
        ("data-url", (prov.verify_url or "") if prov else ""),
        ("data-api", (prov.source_url or "") if prov else ""),
        ("data-retrieved", prov.retrieved_at.isoformat(timespec="seconds") if prov else ""),
    ]
    # 파이프는 반드시 엔티티로 바꾼다. YoY 산식이 절댓값을 `|revenue_2024a|`로
    # 쓰는데, 이 문자가 속성 안에 살아 있으면 **마크다운 표의 셀 구분자로
    # 해석돼** 그 뒤 셀이 통째로 이스케이프된 문자열로 렌더된다(실측).
    return " ".join(
        f'{k}="{_html.escape(str(v), quote=True).replace("|", "&#124;")}"' for k, v in pairs if v
    )


def substitute_with_spans(text: str, registry: NumberRegistry) -> str:
    """플레이스홀더 → `<span>`. 조사 교정은 `render_text()`와 동일하게 적용한다."""
    out: list[str] = []
    pos = 0
    for m in PLACEHOLDER_RE.finditer(text):
        if m.start() < pos:
            continue
        out.append(text[pos : m.start()])
        entry = registry._entries.get(m.group(1))
        if entry is None:
            out.append(m.group(0))  # 미등록은 그대로 둔다 (G0가 이미 잡았어야 한다)
            pos = m.end()
            continue
        value = entry.rendered()
        cls = "num num--estimate" if m.group(1).endswith("e") else "num"
        # 키보드로도 출처에 닿아야 한다. "숫자를 누르면 출처가 나온다"가 이
        # 제품의 논증인데, 마우스로만 열리면 키보드 검토자에게는 그 논증이
        # 아예 없는 것과 같다. 마크다운 안의 인라인 요소라 <button>을 쓰면
        # 문단·표 셀 레이아웃이 흔들리므로 span에 역할만 부여한다.
        out.append(
            f'<span class="{cls}" role="button" tabindex="0" '
            f"{_attrs(entry)}>{_html.escape(value)}</span>"
        )
        particle, consumed = replace_particle(value, text[m.end() : m.end() + 3])
        out.append(particle)
        pos = m.end() + consumed
    out.append(text[pos:])
    return "".join(out)


def render_html(assembled: str, registry: NumberRegistry) -> str:
    """조립본 → 본문 HTML.

    `html=True`가 필요하다 — 치환으로 넣은 `<span>`이 이스케이프되면
    출처가 화면에 문자열로 튀어나온다.
    """
    md = MarkdownIt("commonmark", {"html": True, "linkify": False}).enable("table")
    return md.render(substitute_with_spans(assembled, registry))


def binding_rows(result) -> list[dict]:
    """감사 패널용 바인딩 목록. 등장 순서를 유지한다."""
    seen: set[str] = set()
    rows: list[dict] = []
    for key in registry_keys(result):
        if key in seen:
            continue
        seen.add(key)
        entry = result.registry._entries.get(key)
        if entry is None:
            continue
        prov = entry.provenance
        rows.append(
            {
                "key": entry.key,
                "label": entry.label or entry.key,
                "value": entry.rendered(),
                "formula": entry.formula,
                "inputs": entry.inputs,
                "source": prov.describe if prov else "",
                "document": (prov.source_ref or "") if prov else "",
                "url": (prov.verify_url or "") if prov else "",
                "api": (prov.source_url or "") if prov else "",
                "internal": entry.internal,
            }
        )
    return rows


def registry_keys(result) -> list[str]:
    return NumberRegistry.extract_keys(result.assembled)
