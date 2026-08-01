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

**뷰의 차이는 부호가 아니라 세 가지에서 온다** (초판이 놓친 것)
------------------------------------------------------------
초판은 렌즈마다 `stance`를 하나 두고 "부정이 하나라도 있으면 부정"으로 접었다.
그 결과 LG전자에서 **렌즈의 1순위 발견과 정반대 문장**이 리포트에 실렸다 —
자본수익률 렌즈의 주된 발견은 "자산이 흑자 부문에 배치돼 있다"(긍정)였는데,
부차적인 레버리지 관찰 하나 때문에 렌즈 전체가 부정으로 접혔다.

같은 결론이라도 태도가 갈리는 지점은 셋이다:

1. **무엇을 보는가** — 렌즈마다 다른 데이터를 본다
2. **무엇을 먼저 보는가** — `chain`이 질문의 순서를 고정한다. 순서는 임의가
   아니라 **질문에서 도출된다**: 자본이 어디 있는지 모르면 그게 버는지 말할 수 없다
3. **다음에 무엇을 볼 것인가** — `watch`. 두 렌즈가 똑같이 긍정이어도 다음 분기에
   볼 숫자가 다르면 그게 태도의 차이다

그래서 렌즈는 부호로 접히지 않고 **주된 발견(headline) + 단서(caveat) +
다음에 볼 것(watch)**으로 남는다. 답하지 못한 질문(`unanswered`)도 남긴다 —
"부문 자산을 공시하지 않는다"는 것 자체가 회사에 대한 사실이다.

무엇을 지키는가
---------------
* 관찰문에 **크기를 쓰지 않는다.** 프롬프트의 숫자는 LLM이 리터럴로 베낀다
  ([D16](../../docs/decisions.md)). 크기는 플레이스홀더로 간다.
* 새로 계산한 수치는 전부 `NumberEntry`로 등록한다.
* **근거가 없으면 렌즈는 침묵한다.** 억지로 말하게 하면 페르소나를 기각한
  이유(근거 없는 판단)를 렌즈 이름으로 되풀이하는 것이다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from arc.data.base import Provenance
from arc.finmodel.metrics import MarginBridge, MetricSet, fmt_krw, fmt_pct
from arc.finmodel.segment_profit import SegmentProfitSet
from arc.finmodel.valuation import ValuationSet
from arc.llm.number_registry import NumberEntry

# 적자 부문에 묶인 자산이 이 비중을 넘으면 "자본이 안 버는 곳에 있다"고 말한다
TRAPPED_ASSET_PCT = 30.0
# 부문 간 상각 부담 격차가 이보다 크면 같은 잣대로 보지 말라고 말한다(pp)
DEPRECIATION_SPREAD_PP = 10.0

CAPITAL_RETURN = "capital_return"
DURABILITY = "durability"

# 판독의 방향. **rating 어휘를 쓰지 않는다** — 이건 사실의 방향이지 투자의견이
# 아니고, 리포트 본문에 이 낱말이 그대로 나가지도 않는다.
SUPPORTIVE = "supportive"
ADVERSE = "adverse"
NEUTRAL = "neutral"


@dataclass(frozen=True)
class LensReading:
    """렌즈가 질문 사슬의 한 단계에 내놓은 답.

    `claim`에는 **크기가 없다.** 크기는 `keys`가 가리키는 레지스트리 항목이
    플레이스홀더로 들고 온다.
    """

    step: int  # 질문 사슬에서의 위치 (1이 가장 앞)
    claim: str
    direction: str = NEUTRAL
    keys: list[str] = field(default_factory=list)


@dataclass
class LensView:
    """렌즈 1개의 판독.

    **부호 하나로 접지 않는다.** 주된 발견과 단서를 나눠 두어야 "긍정이지만
    조건이 붙는다"를 표현할 수 있다.
    """

    key: str
    label: str
    question: str
    chain: tuple[str, ...] = ()  # 질문 사슬 — 우선순위의 근거
    readings: list[LensReading] = field(default_factory=list)
    unanswered: list[str] = field(default_factory=list)  # 답하지 못한 사슬 단계
    watch: str = ""  # 이 렌즈가 다음에 볼 것
    watch_keys: list[str] = field(default_factory=list)
    silent_reason: str = ""

    @property
    def ordered(self) -> list[LensReading]:
        return sorted(self.readings, key=lambda r: r.step)

    @property
    def usable(self) -> bool:
        return bool(self.readings)

    @property
    def headline(self) -> LensReading | None:
        """**가장 앞선 질문에 대한 답**이 이 렌즈의 주된 발견이다.

        코드 작성 순서가 아니라 `step`이 정한다. 초판은 이걸 안 해서 부차적
        관찰이 렌즈 전체의 결론을 뒤집었다.
        """
        answered = [r for r in self.ordered if r.direction != NEUTRAL]
        return answered[0] if answered else None

    @property
    def caveats(self) -> list[LensReading]:
        """주된 발견보다 **뒤에 있고 방향이 다른** 판독.

        "자산은 버는 곳에 있다 — 다만 그 수익률이 부채에서 온다"의 뒷부분이다.
        접어 버리면 리포트가 둘 중 하나만 말하게 된다.
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
    """관전 포인트가 될 지점. **세 종류가 있다.**"""

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


# ── 자본수익률 렌즈 ──────────────────────────────────────────────────
CAPITAL_CHAIN = (
    "자본이 어디에 놓여 있는가",
    "그 자본이 버는가",
    "지금 보이는 수익률이 무엇에서 오는가",
)


def _trapped_asset_pct(sp: SegmentProfitSet | None) -> float | None:
    """적자 부문에 묶인 자산 비중(%). **부문 자산이 공시된 회사에서만 나온다.**"""
    if sp is None or not sp.usable:
        return None
    known = [x for x in sp.lines if x.assets]
    if len(known) < 2:
        return None
    total = sum(x.assets or 0 for x in known)
    if not total:
        return None
    return sum(x.assets or 0 for x in known if x.is_loss) / total * 100.0


def build_capital_return_view(
    ms: MetricSet,
    valuation: ValuationSet | None,
    sp: SegmentProfitSet | None,
    fiscal_year: int,
) -> LensView:
    """「투입 자본 대비 얼마를 버는가」.

    사슬이 순서를 강제한다 — **자본이 어디 있는지 모르면 그게 버는지 말할 수
    없다.** 임의로 매긴 우선순위가 아니라 질문에서 도출된 것이다.
    """
    y = fiscal_year
    view = LensView(
        key=CAPITAL_RETURN,
        label="자본수익률",
        question="이 회사는 투입한 자본 대비 얼마를 버는가",
        chain=CAPITAL_CHAIN,
    )

    # 1단계 — 자본이 어디에 놓여 있는가
    trapped = _trapped_asset_pct(sp)
    if trapped is None:
        view.unanswered.append(CAPITAL_CHAIN[0])
    elif trapped >= TRAPPED_ASSET_PCT:
        losers = ", ".join(x.name for x in (sp.loss_makers if sp else []))
        view.readings.append(
            LensReading(
                step=1,
                claim=(
                    f"부문 자산의 상당 부분이 영업적자 부문({losers})에 묶여 있다. "
                    "자산이 크다는 사실과 그 자산이 번다는 사실은 다르고, "
                    "전사 지표만으로는 이 구분이 보이지 않는다."
                ),
                direction=ADVERSE,
                keys=[f"trapped_asset_{y}a", f"trapped_asset_share_{y}a"],
            )
        )
    else:
        view.readings.append(
            LensReading(
                step=1,
                claim=(
                    "부문 자산이 대체로 흑자 부문에 배치돼 있다. "
                    "자산 구성이 수익성을 갉아먹는 구조는 아니다."
                ),
                direction=SUPPORTIVE,
                keys=[f"trapped_asset_share_{y}a"],
            )
        )

    # 2단계 — 그 자본이 버는가. **1단계가 답해졌을 때만 본다.**
    ranked = [x for x in (sp.lines if sp and sp.usable else []) if x.asset_return is not None]
    if trapped is not None and len(ranked) >= 2:
        best = max(ranked, key=lambda x: x.asset_return or 0)
        worst = min(ranked, key=lambda x: x.asset_return or 0)
        view.readings.append(
            LensReading(
                step=2,
                claim=(
                    f"자산 대비 영업이익이 가장 높은 부문은 {best.name}, 가장 낮은 부문은 "
                    f"{worst.name}이다. 이익률이 높아도 자산이 무거우면 자본 효율은 "
                    "다르게 읽힌다."
                ),
                direction=NEUTRAL,
            )
        )
    elif trapped is not None:
        view.unanswered.append(CAPITAL_CHAIN[1])

    # 3단계 — 지금 보이는 수익률이 무엇에서 오는가. 전사 지표라 부문 자산 없이도 본다.
    spread = [
        (x.ebitda_margin or 0) - (x.op_margin or 0)
        for x in (sp.lines if sp and sp.usable else [])
        if x.ebitda_margin is not None and x.op_margin is not None
    ]
    if len(spread) >= 2 and (max(spread) - min(spread)) >= DEPRECIATION_SPREAD_PP:
        view.readings.append(
            LensReading(
                step=3,
                claim=(
                    "부문 간 감가상각 부담의 차이가 크다. 영업이익률만 나란히 놓으면 "
                    "자본집약도가 다른 사업을 같은 종류의 수익성으로 읽게 된다."
                ),
                direction=NEUTRAL,
            )
        )

    # ROE와 ROA의 벌어짐은 레버리지에서 온다.
    # **둘 다 양수일 때만 본다** — 적자면 부등호의 뜻이 뒤집힌다. 손실 상태에서
    # 레버리지가 높으면 ROE는 ROA보다 더 **낮아진다**(더 큰 음수). 롯데케미칼에서
    # 이 조건이 그대로 발화해 반대로 말할 뻔했다.
    roe = valuation.roe if valuation else None
    roa = valuation.roa if valuation else None
    if roe is not None and roa is not None and roe > 0 and roa > 0 and roe > roa * 2:
        view.readings.append(
            LensReading(
                step=3,
                claim=(
                    "자기자본이익률이 총자산이익률을 크게 웃돈다. 그 차이는 사업의 "
                    "수익성이 아니라 부채 사용에서 온다."
                ),
                direction=ADVERSE,
                keys=[f"roe_{y}a", f"roa_{y}a", f"debt_ratio_{y}a"],
            )
        )

    # 다음에 볼 것 — **주된 발견이 무엇이었는지가 정한다.**
    head = view.headline
    if head is not None and head.step == 1 and head.direction is ADVERSE:
        view.watch = "적자 부문에 묶인 자산이 줄어드는가"
        view.watch_keys = [f"trapped_asset_{y}a"]
    elif head is not None and head.step == 1:
        view.watch = "부문별 자산 대비 영업이익의 격차가 벌어지는가"
    elif head is not None:
        view.watch = "자기자본이익률과 총자산이익률의 격차가 좁혀지는가"
        view.watch_keys = [f"debt_ratio_{y}a"]

    if not view.readings:
        view.silent_reason = "부문 자산이 공시되지 않아 자본이 어디에 묶여 있는지 확인하지 못했다."
    return view


# ── 재현성 렌즈 ──────────────────────────────────────────────────────
DURABILITY_CHAIN = (
    "무엇이 마진을 움직였는가",
    "그것이 회사가 통제하는 것인가",
    "부문마다 같은가",
    "영업 밖의 요인이 섞였는가",
)


def build_durability_view(
    ms: MetricSet,
    bridge: MarginBridge | None,
    sp: SegmentProfitSet | None,
    fiscal_year: int,
) -> LensView:
    """「이번 이익은 다음에도 반복되는가」."""
    y = fiscal_year
    view = LensView(
        key=DURABILITY,
        label="재현성",
        question="이번 이익은 다음에도 반복되는가",
        chain=DURABILITY_CHAIN,
    )

    # 1~2단계 — 분해가 되는가, 그리고 주도 요인이 통제 안인가
    if bridge is None or not bridge.reconciled:
        view.unanswered.append(DURABILITY_CHAIN[0])
        view.unanswered.append(DURABILITY_CHAIN[1])
        if bridge is not None:
            view.readings.append(
                LensReading(
                    step=1,
                    claim=(
                        "영업이익이 매출에서 원가와 판관비를 뺀 값과 일치하지 않아 "
                        "이번 마진 변화를 비용 항목으로 분해하지 못했다. 무엇이 이익을 "
                        "움직였는지 단정하지 않는다."
                    ),
                    direction=NEUTRAL,
                )
            )
    elif "원가" in bridge.dominant:
        view.readings.append(
            LensReading(
                step=2,
                claim=(
                    "이익률 변화를 주도한 것은 원가율이다. 원가는 원재료·환율 등 "
                    "회사 밖 조건에 좌우되는 부분이 커서, 같은 폭의 변화가 다음 해에도 "
                    "반복된다고 보기 어렵다."
                ),
                direction=ADVERSE,
                keys=[f"bridge_cost_contrib_{y}a", f"cost_ratio_{y}a"],
            )
        )
    else:
        view.readings.append(
            LensReading(
                step=2,
                claim=(
                    "이익률 변화를 주도한 것은 판관비율이다. 판관비는 원가보다 회사가 "
                    "통제할 여지가 커서, 구조적 변화라면 유지될 가능성이 있다."
                ),
                direction=SUPPORTIVE,
                keys=[f"bridge_sga_contrib_{y}a", f"sga_ratio_{y}a"],
            )
        )

    # 3단계 — 부문마다 같은가
    changed = [x for x in (sp.lines if sp and sp.usable else []) if x.margin_change is not None]
    if len(changed) >= 2:
        up = [x for x in changed if (x.margin_change or 0) > 0]
        down = [x for x in changed if (x.margin_change or 0) < 0]
        if up and down:
            view.readings.append(
                LensReading(
                    step=3,
                    claim=(
                        "부문별 이익률의 방향이 갈렸다. 전사 마진 변화를 업황 같은 "
                        "공통 요인으로 설명할 수 없고, 부문마다 재현 가능성을 따로 "
                        "봐야 한다."
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
        view.unanswered.append(DURABILITY_CHAIN[2])

    # 4단계 — 영업 밖 요인
    op_yoy, net_yoy = _yoy(ms, "operating_income"), _yoy(ms, "net_income")
    if op_yoy is not None and net_yoy is not None and (op_yoy > 0) != (net_yoy > 0):
        view.readings.append(
            LensReading(
                step=4,
                claim=(
                    "영업이익과 당기순이익의 증감 방향이 다르다. 차이는 영업외손익 또는 "
                    "법인세에서 오므로, 순이익만 보면 본업의 방향을 잘못 읽는다."
                ),
                direction=ADVERSE,
                keys=[f"operating_income_yoy_{y}a", f"net_income_yoy_{y}a"],
            )
        )

    head = view.headline
    if head is not None and head.step == 2 and head.direction is ADVERSE:
        view.watch = "원가율이 같은 방향으로 이어지는가"
        view.watch_keys = [f"cost_ratio_{y}a"]
    elif head is not None and head.step == 2:
        view.watch = "판관비율의 개선이 유지되는가"
        view.watch_keys = [f"sga_ratio_{y}a"]
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

    초판은 부호가 다를 때만 잡았다. 그래서 **둘 다 긍정일 때 아무 말도 안 했는데,
    사실 그때가 독자를 가장 오도하기 쉽다** — "합의됐다"로 읽히지만 실제로는
    서로 다른 방식으로 맞을 수 있다는 뜻이다.
    """
    out: list[LensTension] = []
    usable = [v for v in views if v.usable]

    # 1) 단서 충돌 — 한 렌즈 안에서 주된 발견과 뒤 판독의 방향이 갈린다
    for view in usable:
        for caveat in view.caveats:
            out.append(
                LensTension(
                    kind="caveat",
                    text=(
                        f"{view.label} 관점의 주된 판단에는 단서가 붙는다 — {caveat.claim} "
                        "두 사실이 함께 성립하므로 한쪽만 보면 판단이 달라진다."
                    ),
                    keys=list(caveat.keys),
                )
            )

    # 2·3) 렌즈 사이 — 결론이 갈리는가, 아니면 결론은 같은데 근거가 다른가
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
                            f"{left.label}과 {right.label} 관점이 같은 방향을 가리키지만 "
                            f"근거가 다르다. 전자는 «{left.watch}», 후자는 «{right.watch}»를 본다. "
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
) -> LensSet:
    y = ms.fiscal_year
    views = [
        build_capital_return_view(ms, valuation, segment_profit, y),
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
            # 라벨에 **분모를 적는다.** 실측: "영업적자 부문의 자산 비중"만 주니
            # LLM이 "자산총계의 75.9%"라고 썼는데 분모는 부문 자산 합계다
            # (롯데케미칼 35.4조 vs 자산총계 31.1조). 값이 맞아도 무엇에 대한
            # 비중인지가 틀리면 독자가 오해한다.
            label=f"부문 자산 합계 대비 영업적자 부문의 자산 비중 ({y}A)",
            formula="영업적자 부문 자산 / 부문 자산이 확인된 부문의 합계 (자산총계가 아니다)",
            inputs=[f"trapped_asset_{y}a"],
        ),
    ]


# ── 논지 ─────────────────────────────────────────────────────────────
def build_lens_observations(lenses: LensSet) -> list[str]:
    """렌즈 논지. **크기를 쓰지 않는다** (D16).

    LLM이 관점의 구조를 알아볼 수 있게 **주된 발견 / 단서 / 다음에 볼 것**을
    라벨로 구분해 준다. 평평한 목록으로 주면 LLM이 우선순위를 지어낸다.
    """
    obs: list[str] = []
    for view in lenses.views:
        if not view.usable:
            continue
        obs.append(f"[{view.label} 관점] {view.question}?")
        head = view.headline
        for r in view.ordered:
            if head is not None and r is head:
                obs.append(f"[{view.label}·주된 발견] {r.claim}")
            elif r in view.caveats:
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
