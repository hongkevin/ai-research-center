"""사업 이해 레이어 — "이 회사는 무엇을 하는 회사인가".

왜 이게 최대 갭인가
-------------------
벤치마크(SMIC 15건)와 우리 노트의 차이를 좁히다 보면 분량이 아니라 **출발점**에
닿는다. 우리는 손익계산서에서 출발해 "매출이 늘었다"를 쓴다. 벤치마크는 산업과
사업 구조에서 출발해 "왜 이 회사인가"를 쓴다.

그런데 그 재료가 이미 공시에 있다. 사업보고서 「II. 사업의 내용 → 1. 사업의
개요」는 회사가 직접 쓴 사업 서술이고(파마리서치 6,714자), 제품·핵심기술·
자회사·해외 전략이 전부 여기 있다. 우리는 이걸 한 글자도 안 읽고 있었다.

숫자 취급
---------
원문에는 **등록되지 않은 숫자가 가득하다.** 그대로 프롬프트에 넣으면 LLM이
리터럴로 베끼고 G0가 차단한다. `mask_numbers()`로 가린 뒤 넘긴다 — 탐지와
같은 화이트리스트를 쓰므로 "가렸는데 걸리는" 일이 없다.

즉 이 모듈이 LLM에 주는 것은 **질적 사실**(무엇을 파는가, 어떤 기술인가,
어디에 파는가)이고, 크기는 여전히 레지스트리만 안다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from arc.data.base import Provenance
from arc.data.kr.dart_document import Section
from arc.data.kr.dart_reports import Affiliates, Ownership
from arc.llm.number_registry import NumberEntry, mask_numbers

# 원문 서술에서 잘라낼 상투구. 남겨두면 LLM이 공시 문체를 그대로 흉내 낸다.
_BOILERPLATE = (
    "가. 사업의 개요",
    "나. 사업의 개요",
    "(1) 기업 개요",
    "당사는",
    "동사는",
)

# 섹션 제목이 본문 앞에 붙어 온다. 남기면 LLM이 그걸 문장으로 읽는다.
_LEADING_TITLE_RE = re.compile(r"^\s*\d+\.\s*[^.]{0,20}?(개요|내용)\s*")

# 사업 개요에서 뽑을 신호. 있으면 서술에 쓰고, 없으면 만들지 않는다.
_SIGNALS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("해외 진출", ("해외", "수출", "글로벌", "북미", "유럽", "중국", "일본", "아시아")),
    ("연구개발", ("R&D", "연구개발", "임상", "특허", "파이프라인")),
    ("생산 설비", ("공장", "GMP", "생산라인", "증설", "준공", "가동")),
    ("인허가", ("허가", "인증", "승인", "등록", "FDA", "CE")),
    ("자회사", ("종속회사", "자회사", "계열사", "지분")),
)

_SENTENCE_RE = re.compile(r"(?<=[.!?다])\s+")


@dataclass
class BusinessProfile:
    """사업 개요에서 뽑은 **질적** 프로필. 크기는 담지 않는다."""

    fiscal_year: int
    overview: str = ""  # 숫자가 가려진 사업 서술
    signals: list[str] = field(default_factory=list)  # 감지된 사업 특성
    ownership: Ownership | None = None
    affiliates: Affiliates | None = None
    total_assets: int | None = None  # 출자 비중 판정의 분모
    source_title: str = ""
    note: str = ""

    @property
    def usable(self) -> bool:
        return bool(self.overview)

    @property
    def affiliate_weight(self) -> float | None:
        """출자 장부가 / 자산총계 (%). 사업 구조를 가르는 실질 지표다."""
        a = self.affiliates
        if a is None or not self.total_assets:
            return None
        return a.total_book_value / self.total_assets * 100.0

    @property
    def is_holding_like(self) -> bool:
        """지주·투자 구조인가.

        **건수로 판정하면 안 된다.** 파마리서치는 경영참여 출자가 12건이지만
        장부가는 자산총계의 10%뿐인 사업회사다. 소액 투자조합이 건수를 부풀린다.
        자산의 절반 이상이 출자로 묶여 있어야 밸류에이션 방법(SOTP·PBR)이
        달라지는 구조다.
        """
        w = self.affiliate_weight
        return bool(w is not None and w >= 50.0)


def _clean_overview(raw: str, limit: int = 1800) -> str:
    """원문 → 프롬프트에 넣을 서술.

    `limit`을 두는 이유는 비용이 아니라 **논지 희석**이다. 6,000자를 그대로
    주면 LLM이 요약에만 매달려 재무와 연결하지 못한다. 앞부분에 사업 정의가
    오는 것이 공시 관행이므로 앞에서 자른다.
    """
    text = _LEADING_TITLE_RE.sub("", " ".join(raw.split()))
    for junk in _BOILERPLATE:
        text = text.replace(junk, " ")
    text = " ".join(text.split())
    if len(text) <= limit:
        return mask_numbers(text)
    # 문장 경계에서 자른다 — 중간에서 끊으면 LLM이 잘린 절을 사실로 읽는다
    cut = text[:limit]
    parts = _SENTENCE_RE.split(cut)
    if len(parts) > 1:
        cut = " ".join(parts[:-1])
    return mask_numbers(cut)


def build_business_profile(
    section: Section | None,
    fiscal_year: int,
    *,
    ownership: Ownership | None = None,
    affiliates: Affiliates | None = None,
    total_assets: int | None = None,
) -> BusinessProfile:
    """「사업의 개요」 + 지분·출자 → 사업 프로필."""
    profile = BusinessProfile(
        fiscal_year=fiscal_year,
        ownership=ownership,
        affiliates=affiliates,
        total_assets=total_assets,
    )
    if section is None:
        profile.note = "사업보고서에서 사업 개요 섹션을 찾지 못했다."
        return profile

    profile.source_title = section.title
    body = re.sub(r"<[^>]+>", " ", section.body)
    profile.overview = _clean_overview(body)
    if not profile.overview:
        profile.note = "사업 개요 본문이 비어 있다."
        return profile

    profile.signals = [
        label for label, words in _SIGNALS if any(w in profile.overview for w in words)
    ]
    return profile


def build_business_observations(profile: BusinessProfile) -> list[str]:
    """사업 구조 논지. **크기를 쓰지 않는다** (LLM이 리터럴로 베낀다)."""
    obs: list[str] = []
    if profile.usable:
        obs.append(
            "회사가 공시한 사업 서술이다. 재무 지표를 여기에 연결해 읽어야 하고, "
            "여기 없는 산업·경쟁 정보를 지어내면 안 된다:\n" + profile.overview
        )
        if profile.signals:
            obs.append(f"사업 서술에서 확인되는 축: {', '.join(profile.signals)}.")

    own = profile.ownership
    if own is not None and own.total_stake is not None:
        if own.is_owner_controlled:
            obs.append(
                f"최대주주({own.principal})와 특수관계인의 지분이 회사를 지배하는 수준이다. "
                "배당·승계 같은 지배주주 유인이 실적과 별개로 주가에 작용할 수 있다."
            )
        else:
            obs.append(
                f"최대주주({own.principal}) 지분이 지배적 수준에 못 미친다. "
                "경영권 안정성과 주주 구성 변화가 변수가 될 수 있다."
            )

    aff = profile.affiliates
    if aff is not None and aff.operating:
        names = ", ".join(e.name for e in aff.top(3) if e.is_operating)
        if profile.is_holding_like:
            obs.append(
                f"자산의 상당 부분이 타법인 출자로 묶여 있다({names} 등). 사업회사가 아니라 "
                "지주·투자 구조로 읽어야 하고, 전사 손익만으로 본업을 판단할 수 없다."
            )
        elif names:
            obs.append(
                f"경영참여 출자처: {names}. 연결 실적에 이들이 포함되므로 전사 지표는 "
                "본업과 자회사가 합쳐진 값이다."
            )

    return obs


def build_business_entries(profile: BusinessProfile, prov: Provenance) -> list[NumberEntry]:
    """지분율·출자 비중을 레지스트리에 등록한다.

    지분율도 수치다. 본문에 리터럴로 쓰면 G0가 막는다 — 가정을 리터럴로 썼다가
    막혔던 것과 같은 이유다([D24](../../docs/decisions.md)).
    """
    y = profile.fiscal_year
    out: list[NumberEntry] = []
    own = profile.ownership

    def add(key, value, unit, display, label, formula=None, internal=False):
        if value is None or display is None:
            return
        out.append(
            NumberEntry(
                key=f"{key}_{y}a",
                value=value,
                unit=unit,
                display=display,
                provenance=prov,
                label=f"{label} ({y}A)",
                formula=formula,
                internal=internal,
            )
        )

    if own is not None:
        add(
            "owner_stake",
            own.principal_stake,
            "%",
            f"{own.principal_stake:.2f}%" if own.principal_stake is not None else None,
            "최대주주 지분율",
        )
        add(
            "owner_total_stake",
            own.total_stake,
            "%",
            f"{own.total_stake:.2f}%" if own.total_stake is not None else None,
            "최대주주 및 특수관계인 지분율",
        )

    # 자회사 지분율도 수치다. 표에 리터럴로 넣었다가 G0에 막혔다(실측).
    aff = profile.affiliates
    if aff is not None:
        for i, e in enumerate(x for x in aff.top(5) if x.is_operating):
            add(
                f"affiliate{i + 1}_stake",
                e.stake,
                "%",
                f"{e.stake:.1f}%" if e.stake is not None else None,
                f"{e.name} 지분율",
            )

    w = profile.affiliate_weight
    add(
        "affiliate_weight",
        w,
        "%",
        f"{w:.1f}%" if w is not None else None,
        "타법인 출자 장부가 / 자산총계",
        formula="출자 장부가 합계 / 자산총계",
    )
    return out
