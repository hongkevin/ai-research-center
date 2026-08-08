"""시세·공시 레인 — **리포트가 없어도 답한다** (D86).

왜 필요한가
-----------
인터뷰가 준 가장 큰 발견은 이것이었다: 병목은 리포트가 아니라 **클라이언트
리퀘스트**다 — 하루 10~15건, 시간 단위 청구. 그런데 채팅은 **리포트 카드만**
근거로 쳤다. 그래서 실측으로:

    질문: 신한지주 실적 어때요?
    답:   확인할 수 있는 근거가 없습니다. 근거로 쓸 수 있는 카드가 없습니다.

커버 20~30종목의 리포트를 다 쓰기 전까지 위젯이 쓸모없다는 뜻이고, **클라이언트는
기다려 주지 않는다.**

그런데 *"왜 올랐어요?"* 에 답할 재료는 이미 손에 있었다. 같은 종목에 대해:

    등락  1일 +0.45% · 5일 +2.45% · 1개월 +6.18% · 1년 +35.90%
    공시  5건 (최근 7일)

**채팅만 그걸 못 봤다.**

불변식 1은 안 깨진다
--------------------
값이 프롬프트에 들어가지 않는다 — 다른 레인과 똑같이 `{{num:key}}`만 쓴다.
숫자는 여기서 **레지스트리에 등록**되고, 등록된 것에는 출처가 붙는다:

* 등락 — 금융위 시세. 계산은 우리 것이고 원천은 공개 API다
* 공시 — DART 원문 링크. **제목만 싣는다** — 본문 해석은 리포트가 하는 일이고,
  여기서 흉내 내면 검산 없는 숫자가 답에 앉는다

무엇을 안 하나
--------------
**공시 본문을 안 읽는다.** 「왜 올랐나」에 대한 답으로 「이런 공시가 있었다」까지가
이 레인의 한계다. 그 이상은 리포트가 하고, 그 경계를 프롬프트가 못 박는다.

**기사·텔레그램은 여기 없다.** 그것들은 이미 미검증 레인으로 따로 다룬다(D45).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from arc.data.base import Provenance
from arc.llm.number_registry import NumberEntry

# 프롬프트에 세울 구간. **전부 싣지 않는다** — 「왜 올랐나」에 답하는 것은
# 최근이고, 1년은 배경이다.
HORIZONS = ("1d", "5d", "1m", "1y")

# 공시는 최근 것 몇 건만. 스무 건을 나열하면 답이 목록이 된다.
MAX_FILINGS = 5

# 본문에 붙일 인용 표시. **검증기가 아는 꼴이어야 한다** — `guard._MARKER_RE`가
# 이 형태만 마커로 인정하고, 아니면 그 문장이 전부 「출처 없음」으로 찍힌다.
# 실제로 회사명으로 인용시켰다가 문장 셋이 다 걸렸다.
MARKET_TAG = "m1"


@dataclass
class MarketFacts:
    """카드 없이도 아는 것. **비어 있으면 아무것도 안 낸다.**"""

    symbol: str
    company: str = ""
    entries: list[NumberEntry] = field(default_factory=list)
    # (제목, 날짜, 링크). **숫자가 아니라 제목이다**
    filings: list[tuple[str, str, str]] = field(default_factory=list)
    asof: str = ""

    @property
    def empty(self) -> bool:
        return not self.entries and not self.filings

    def keys(self) -> list[str]:
        return [e.key for e in self.entries]


def _price_provenance(asof: str) -> Provenance:
    """시세의 출처. **재현용과 확인용을 나눈다**(`base.Provenance` 참조)."""
    return Provenance(
        source="krx_price",
        retrieved_at=dt.datetime.now(dt.UTC),
        dataset=f"금융위 주식시세 · {asof} 종가 기준" if asof else "금융위 주식시세",
        source_url="https://apis.data.go.kr/1160100/service/GetStockSecuritiesInfoService",
        # 사람이 열어 확인할 곳. 종목별 페이지가 없어 비운다 — **없는 링크를
        # 지어내지 않는다**
        verify_url="",
    )


def build_market_facts(
    symbol: str,
    *,
    company: str = "",
    moves: dict | None = None,
    filings: list[dict] | None = None,
    tag: str = MARKET_TAG,
) -> MarketFacts:
    """등락·공시 → 근거. **순수 함수다** — 가져오는 것은 부르는 쪽이 한다.

    `moves`는 `_moves_payload()`의 한 항목, `filings`는 `_recent_filings()`의
    결과를 그대로 받는다. 키에 `tag`를 붙이는 이유는 카드 수치와 섞이지 않게
    하려는 것이다 — 같은 이름이 두 뜻을 가지면 출처가 흐려진다.
    """
    out = MarketFacts(symbol=symbol, company=company or symbol)
    moves = moves or {}
    asof = str(moves.get("last_date") or "")
    out.asof = asof

    prov = _price_provenance(asof)
    for item in moves.get("items") or []:
        key = str(item.get("key") or "")
        pct = item.get("change_pct")
        if key not in HORIZONS or pct is None:
            continue
        out.entries.append(
            NumberEntry(
                key=f"{tag}.change_{key}",
                value=round(float(pct), 2),
                unit="%",
                display=f"{float(pct):+.2f}%",
                provenance=prov,
                label=f"{out.company} {item.get('label') or key} 등락",
            )
        )

    for f in (filings or [])[:MAX_FILINGS]:
        title = str(f.get("title") or "").strip()
        if not title:
            continue
        out.filings.append((title, str(f.get("filed_at") or "")[:10], str(f.get("url") or "")))
    return out


def _dashed(asof: str) -> str:
    """`20260807` → `2026-08-07`. **G0를 통과하는 형태다** (실측).

    붙여 쓴 여덟 자리는 등록 안 된 숫자로 잡히고, 「2026년 8월 7일」은 8과 7이
    각각 잡힌다. 하이픈 꼴만 화이트리스트를 통과한다.
    """
    if len(asof) != 8 or not asof.isdigit():
        return asof
    return f"{asof[:4]}-{asof[4:6]}-{asof[6:]}"


def market_prompt(facts: MarketFacts) -> str:
    """근거 절. **비면 빈 문자열** — 빈 절은 모델이 뭔가로 채우려 한다.

    값을 안 넣는다. 카탈로그는 `build_prompt`가 레지스트리에서 만든다.
    """
    if facts.empty:
        return ""

    lines = [f"# 시세·공시 ({facts.company}) — 리포트 카드가 아닌 근거입니다"]
    if facts.entries:
        # **날짜와 종목코드를 날것으로 안 쓴다.** 모델이 되뱉으면 G0가
        # 그 문장을 버린다 — 실측: `20260807` 막힘, `2026-08-07` 통과,
        # `[316140]` 막힘, `한화오션(042660)` 통과. 인용 표시는 회사명으로 준다.
        when = f" ({_dashed(facts.asof)} 종가 기준)" if facts.asof else ""
        lines.append(
            f"- 주가 등락{when}: " + " · ".join(f"{{{{num:{e.key}}}}}" for e in facts.entries)
        )
        lines.append(
            f"  (구간 이름은 「수치 카탈로그」에 있습니다. **[{MARKET_TAG}]** 로 인용하십시오)"
        )
    if facts.filings:
        lines.append(
            f"- 같은 기간 공시 (제목만 — **본문은 안 읽었습니다**. [{MARKET_TAG}] 로 인용):"
        )
        lines.extend(f"    · {title} ({when})" for title, when, _ in facts.filings)
        lines.append(
            "  **공시 내용을 추측하지 마십시오.** 「이런 공시가 있었다」까지만"
            " 쓰고, 무슨 뜻인지는 근거가 없으면 쓰지 않습니다."
        )
    lines.append("")
    return "\n".join(lines)
