"""**순서는 크기가 아니다** — 분석 레인이 말을 할 수 있게 하는 최소 재료.

왜 필요한가
-----------
실측에서 "부문별로 어디가 제일 많이 벌어?"에 이 시스템이 답하지 못했다. 근거는
있었다 — 부문 손익 표를 통째로 줬다. **LLM이 값을 볼 수 없어서 비교를 못 한
것이다.** 불변식 1을 지키는 한 이건 모델을 바꿔서 풀리는 문제가 아니다.

[D15](../../../docs/decisions.md#d15)·[D16](../../../docs/decisions.md#d16)이
같은 자리에서 같은 답을 냈다. 방향은 결정적 코드가 부호에서 뽑은 사실이라
환각이 아니고, 크기는 여전히 노출되지 않는다. **순위도 같은 성질이다** —
정렬은 값을 읽어야 하지만 그 결과는 순서일 뿐 크기가 아니다.

계열을 어떻게 찾는가
--------------------
키에 붙은 일련번호만 뺀 나머지가 같으면 같은 계열이다.

    opseg1_revenue_2026a ┐
    opseg2_revenue_2026a ├→ opseg#_revenue_2026a
    opseg3_revenue_2026a ┘

연도는 계열이 아니다(`revenue_2026a`의 2026 뒤에는 `_`가 없다). 부문이 하나면
계열이 아니므로 아무 말도 하지 않는다.
"""

from __future__ import annotations

import re

from arc.llm.number_registry import NumberRegistry

# 키 한가운데의 일련번호. 뒤에 `_`가 와야 한다 — 연도(`_2026a`)를 잡지 않는다.
_INDEX_RE = re.compile(r"(?<=[a-z])\d+(?=_)")

# 계열이 너무 길면 프롬프트를 지배한다. 리서치에서 의미 있는 계열은 부문·자회사
# 수준이라 이 정도면 충분하다.
_MAX_MEMBERS = 8


def family_of(key: str) -> str:
    return _INDEX_RE.sub("#", key)


def rank_observations(
    registry: NumberRegistry, keys: list[str], *, max_families: int = 6
) -> list[str]:
    """같은 계열 항목들의 **크기 순서**. 크기는 한 자도 나가지 않는다."""
    families: dict[str, list[str]] = {}
    for key in keys:
        entry = registry._entries.get(key)
        if entry is None or entry.internal:
            continue
        families.setdefault(family_of(key), []).append(key)

    out: list[str] = []
    for members in families.values():
        if not 2 <= len(members) <= _MAX_MEMBERS:
            continue
        entries = [registry._entries[k] for k in members]
        if len({e.unit for e in entries}) != 1:
            continue  # 단위가 섞이면 비교가 아니다
        entries.sort(key=lambda e: e.value, reverse=True)
        names = [f"「{e.label or e.key}」{'(음수)' if e.value < 0 else ''}" for e in entries]
        out.append(f"{' > '.join(names)} — 크기 **순서**다. 크기 자체는 주어지지 않았다.")
        if len(out) >= max_families:
            break
    return out
