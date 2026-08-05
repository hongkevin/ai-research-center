"""코멘트를 받아 **한 섹션만** 다시 쓴다.

왜 별도 호출인가
----------------
`narrate()`는 문서 전체를 한 번에 만든다. 리뷰 루프에서는 그러면 안 된다 —
코멘트 하나 때문에 손대지 않은 문단까지 바뀌면 diff가 의미를 잃고, 검토자는
무엇이 왜 바뀌었는지 알 수 없다. 고친 자리만 고쳐야 diff가 논증이 된다.

왜 이 루프가 안전한가
---------------------
LLM은 `{{num:key}}` 플레이스홀더만 쓰고 **값은 프롬프트에 들어가지도 않는다**
(`narrate.py`와 같은 규칙, 같은 `catalog()`). 그래서 이 호출이 문서를 고쳐도
**숫자는 구조적으로 바뀔 수 없다.** diff는 문장에만 생긴다 — 약속이 아니라
구조다.

고친 결과는 **반드시 G0를 다시 통과해야** 채택할 수 있다. 게이트를 건너뛰면
리뷰 루프가 불변식을 우회하는 뒷문이 된다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from arc.llm.client import LLMClient, Tier
from arc.llm.number_registry import PLACEHOLDER_RE, NumberRegistry

SYSTEM_PROMPT = """\
당신은 한국 증권사 리서치센터의 애널리스트입니다. **이미 쓰인 실적 리뷰 노트의
한 섹션**을, 검토자가 남긴 코멘트에 따라 고쳐 씁니다.

## 절대 규칙 — 숫자

- **숫자를 직접 쓰지 마십시오.** 아라비아 숫자를 문장에 넣을 수 없습니다.
- 수치가 필요하면 **반드시 `{{num:키}}` 플레이스홀더**를 씁니다. 카탈로그에
  있는 키만 쓸 수 있습니다. 값은 제공되지 않으며 추측해서도 안 됩니다.
- 원문에 이미 있던 플레이스홀더는 **그대로 두는 것이 기본**입니다. 코멘트가
  요구하지 않는 한 빼지 마십시오.

## 절대 규칙 — 투자의견

- 목표주가·투자의견(매수/중립/비중확대 등)을 **부정문으로도** 쓰지 마십시오.

## 고쳐 쓰는 법

- **코멘트가 요구한 것만 고칩니다.** 나머지 문장은 가능한 한 원문 그대로
  두십시오 — 검토자가 무엇이 바뀌었는지 보아야 합니다.
- 두괄식을 유지합니다. 문단의 첫 문장이 결론입니다.
- "추가 확인이 필요하다" 류의 실사 보고서 어투를 쓰지 마십시오.
- 코멘트가 근거 없는 주장을 요구하면 **따르지 말고**, 카탈로그가 뒷받침하는
  선까지만 고치십시오.

## 출력

고쳐 쓴 섹션 본문만 출력하십시오. 설명·머리말·코드펜스를 붙이지 마십시오.
"""


# 고칠 수 없는 섹션. 템플릿이 만들고 규칙이 지키는 자리라 LLM이 손대면 안 된다.
#   · 디스클레이머 — 3중 고지가 G0의 발간 조건이다
#   · 수치 출처 — 레지스트리에서 자동 생성된다 (D36)
#   · 작성 기준 — 우리 자료의 한계를 밝히는 자리
#   · 산업 배경 — 미검증 레인. 숫자가 하나라도 있으면 버린다 (D31)
# 공시 밖 레인은 고쳐 쓰지 않는다 — 검산할 수 없는 문단을 LLM이 또 만지면
# 무엇이 어디서 왔는지 추적이 끊긴다.
_LOCKED = ("디스클레이머", "수치 출처", "작성 기준", "공시 밖 배경", "산업 구조", "최근 이슈")

_HEADING_RE = re.compile(r"^(#{2,3})\s+(.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class DocSection:
    """조립본의 `##` 섹션 하나."""

    title: str
    text: str  # 제목 아래 본문 (플레이스홀더 살아 있음)
    start: int  # 조립본에서의 본문 시작 위치
    end: int
    editable: bool


def split_sections(assembled: str) -> list[DocSection]:
    """조립본을 `##` 제목으로 자른다.

    제목을 주소로 쓴다 — 인덱스는 문서가 바뀌면 어긋나고, `sections` 딕트는
    조립 뒤에 남지 않는다. 제목은 카드 안에서 안정적이고 사람이 읽을 수 있다.
    """
    heads = [m for m in _HEADING_RE.finditer(assembled) if m.group(1) == "##"]
    out: list[DocSection] = []
    for i, m in enumerate(heads):
        start = m.end()
        end = heads[i + 1].start() if i + 1 < len(heads) else len(assembled)
        title = m.group(2).strip()
        out.append(
            DocSection(
                title=title,
                text=assembled[start:end].strip("\n"),
                start=start,
                end=end,
                editable=not any(k in title for k in _LOCKED),
            )
        )
    return out


def find_section(assembled: str, title: str) -> DocSection | None:
    return next((s for s in split_sections(assembled) if s.title == title), None)


def splice(assembled: str, section: DocSection, after: str) -> str:
    """고친 본문을 조립본에 되붙인다. 앞뒤 개행을 보존한다."""
    return assembled[: section.start] + "\n\n" + after.strip() + "\n\n" + assembled[section.end :]


@dataclass
class RevisionProposal:
    """제안된 수정 1건. **아직 채택되지 않았다.**"""

    section: str
    comment: str
    before: str
    after: str
    used_llm: bool = False
    model: str = ""
    cost_usd: float | None = None
    problems: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.after.strip() != self.before.strip()

    @property
    def numbers_before(self) -> list[str]:
        return sorted(set(PLACEHOLDER_RE.findall(self.before)))

    @property
    def numbers_after(self) -> list[str]:
        return sorted(set(PLACEHOLDER_RE.findall(self.after)))

    @property
    def numbers_unchanged(self) -> bool:
        """**이 루프의 핵심 보장.** 문장은 바뀌어도 수치는 그대로다."""
        return self.numbers_before == self.numbers_after


def _strip_fence(text: str) -> str:
    """모델이 코드펜스를 붙이면 벗긴다. 지시해도 가끔 붙인다."""
    t = text.strip()
    m = re.fullmatch(r"```[a-zA-Z]*\n(.*?)\n?```", t, re.DOTALL)
    return m.group(1).strip() if m else t


def build_prompt(*, section_label: str, before: str, comment: str, registry: NumberRegistry) -> str:
    """카탈로그(크기 없음) + 원문 + 코멘트."""
    lines = []
    for e in registry.catalog():
        d = f", 방향: {e['direction']}" if e["direction"] != "-" else ""
        lines.append(f"- {{{{num:{e['key']}}}}} — {e['label']} (단위: {e['unit']}{d})")
    return (
        f"# 고칠 섹션\n{section_label}\n\n"
        f"# 현재 본문\n{before}\n\n"
        f"# 검토자 코멘트\n{comment}\n\n"
        "# 수치 카탈로그 (크기는 제공하지 않습니다. 키만 쓰십시오)\n" + "\n".join(lines)
    )


def revise_section(
    client: LLMClient,
    *,
    section: str,
    section_label: str,
    before: str,
    comment: str,
    registry: NumberRegistry,
    max_tokens: int = 2048,
) -> RevisionProposal:
    """코멘트대로 한 섹션을 고쳐 쓴다. **채택은 별개다.**

    실패하면 원문을 그대로 담아 돌려준다 — 리뷰 루프가 죽는 것보다 "바뀐 것이
    없다"가 낫다.
    """
    p = RevisionProposal(section=section, comment=comment, before=before, after=before)
    try:
        c = client.complete(
            system=SYSTEM_PROMPT,
            user=build_prompt(
                section_label=section_label, before=before, comment=comment, registry=registry
            ),
            tier=Tier.WRITE,
            max_tokens=max_tokens,
        )
    except Exception as exc:  # noqa: BLE001 — 원인을 화면에 보여주는 게 목적이다
        p.problems.append(f"{type(exc).__name__}: {exc}")
        return p

    after = _strip_fence(c.text)
    if not after:
        p.problems.append("모델이 빈 응답을 냈습니다.")
        return p

    p.after = after
    p.used_llm = True
    p.model = c.model
    p.cost_usd = c.cost_usd

    # 카탈로그 밖의 키를 만들어 쓰면 치환이 안 되고 G0가 막는다. 미리 알린다.
    unknown = [k for k in PLACEHOLDER_RE.findall(after) if k not in registry]
    if unknown:
        p.problems.append(f"카탈로그에 없는 키: {', '.join(sorted(set(unknown)))}")
    if not p.numbers_unchanged:
        added = set(p.numbers_after) - set(p.numbers_before)
        removed = set(p.numbers_before) - set(p.numbers_after)
        bits = []
        if added:
            bits.append(f"추가 {', '.join(sorted(added))}")
        if removed:
            bits.append(f"제거 {', '.join(sorted(removed))}")
        p.problems.append("수치 구성이 바뀌었습니다 — " + " · ".join(bits))
    return p
