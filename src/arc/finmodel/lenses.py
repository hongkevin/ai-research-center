"""분석 렌즈 — 같은 숫자에 **다른 질문**을 던진다 ([D35](../../docs/decisions.md)).

왜 렌즈인가
-----------
[D22](../../docs/decisions.md)가 진단한 빈 곳: 목표주가·투자의견을 금지했으므로
([D4](../../docs/decisions.md)) 뷰가 빠진 자리에 관측의 나열만 남고, 그건
필연적으로 회계 문서가 된다.

제안된 대안은 실명 페르소나였다(버핏·애크먼을 에이전트로). **채택하지 않았다** —
"버핏이라면 샀을 것"은 우회가 아니라 투자의견 그 자체이고, 살아 있는 실명
인물에게 하지 않은 말을 귀속시킨다. 근거는
[research/05](../../docs/research/05-view-and-personas.md).

    페르소나는 **결론**을 내지만, 렌즈는 **다른 질문**을 던진다.
    같은 검증된 숫자에 서로 다른 질문을 던지고, **답이 갈리는 지점이 곧 View다.**

**렌즈는 타깃 시장의 데이터에서 고른다** (2차 수정)
--------------------------------------------------
초판은 「자본수익률·재현성」 둘을 골랐다. 대형주 4곳으로 검증하고 일반화했는데,
실제 타깃인 코스닥 25곳에서 재보니 참사였다:

* 자본수익률 렌즈가 요구하는 **부문 자산: 0/25**. 1순위 질문에 답한 종목이 없다.
* 그런데도 3순위 관찰(ROE vs ROA) 하나로 "결론"을 냈고, **다섯 곳이 글자까지
  같은 문장**을 받았다. [D3 보강](../../docs/decisions.md)이 이미 기록한 실패
  모드(*"NVDA와 TSLA가 글자까지 동일한 앵글을 받는다"*)를 렌즈 이름으로 재현했다.
* 관점 충돌은 4/25(16%)뿐이었다.

그래서 **먼저 코스닥 22곳의 데이터 가용률을 재고 그 위에서 렌즈를 골랐다**:

    최대주주·ROE·사업개요 100% · 부문 매출 82% · 출자 장부가 73% · 다부문 64%
    마진 브리지 55% · 자기주식 50% · 배당성향 27% · 부문 자산 0%

세 렌즈의 **1순위 질문**을 각각 100% / 82% / 55% 가용한 것으로 놓았다.

두 가지 구조 규칙
-----------------
1. **1순위에 답하지 못하면 결론을 내지 못한다** (`headline`). 뒤 단계의 관찰은
   맥락으로 남되 판정이 되지 않는다. 이 규칙 하나가 위의 "다섯 곳 같은 문장"을
   막는다 — 자본이 어디 있는지 모르면 자본에 대해 결론짓지 않는다.
2. **판독은 회사를 지목한다.** 부문명·출자처·최대주주처럼 이름을 부르고, 크기는
   `slots`으로 플레이스홀더가 물고 온다. 이름도 숫자도 없는 판독은 상용구다.

무엇을 지키는가
---------------
* 관찰문(`claim`)에 **크기를 쓰지 않는다.** 프롬프트의 숫자는 LLM이 리터럴로
  베낀다([D16](../../docs/decisions.md)). 크기는 본문에서만 플레이스홀더로 나온다.
* 새로 계산한 수치는 전부 `NumberEntry`로 등록한다.
* **근거가 없으면 렌즈는 침묵한다.** 억지로 말하게 하면 페르소나를 기각한
  이유(근거 없는 판단)를 렌즈 이름으로 되풀이하는 것이다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from arc.data.base import Provenance
from arc.data.kr.dart_reports import PeriodicReportInfo
from arc.finmodel.business import BusinessProfile
from arc.finmodel.metrics import MarginBridge, MetricSet, fmt_krw, fmt_pct
from arc.finmodel.segment_profit import SegmentProfitSet
from arc.finmodel.segments import SegmentBreakdown
from arc.finmodel.valuation import ValuationSet
from arc.llm.josa import attach
from arc.llm.number_registry import NumberEntry

CAPITAL = "capital"
CONCENTRATION = "concentration"
DURABILITY = "durability"

# 판독의 방향. **rating 어휘를 쓰지 않는다** — 사실의 방향이지 투자의견이 아니고,
# 이 낱말이 리포트 본문에 그대로 나가지도 않는다.
SUPPORTIVE = "supportive"
ADVERSE = "adverse"
NEUTRAL = "neutral"

# 자본이 이 정도 남기지 못하면 "자본을 늘리는 속도가 느리다"고 본다 (ROE %)
LOW_ROE_PCT = 5.0
# 출자 장부가가 자산에서 이 비중을 넘으면 자본이 본업 밖에 있다고 본다
AFFILIATE_HEAVY_PCT = 20.0
# 최대 부문이 이 비중을 넘으면 단일 사업에 기댄 구조로 본다
SEGMENT_CONCENTRATION_PCT = 70.0
# 최대주주+특수관계인 지분이 이 비중을 넘으면 소유가 집중된 것으로 본다
OWNER_CONTROL_PCT = 50.0
# 적자 부문에 묶인 자산이 이 비중을 넘으면 "자본이 안 버는 곳에 있다"
TRAPPED_ASSET_PCT = 30.0

# 부문명이 아니라 **매출 유형·판매 구분**인 라벨. D28 파서가 표에서 이런 라벨을
# 부문으로 뽑는 경우가 있고("매출이 제품에 몰려 있다"), 그대로 인용하면 리포트가
# 아무 말도 안 하게 된다. 이름을 못 믿으면 사업 집중을 말하지 않는다.
_NON_SEGMENT_LABELS = frozenset(
    [
        "제품",
        "상품",
        "용역",
        "기타",
        "제품매출",
        "상품매출",
        "용역매출",
        "기타매출",
        "매출",
        "매출액",
        "수익",
        "합계",
        "계",
        "소계",
        "총계",
        "국내",
        "해외",
        "수출",
        "내수",
        "-",
        "—",
    ]
)


def _is_named_segment(name: str) -> bool:
    v = (name or "").replace(" ", "")
    return bool(v) and v not in _NON_SEGMENT_LABELS


@dataclass(frozen=True)
class LensReading:
    """렌즈가 질문 사슬의 한 단계에 내놓은 답.

    `claim`은 **크기가 없는** 프롬프트용 문장이다. 본문에는 `report`(슬롯 포함)가
    쓰이고 슬롯은 레지스트리 키로 채워진다 — 그래야 회사마다 다른 글이 된다.
    """

    step: int  # 질문 사슬에서의 위치 (1이 가장 앞)
    claim: str
    direction: str = NEUTRAL
    report: str = ""  # 본문용. `{슬롯}` 포함. 비면 claim을 쓴다
    slots: dict[str, str] = field(default_factory=dict)  # 슬롯 → 레지스트리 키

    def report_text(self, resolve) -> str:
        """`resolve(key)` → 플레이스홀더. 못 찾은 슬롯이 있으면 `claim`으로 물러난다."""
        if not self.report:
            return self.claim
        filled = {name: resolve(key) for name, key in self.slots.items()}
        if any(v is None for v in filled.values()):
            return self.claim
        return self.report.format(**filled)


@dataclass
class LensView:
    """렌즈 1개의 판독. **부호 하나로 접지 않는다.**"""

    key: str
    label: str
    question: str
    chain: tuple[str, ...] = ()
    readings: list[LensReading] = field(default_factory=list)
    unanswered_steps: list[int] = field(default_factory=list)
    watch: str = ""
    watch_keys: list[str] = field(default_factory=list)
    silent_reason: str = ""

    @property
    def ordered(self) -> list[LensReading]:
        return sorted(self.readings, key=lambda r: r.step)

    @property
    def usable(self) -> bool:
        return bool(self.readings)

    @property
    def unanswered(self) -> list[str]:
        return [self.chain[s - 1] for s in sorted(self.unanswered_steps) if s <= len(self.chain)]

    @property
    def headline(self) -> LensReading | None:
        """주된 발견. **앞선 질문에 답하지 못했으면 결론을 내지 않는다.**

        이 규칙이 2차 수정의 핵심이다. 코스닥 25곳에서 자본 관련 1순위 질문에
        답한 곳이 0이었는데, 초판은 3순위 관찰 하나로 결론을 내 다섯 곳이 글자까지
        같은 문장을 받았다. **모르면 결론짓지 않는다.**
        """
        answered = [r for r in self.ordered if r.direction != NEUTRAL]
        if not answered:
            return None
        first = answered[0]
        if any(s < first.step for s in self.unanswered_steps):
            return None
        return first

    @property
    def caveats(self) -> list[LensReading]:
        """주된 발견보다 **뒤에 있고 방향이 다른** 판독.

        "자산은 버는 곳에 있다 — 다만 그 수익률이 부채에서 온다"의 뒷부분이다.
        """
        head = self.headline
        if head is None:
            return []
        return [
            r
            for r in self.ordered
            if r.step > head.step and r.direction not in (NEUTRAL, head.direction)
        ]

    @property
    def verdict(self) -> str | None:
        head = self.headline
        return head.direction if head else None


@dataclass(frozen=True)
class LensTension:
    kind: str  # "verdict" | "grounds" | "caveat"
    text: str
    keys: list[str] = field(default_factory=list)


@dataclass
class LensSet:
    views: list[LensView] = field(default_factory=list)
    tensions: list[LensTension] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return any(v.usable for v in self.views)

    def view(self, key: str) -> LensView | None:
        return next((v for v in self.views if v.key == key), None)


# ── 자본 렌즈 ────────────────────────────────────────────────────────
CAPITAL_CHAIN = (
    "투입한 자본이 수익을 내는가",  # ROE — 코스닥 100%
    "그 자본이 어디에 놓여 있는가",  # 출자 장부가 73% · 부문 자산(대형주)
    "남는 것이 주주에게 돌아가는가",  # 배당·자기주식 ~60%
)


def _trapped_asset_pct(sp: SegmentProfitSet | None) -> float | None:
    if sp is None or not sp.usable:
        return None
    known = [x for x in sp.lines if x.assets]
    if len(known) < 2:
        return None
    total = sum(x.assets or 0 for x in known)
    if not total:
        return None
    return sum(x.assets or 0 for x in known if x.is_loss) / total * 100.0


def build_capital_view(
    ms: MetricSet,
    valuation: ValuationSet | None,
    business: BusinessProfile | None,
    info: PeriodicReportInfo | None,
    sp: SegmentProfitSet | None,
    y: int,
) -> LensView:
    """「투입한 자본이 무엇을 하고 있는가」.

    1순위를 ROE로 놓은 이유는 **코스닥에서 100% 가용**하기 때문이다. 초판은
    부문 자산(0%)을 1순위로 놓아 이 렌즈가 타깃 시장에서 아무것도 못 말했다.
    """
    view = LensView(
        key=CAPITAL, label="자본", question="투입한 자본이 무엇을 하고 있는가", chain=CAPITAL_CHAIN
    )

    # 1단계 — 자본이 수익을 내는가
    roe = valuation.roe if valuation else None
    if roe is None:
        view.unanswered_steps.append(1)
    elif roe < 0:
        view.readings.append(
            LensReading(
                step=1,
                claim=(
                    "자기자본이익률이 마이너스다. 한 해 동안 자본이 늘지 않고 줄었다는 "
                    "뜻이므로, 자본을 어디에 썼는지보다 손실이 계속되는지가 먼저다."
                ),
                direction=ADVERSE,
                report=(
                    "자기자본이익률이 {roe}로 마이너스다. 한 해 동안 자본이 늘지 않고 "
                    "줄었다는 뜻이므로, 자본을 어디에 썼는지보다 손실이 계속되는지가 먼저다."
                ),
                slots={"roe": f"roe_{y}a"},
            )
        )
    elif roe < LOW_ROE_PCT:
        view.readings.append(
            LensReading(
                step=1,
                claim=(
                    "자기자본이익률이 낮다. 자본이 늘어나는 속도가 느려 이익을 쌓아 "
                    "가치를 키우는 경로는 기대하기 어렵고, 자본을 어디에 배치했는지가 "
                    "더 중요해진다."
                ),
                direction=ADVERSE,
                report=(
                    "자기자본이익률이 {roe}에 그친다. 자본이 늘어나는 속도가 느려 이익을 "
                    "쌓아 가치를 키우는 경로는 기대하기 어렵고, 자본을 어디에 배치했는지가 "
                    "더 중요해진다."
                ),
                slots={"roe": f"roe_{y}a"},
            )
        )
    else:
        view.readings.append(
            LensReading(
                step=1,
                claim=(
                    "자기자본이익률이 자본을 의미 있게 늘리는 수준이다. 이 수준이 "
                    "유지되는지가 이 회사를 보는 첫 번째 기준이 된다."
                ),
                direction=SUPPORTIVE,
                report=(
                    "자기자본이익률은 {roe}로 자본을 의미 있게 늘리는 수준이다. 이 수준이 "
                    "유지되는지가 이 회사를 보는 첫 번째 기준이 된다."
                ),
                slots={"roe": f"roe_{y}a"},
            )
        )

    # 2단계 — 자본이 어디에 놓여 있는가. 출자(코스닥) → 부문 자산(대형주) 순으로 본다.
    weight = business.affiliate_weight if business else None
    trapped = _trapped_asset_pct(sp)
    if weight is None and trapped is None:
        view.unanswered_steps.append(2)
    if weight is not None and weight >= AFFILIATE_HEAVY_PCT:
        top = business.affiliates.top(1)[0].name if business and business.affiliates else "출자처"
        view.readings.append(
            LensReading(
                step=2,
                claim=(
                    f"자산의 상당 부분이 타법인 출자에 들어가 있다. 장부가가 가장 큰 곳은 "
                    f"{attach(top, '이다', '다')}. 본업의 수익성과 출자처의 성과가 섞여 있어 전사 지표만으로는 "
                    "어느 쪽이 움직였는지 가려지지 않는다."
                ),
                direction=ADVERSE,
                report=(
                    f"자산의 {{w}}가 타법인 출자에 들어가 있고, 장부가가 가장 큰 곳은 {top}이다. "
                    "본업의 수익성과 출자처의 성과가 섞여 있어 전사 지표만으로는 어느 쪽이 "
                    "움직였는지 가려지지 않는다."
                ),
                slots={"w": f"affiliate_weight_{y}a"},
            )
        )
    elif weight is not None:
        view.readings.append(
            LensReading(
                step=2,
                claim=(
                    "타법인 출자가 자산에서 차지하는 비중은 작다. 자본이 본업에 놓여 있어 "
                    "전사 지표를 본업의 지표로 읽어도 크게 어긋나지 않는다."
                ),
                direction=SUPPORTIVE,
                report=(
                    "타법인 출자는 자산의 {w}에 그친다. 자본이 본업에 놓여 있어 전사 지표를 "
                    "본업의 지표로 읽어도 크게 어긋나지 않는다."
                ),
                slots={"w": f"affiliate_weight_{y}a"},
            )
        )
    if trapped is not None and trapped >= TRAPPED_ASSET_PCT:
        losers = ", ".join(x.name for x in (sp.loss_makers if sp else []))
        view.readings.append(
            LensReading(
                step=2,
                claim=(
                    f"부문 자산의 상당 부분이 영업적자 부문({losers})에 묶여 있다. "
                    "자산이 크다는 사실과 그 자산이 번다는 사실은 다르다."
                ),
                direction=ADVERSE,
                report=(
                    f"부문 자산의 {{s}}가 영업적자 부문({losers})에 묶여 있다. "
                    "자산이 크다는 사실과 그 자산이 번다는 사실은 다르다."
                ),
                slots={"s": f"trapped_asset_share_{y}a"},
            )
        )

    # 3단계 — 남는 것이 주주에게 돌아가는가
    payout = valuation.payout_ratio if valuation else None
    treasury = info.shares.treasury if info and info.shares else None
    if payout is None and not treasury:
        view.unanswered_steps.append(3)
    elif payout is not None:
        view.readings.append(
            LensReading(
                step=3,
                claim=(
                    "배당으로 나가는 몫이 공시돼 있다. 번 돈을 회사에 남길지 주주에게 "
                    "돌릴지가 자본 배치의 마지막 갈림길이다."
                ),
                direction=NEUTRAL,
                report=(
                    "현금배당성향은 {p}다. 번 돈을 회사에 남길지 주주에게 돌릴지가 자본 "
                    "배치의 마지막 갈림길이다."
                ),
                slots={"p": f"payout_ratio_{y}a"},
            )
        )

    head = view.headline
    if head is not None and head.step == 1 and head.direction is ADVERSE:
        view.watch, view.watch_keys = "자기자본이익률이 회복되는가", [f"roe_{y}a"]
    elif head is not None and head.step == 1:
        view.watch, view.watch_keys = "자기자본이익률이 유지되는가", [f"roe_{y}a"]
    elif head is not None:
        view.watch, view.watch_keys = "자본이 본업으로 돌아오는가", [f"affiliate_weight_{y}a"]

    if not view.readings:
        view.silent_reason = "수익성·자본 배치를 확인할 지표가 없어 자본에 대해 말하지 않는다."
    return view


# ── 집중 렌즈 ────────────────────────────────────────────────────────
CONCENTRATION_CHAIN = (
    "매출이 어디에 몰려 있는가",  # 부문 매출 82%
    "그 집중이 커지고 있는가",  # 부문별 YoY
    "소유는 어디에 몰려 있는가",  # 최대주주 100%
)


def build_concentration_view(
    seg: SegmentBreakdown | None,
    info: PeriodicReportInfo | None,
    y: int,
) -> LensView:
    """「무엇에 기대고 있는가」.

    코스닥 소형주에서 가장 중요한 축이고 **데이터가 실제로 있다** — 부문 매출
    82%, 최대주주 100%. 판독이 부문명·최대주주명을 부르므로 회사마다 다른 글이
    된다(상용구가 되지 않는다).
    """
    view = LensView(
        key=CONCENTRATION,
        label="집중",
        question="이 회사는 무엇에 기대고 있는가",
        chain=CONCENTRATION_CHAIN,
    )

    # 1단계 — 매출이 어디에 몰려 있는가
    conc = seg.concentration if seg and seg.usable else None
    big = seg.largest if seg and seg.usable else None
    # 부문명을 못 믿으면 사업 집중을 말하지 않는다. 실측: D28 파서가 「제품」·「-」를
    # 부문으로 뽑아 "매출이 제품에 몰려 있다"가 나왔다 — 아무 말도 아니다.
    if big is not None and not _is_named_segment(big.name):
        conc = big = None
    if conc is None or big is None:
        view.unanswered_steps.append(1)
    elif seg.single_segment:
        view.readings.append(
            LensReading(
                step=1,
                claim=(
                    f"공시된 사업부문은 {big.name} 하나다. 전사 실적이 곧 이 사업의 실적이므로 "
                    "부문 구성 변화로 완충할 여지가 없다."
                ),
                direction=ADVERSE,
            )
        )
    elif conc >= SEGMENT_CONCENTRATION_PCT:
        view.readings.append(
            LensReading(
                step=1,
                claim=(
                    f"매출이 {big.name}에 몰려 있다. 전사 실적은 이 부문의 흐름에 좌우되므로 "
                    "이익률 변화도 이 부문을 기준으로 읽어야 한다."
                ),
                direction=ADVERSE,
                report=(
                    f"매출의 {{s}}가 {big.name}에서 나온다. 전사 실적은 이 부문의 흐름에 "
                    "좌우되므로 이익률 변화도 이 부문을 기준으로 읽어야 한다."
                ),
                slots={"s": f"segment{seg.lines.index(big) + 1}_share_{y}a"},
            )
        )
    else:
        view.readings.append(
            LensReading(
                step=1,
                claim=(
                    f"매출이 여러 부문에 나뉘어 있고 가장 큰 곳은 {big.name}이다. "
                    "한 부문이 흔들려도 전사가 그대로 따라가지는 않는다."
                ),
                direction=SUPPORTIVE,
                report=(
                    f"매출이 여러 부문에 나뉘어 있고 가장 큰 {attach(big.name, '이', '가')} "
                    "{s}를 차지한다. "
                    "한 부문이 흔들려도 전사가 그대로 따라가지는 않는다."
                ),
                slots={"s": f"segment{seg.lines.index(big) + 1}_share_{y}a"},
            )
        )

    # 2단계 — 그 집중이 커지고 있는가
    grown = [x for x in (seg.lines if seg and seg.usable else []) if x.yoy is not None]
    if len(grown) >= 2:
        fastest = max(grown, key=lambda x: x.yoy or 0)
        slowest = min(grown, key=lambda x: x.yoy or 0)
        if (slowest.yoy or 0) < 0 < (fastest.yoy or 0):
            view.readings.append(
                LensReading(
                    step=2,
                    claim=(
                        f"{attach(fastest.name, '은', '는')} 늘고 "
                        f"{attach(slowest.name, '은', '는')} 줄었다. 부문 구성이 이동하는 "
                        "중이므로 전사 성장률만 보면 이 이동이 보이지 않는다."
                    ),
                    direction=NEUTRAL,
                )
            )
    elif seg and seg.usable and not grown:
        view.unanswered_steps.append(2)

    # 3단계 — 소유는 어디에 몰려 있는가
    own = info.ownership if info else None
    if own is None or not own.principal:
        view.unanswered_steps.append(3)
    else:
        total = getattr(own, "total_stake", None)
        controlled = total is not None and total >= OWNER_CONTROL_PCT
        view.readings.append(
            LensReading(
                step=3,
                claim=(
                    f"최대주주는 {own.principal}이며 특수관계인을 포함한 지분이 "
                    + (
                        "과반이다. 경영권 분쟁이나 외부 주주의 개입 여지는 작고, "
                        "자본 배치는 최대주주의 판단에 크게 좌우된다."
                        if controlled
                        else "과반에 못 미친다. 지분 구조가 자본 배치를 한쪽으로 강제하지는 않는다."
                    )
                ),
                direction=NEUTRAL,
                report=(
                    f"최대주주는 {own.principal}이며 특수관계인을 포함한 지분은 {{t}}다. "
                    + (
                        "자본 배치는 최대주주의 판단에 크게 좌우된다."
                        if controlled
                        else "지분 구조가 자본 배치를 한쪽으로 강제하지는 않는다."
                    )
                ),
                slots={"t": f"owner_total_stake_{y}a"},
            )
        )

    head = view.headline
    if head is not None and head.direction is ADVERSE and big is not None:
        view.watch = f"{big.name} 의존이 낮아지는가"
    elif head is not None:
        view.watch = "부문 구성이 어느 쪽으로 이동하는가"

    if not view.readings:
        view.silent_reason = (
            "부문 매출과 지분 구조를 확인하지 못해 무엇에 기대고 있는지 가리지 못했다."
        )
    return view


# ── 재현성 렌즈 ──────────────────────────────────────────────────────
DURABILITY_CHAIN = (
    "무엇이 마진을 움직였는가",
    "그것이 회사가 통제하는 것인가",
    "부문마다 같은가",
    "영업 밖의 요인이 섞였는가",
)


def build_durability_view(
    ms: MetricSet, bridge: MarginBridge | None, sp: SegmentProfitSet | None, y: int
) -> LensView:
    """「이번 이익은 다음에도 반복되는가」."""
    view = LensView(
        key=DURABILITY,
        label="재현성",
        question="이번 이익은 다음에도 반복되는가",
        chain=DURABILITY_CHAIN,
    )

    if bridge is None or not bridge.reconciled:
        view.unanswered_steps.extend([1, 2])
        if bridge is not None:
            view.readings.append(
                LensReading(
                    step=1,
                    claim=(
                        "영업이익이 매출에서 원가와 판관비를 뺀 값과 일치하지 않아 이번 마진 "
                        "변화를 비용 항목으로 분해하지 못했다. 무엇이 이익을 움직였는지 "
                        "단정하지 않는다."
                    ),
                    direction=NEUTRAL,
                )
            )
    elif "원가" in bridge.dominant:
        view.readings.append(
            LensReading(
                step=2,
                claim=(
                    "이익률 변화를 주도한 것은 원가율이다. 원가는 원재료·환율 등 회사 밖 "
                    "조건에 좌우되는 부분이 커서, 같은 폭의 변화가 다음 해에도 반복된다고 "
                    "보기 어렵다."
                ),
                direction=ADVERSE,
                report=(
                    "이익률 변화를 주도한 것은 원가율이고, 원가율은 {c} 움직였다. 원가는 "
                    "원재료·환율 등 회사 밖 조건에 좌우되는 부분이 커서, 같은 폭의 변화가 "
                    "다음 해에도 반복된다고 보기 어렵다."
                ),
                slots={"c": f"cost_ratio_chg_{y}a"},
            )
        )
    else:
        view.readings.append(
            LensReading(
                step=2,
                claim=(
                    "이익률 변화를 주도한 것은 판관비율이다. 판관비는 원가보다 회사가 통제할 "
                    "여지가 커서, 구조적 변화라면 유지될 가능성이 있다."
                ),
                direction=SUPPORTIVE,
                report=(
                    "이익률 변화를 주도한 것은 판관비율이고, 판관비율은 {s} 움직였다. 판관비는 "
                    "원가보다 회사가 통제할 여지가 커서, 구조적 변화라면 유지될 가능성이 있다."
                ),
                slots={"s": f"sga_ratio_chg_{y}a"},
            )
        )

    changed = [x for x in (sp.lines if sp and sp.usable else []) if x.margin_change is not None]
    if len(changed) >= 2:
        up = [x for x in changed if (x.margin_change or 0) > 0]
        down = [x for x in changed if (x.margin_change or 0) < 0]
        if up and down:
            view.readings.append(
                LensReading(
                    step=3,
                    claim=(
                        "부문별 이익률의 방향이 갈렸다. 전사 마진 변화를 업황 같은 공통 "
                        "요인으로 설명할 수 없고, 부문마다 재현 가능성을 따로 봐야 한다."
                    ),
                    direction=NEUTRAL,
                )
            )
        else:
            view.readings.append(
                LensReading(
                    step=3,
                    claim=(
                        "모든 부문의 이익률이 같은 방향으로 움직였다. 회사 고유의 실행보다 "
                        "전사에 걸친 요인이 작용했을 가능성이 크고, 그 요인이 지속되는지가 "
                        "재현성을 가른다."
                    ),
                    direction=ADVERSE if down else SUPPORTIVE,
                )
            )
    else:
        view.unanswered_steps.append(3)

    op_yoy, net_yoy = _yoy(ms, "operating_income"), _yoy(ms, "net_income")
    if op_yoy is None or net_yoy is None:
        view.unanswered_steps.append(4)
    elif (op_yoy > 0) != (net_yoy > 0):
        view.readings.append(
            LensReading(
                step=4,
                claim=(
                    "영업이익과 당기순이익의 증감 방향이 다르다. 차이는 영업외손익 또는 "
                    "법인세에서 오므로, 순이익만 보면 본업의 방향을 잘못 읽는다."
                ),
                direction=ADVERSE,
                report=(
                    "영업이익은 {o}, 당기순이익은 {n} 변동해 방향이 다르다. 차이는 "
                    "영업외손익 또는 법인세에서 오므로, 순이익만 보면 본업의 방향을 잘못 읽는다."
                ),
                slots={"o": f"operating_income_yoy_{y}a", "n": f"net_income_yoy_{y}a"},
            )
        )

    head = view.headline
    if head is not None and head.step == 2 and head.direction is ADVERSE:
        view.watch, view.watch_keys = "원가율이 같은 방향으로 이어지는가", [f"cost_ratio_{y}a"]
    elif head is not None and head.step == 2:
        view.watch, view.watch_keys = "판관비율의 개선이 유지되는가", [f"sga_ratio_{y}a"]
    elif head is not None:
        view.watch = "부문별 이익률이 같은 방향을 이어가는가"

    if not view.readings:
        view.silent_reason = "마진을 비용 항목으로 분해할 지표가 없어 재현성을 따지지 못했다."
    return view


def _yoy(ms: MetricSet, key: str) -> float | None:
    cur, prior = ms.get(key), ms.get_prior(key)
    if cur is None or prior is None or prior == 0:
        return None
    return (cur - prior) / abs(prior) * 100.0


# ── 충돌 ─────────────────────────────────────────────────────────────
def find_tensions(views: list[LensView]) -> list[LensTension]:
    """관전 포인트가 될 지점. **세 종류를 구분한다.**

    둘 다 긍정일 때 아무 말도 안 하면 안 된다 — 그때가 독자를 가장 오도하기 쉽다.
    "합의됐다"로 읽히지만 실제로는 서로 다른 방식으로 맞을 수 있다는 뜻이다.
    """
    out: list[LensTension] = []
    usable = [v for v in views if v.usable]

    for view in usable:
        for caveat in view.caveats:
            out.append(
                LensTension(
                    kind="caveat",
                    text=(
                        f"{view.label} 관점의 주된 판단에는 단서가 붙는다 — {caveat.claim} "
                        "두 사실이 함께 성립하므로 한쪽만 보면 판단이 달라진다."
                    ),
                    keys=list(caveat.slots.values()),
                )
            )

    verdicts = [(v, v.verdict) for v in usable if v.verdict is not None]
    for i, (left, lv) in enumerate(verdicts):
        for right, rv in verdicts[i + 1 :]:
            if lv != rv:
                adverse, supportive = (left, right) if lv is ADVERSE else (right, left)
                out.append(
                    LensTension(
                        kind="verdict",
                        text=(
                            f"두 관점의 결론이 갈린다. {adverse.label} 관점이 짚은 지점은 "
                            f"{supportive.label} 관점에서는 드러나지 않는다. "
                            f"다음 공시에서 «{adverse.watch or adverse.question}»가 판단을 가른다."
                        ),
                        keys=list(adverse.watch_keys),
                    )
                )
            elif left.watch and right.watch and left.watch != right.watch:
                out.append(
                    LensTension(
                        kind="grounds",
                        text=(
                            f"{left.label}과 {right.label} 관점이 같은 방향을 가리키지만 근거가 "
                            f"다르다. 전자는 «{left.watch}», 후자는 «{right.watch}»를 본다. "
                            "한쪽이 어긋나도 다른 쪽은 유지될 수 있으므로 둘을 같이 봐야 한다."
                        ),
                        keys=[*left.watch_keys, *right.watch_keys],
                    )
                )
    return out


def build_lenses(
    ms: MetricSet,
    *,
    valuation: ValuationSet | None = None,
    bridge: MarginBridge | None = None,
    segment_profit: SegmentProfitSet | None = None,
    segments: SegmentBreakdown | None = None,
    business: BusinessProfile | None = None,
    info: PeriodicReportInfo | None = None,
) -> LensSet:
    y = ms.fiscal_year
    views = [
        build_capital_view(ms, valuation, business, info, segment_profit, y),
        build_concentration_view(segments, info, y),
        build_durability_view(ms, bridge, segment_profit, y),
    ]
    return LensSet(views=views, tensions=find_tensions(views))


# ── Number Registry ──────────────────────────────────────────────────
def build_lens_entries(
    lenses: LensSet, sp: SegmentProfitSet | None, prov: Provenance, fiscal_year: int
) -> list[NumberEntry]:
    """렌즈가 **새로 계산한** 수치만 등록한다. 나머지는 이미 등록돼 있다."""
    trapped = _trapped_asset_pct(sp)
    if trapped is None or sp is None:
        return []
    known = [x for x in sp.lines if x.assets]
    amount = sum(x.assets or 0 for x in known if x.is_loss)
    y = fiscal_year
    return [
        NumberEntry(
            key=f"trapped_asset_{y}a",
            value=amount,
            unit="원",
            display=fmt_krw(amount),
            provenance=prov,
            label=f"영업적자 부문의 자산 ({y}A)",
            formula="영업적자 부문의 부문 자산 합계",
        ),
        NumberEntry(
            key=f"trapped_asset_share_{y}a",
            value=trapped,
            unit="%",
            display=fmt_pct(trapped),
            provenance=prov,
            # 라벨에 **분모를 적는다.** 실측: 라벨이 모호하니 LLM이 "자산총계의
            # 75.9%"라고 썼는데 분모는 부문 자산 합계다(롯데케미칼 35.4조 vs 31.1조).
            label=f"부문 자산 합계 대비 영업적자 부문의 자산 비중 ({y}A)",
            formula="영업적자 부문 자산 / 부문 자산이 확인된 부문의 합계 (자산총계가 아니다)",
            inputs=[f"trapped_asset_{y}a"],
        ),
    ]


# ── 논지 ─────────────────────────────────────────────────────────────
def build_lens_observations(lenses: LensSet) -> list[str]:
    """렌즈 논지. **크기를 쓰지 않는다** (D16).

    주된 발견 / 단서 / 다음에 볼 것을 라벨로 구분해 준다 — 평평한 목록으로 주면
    LLM이 우선순위를 지어낸다.
    """
    obs: list[str] = []
    for view in lenses.views:
        if not view.usable:
            continue
        obs.append(f"[{view.label} 관점] {view.question}?")
        head = view.headline
        caveats = view.caveats
        for r in view.ordered:
            if head is not None and r is head:
                obs.append(f"[{view.label}·주된 발견] {r.claim}")
            elif r in caveats:
                obs.append(f"[{view.label}·단서] {r.claim}")
            else:
                obs.append(f"[{view.label}] {r.claim}")
        if view.unanswered:
            obs.append(
                f"[{view.label}·확인 못 함] {', '.join(view.unanswered)} — 공시에 근거가 없어 "
                "이 질문에는 답하지 않는다."
            )
        if view.watch:
            obs.append(f"[{view.label}·다음에 볼 것] {view.watch}")
    for t in lenses.tensions:
        obs.append(f"[관점 충돌] {t.text}")
    return obs
