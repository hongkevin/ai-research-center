"""상시 맥락 — **이 사람이 늘 들고 있는 것** (D84).

왜 필요한가
-----------
요구가 그대로였다: *"개인이 가지고 가는 커버리지/종목, 이에 따른 브리프, 시장
센티는 물론이고, 리포트와 또 질문을 주고 받은 대화까지 다 컨텍스트로
가져가야한다."*

지금 `/api/ask`는 **카드만** 본다. 그래서 세 가지를 못 한다:

* *"우리 섹터 어때?"* — 「우리」가 누구인지 모른다
* *"조선 쪽 실적"* — 조선에서 이 사람이 무엇을 보는지 모르니 카드를 못 고른다
* 같은 것을 세 번째 묻는데 **앞에 답이 부족했다는 것을 모른다**

무엇이 근거이고 무엇이 배경인가
-------------------------------
이 절은 **근거가 아니다.** 이 제품의 전제는 *"주어진 근거만으로 답하고 문장마다
출처를 붙인다"*이고, 「당신은 한화오션을 커버합니다」는 출처를 붙일 수 있는
종류가 아니다. 그래서 프롬프트에서 **질문의 배경**으로만 넣고, 시스템 규칙이
*"여기서 사실을 끌어오지 마라"*고 못 박는다.

배경이 하는 일은 하나다 — **무엇이 관련 있는지 판단하는 것.** 사실은 여전히
카드에서만 온다.

개수를 넣지 않는다
------------------
실측으로 확인한 제약이다. 종목코드(`042660`)는 G0 화이트리스트를 통과하지만
**개수는 막힌다**:

    "커버 12종목 · 관심 4종목"  → 막힘 ['12', '4']
    "지난달 3번 물었습니다"      → 막힘 ['3']

LLM이 배경을 되뱉으면 그 문장이 버려진다. 그래서 **이름만 넣고 개수는 안
넣는다.** 「많다/적다」도 안 쓴다 — 그건 판단이고 근거가 없다.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

# 프롬프트에 세울 상한. **다 넣지 않는다** — 커버 30종목을 나열하면 배경이
# 근거보다 길어지고, 긴 배경은 모델이 그중 하나를 사실로 집어 오게 만든다.
MAX_NAMES = 8
MAX_ASKED = 3

# 「또 물었다」의 문턱. 한 번은 그냥 질문이고, **두 번이면 앞의 답이 부족했다.**
REPEAT_AT = 2


@dataclass
class Standing:
    """이 사람의 배경. **전부 이름이고 개수가 없다.**"""

    sectors: list[str] = field(default_factory=list)
    # (종목코드, 회사명). 커버는 「리포트를 낸다」, 관심은 「옆에서 본다」
    covers: list[tuple[str, str]] = field(default_factory=list)
    watches: list[tuple[str, str]] = field(default_factory=list)
    # 이 주제를 전에도 물었다 — **가려진 질문 문장**이다 (D77)
    asked_before: list[str] = field(default_factory=list)
    # 요즘 자주 열어 본 것. 회사명
    focus: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.sectors or self.covers or self.watches or self.asked_before)

    def symbols(self) -> list[str]:
        """내가 보는 종목 전부. **카드 고르기가 이걸 쓴다.**"""
        return [s for s, _ in self.covers] + [s for s, _ in self.watches]


def build_standing(profile, events=None, *, subject: str = "") -> Standing:
    """프로필 + 사건 → 배경. **순수 함수다.**

    `subject`를 주면 「그 종목을 전에도 물었나」만 센다. 안 주면 최근에 반복된
    것을 아무거나 끌어오게 되는데, 그건 지금 질문과 상관없는 잡음이다.
    """
    out = Standing(sectors=[x for x in getattr(profile, "sectors", []) if x])
    for stock in getattr(profile, "stocks", []):
        pair = (stock.symbol, stock.company or stock.symbol)
        (out.covers if stock.kind == "cover" else out.watches).append(pair)

    if events is None:
        return out

    rows = events if isinstance(events, list) else []
    # **같은 것을 또 물었나.** 대상으로 센다 — 문장은 매번 다르지만 「또 이걸
    # 묻고 있다」가 신호다 (D77의 `repeated`와 같은 규칙).
    if subject:
        asked = [e for e in rows if e.kind == "asked" and e.subject == subject and e.text]
        if len(asked) >= REPEAT_AT:
            out.asked_before = [e.text for e in asked[:MAX_ASKED]]

    opened = Counter(e.subject for e in rows if e.kind == "opened" and e.subject)
    known = {s: c for s, c in out.covers + out.watches}
    out.focus = [known.get(s, s) for s, _ in opened.most_common(MAX_NAMES) if s in known]
    return out


def standing_prompt(standing: Standing) -> str:
    """배경을 프롬프트 절로. **비면 빈 문자열** — 빈 절을 넣으면 모델이
    그 자리를 뭔가로 채우려 한다.

    개수·비율·「많다」를 쓰지 않는다. 이름과 사실만이다.
    """
    if standing.empty:
        return ""

    lines = ["# 질문하는 사람의 배경 (근거가 아닙니다 — 여기서 사실을 끌어오지 마십시오)"]
    if standing.sectors:
        lines.append(f"- 담당 섹터: {' · '.join(standing.sectors)}")
    if standing.covers:
        lines.append(
            "- 리포트를 내는 종목: "
            + " · ".join(f"{name}({sym})" for sym, name in standing.covers[:MAX_NAMES])
        )
    if standing.watches:
        lines.append(
            "- 옆에서 보는 종목: "
            + " · ".join(f"{name}({sym})" for sym, name in standing.watches[:MAX_NAMES])
        )
    if standing.focus:
        lines.append(f"- 요즘 자주 열어 본 것: {' · '.join(standing.focus[:MAX_NAMES])}")

    if standing.asked_before:
        # **앞의 답이 부족했다는 뜻이다.** 같은 말을 다시 하지 말라고 적는다 —
        # 지어내라는 뜻이 아니고, 근거가 없으면 그것도 그대로 말하는 것이다.
        lines.append("")
        lines.append("## 같은 주제를 전에도 물었습니다 (수치는 가려져 있습니다)")
        lines.extend(f"- {q}" for q in standing.asked_before)
        lines.append(
            "앞의 답이 충분하지 않았을 수 있습니다. **같은 문장을 되풀이하지 마십시오** — "
            "근거에 더 있으면 그것을 쓰고, 없으면 없다고 그대로 말하십시오."
        )
    lines.append("")
    return "\n".join(lines)
