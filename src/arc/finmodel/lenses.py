"""분석 렌즈 — 같은 숫자에 **다른 질문**을 던진다 ([D35](../../docs/decisions.md)).

왜 렌즈인가
-----------
[D22](../../docs/decisions.md)가 진단한 빈 곳이 남아 있었다: 목표주가·투자의견을
금지했으므로([D4](../../docs/decisions.md)) 뷰가 빠진 자리에 관측의 나열만 남고,
그건 필연적으로 회계 문서가 된다.

제안된 대안은 실명 페르소나였다(버핏·애크먼을 에이전트로). **채택하지 않았다** —
"버핏이라면 샀을 것"은 우회가 아니라 투자의견 그 자체이고, 살아 있는 실명
인물에게 하지 않은 말을 귀속시킨다. 근거는 [research/05](../../docs/research/05-view-and-personas.md).

대신 렌즈를 쓴다:

    페르소나는 **결론**을 내지만, 렌즈는 **다른 질문**을 던진다.
    같은 검증된 숫자에 서로 다른 질문을 던지고, **답이 갈리는 지점이 곧 View다.**

렌즈가 **질문**이지 **판정**이 아니므로 D4를 건드리지 않고, 렌즈 A의 결론이
렌즈 B의 반론이 되어 [D30](../../docs/decisions.md)(반론 필수)과 이어진다.

무엇을 지키는가
---------------
* 관찰문에 **크기를 쓰지 않는다.** 프롬프트의 숫자는 LLM이 리터럴로 베낀다
  ([D16](../../docs/decisions.md)). 방향과 우열만 담고 크기는 플레이스홀더로 간다.
* 새로 계산한 수치는 전부 `NumberEntry`로 등록한다. 렌즈가 만든 숫자라고
  예외가 아니다.
* **근거가 없으면 렌즈는 침묵한다.** 부문 자산이 없는 회사에서 자본수익률
  렌즈가 억지로 말하면, 그게 바로 이 제품이 피하려는 것이다.

렌즈가 갈리면 그것이 「관전 포인트」다
------------------------------------
두 렌즈가 같은 말을 하면 싣지 않는다. **갈리는 지점만** 관전 포인트로 올린다 —
"무엇이 확인되면 판단이 달라지는가"가 그 섹션의 정의이기 때문이다(D22).

실측(롯데케미칼): 자산가치로 보면 싸지만 자산 31.1조 중 24.0조가 영업적자
부문에 묶여 있다. **"싸 보이는 이유가 자산이 안 벌기 때문"** — 전사 지표로는
나오지 않는 논지다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from arc.data.base import Provenance
from arc.finmodel.metrics import MarginBridge, MetricSet, fmt_krw, fmt_pct
from arc.finmodel.segment_profit import SegmentProfitSet
from arc.finmodel.valuation import ValuationSet
from arc.llm.number_registry import NumberEntry

# 적자 부문에 묶인 자산이 이 비중을 넘으면 "자본이 안 버는 곳에 묶여 있다"고
# 말한다. 절반이면 회사의 성격을 규정하는 수준이다.
TRAPPED_ASSET_PCT = 30.0
# 부문 간 상각 부담 격차가 이보다 크면 "같은 잣대로 보면 안 된다"고 말한다(pp)
DEPRECIATION_SPREAD_PP = 10.0
# 영업이익률과 순이익률의 방향이 갈릴 때만 영업외 요인을 짚는다


@dataclass(frozen=True)
class LensReading:
    """렌즈 하나가 읽어낸 사실 1건.

    `claim`에는 **크기가 없다.** 크기는 `keys`가 가리키는 레지스트리 항목이
    플레이스홀더로 들고 온다.
    """

    lens: str  # 렌즈 키
    claim: str  # 이 렌즈가 본 것 (크기 없음)
    keys: list[str] = field(default_factory=list)  # 근거가 되는 레지스트리 키
    stance: str = ""  # 긍정/부정/중립 — 렌즈 간 충돌 판정에 쓴다


@dataclass
class LensView:
    """렌즈 1개의 판독 결과."""

    key: str
    label: str
    question: str
    readings: list[LensReading] = field(default_factory=list)
    silent_reason: str = ""

    @property
    def usable(self) -> bool:
        return bool(self.readings)

    @property
    def stances(self) -> set[str]:
        return {r.stance for r in self.readings if r.stance}


@dataclass
class LensTension:
    """두 렌즈가 갈리는 지점. **이것이 「관전 포인트」가 된다.**"""

    left: str
    right: str
    text: str


@dataclass
class LensSet:
    """렌즈 전체 + 충돌."""

    views: list[LensView] = field(default_factory=list)
    tensions: list[LensTension] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return any(v.usable for v in self.views)

    def view(self, key: str) -> LensView | None:
        return next((v for v in self.views if v.key == key), None)


# ── 자본수익률 렌즈 ──────────────────────────────────────────────────
CAPITAL_RETURN = "capital_return"
DURABILITY = "durability"


def _trapped_asset_pct(sp: SegmentProfitSet | None) -> float | None:
    """적자 부문에 묶인 자산 비중(%).

    **부문 자산이 공시된 회사에서만 나온다.** 전사 자산총계로는 이 질문에
    답할 수 없다 — 어느 자산이 버는지가 부문 주석에만 있다.
    """
    if sp is None or not sp.usable:
        return None
    known = [x for x in sp.lines if x.assets]
    if len(known) < 2:
        return None
    total = sum(x.assets or 0 for x in known)
    if not total:
        return None
    trapped = sum(x.assets or 0 for x in known if x.is_loss)
    return trapped / total * 100.0


def build_capital_return_view(
    ms: MetricSet,
    valuation: ValuationSet | None,
    sp: SegmentProfitSet | None,
) -> LensView:
    """「투입 자본 대비 얼마를 버는가」."""
    view = LensView(
        key=CAPITAL_RETURN,
        label="자본수익률",
        question="이 회사는 투입한 자본 대비 얼마를 버는가",
    )

    trapped = _trapped_asset_pct(sp)
    if trapped is not None and trapped >= TRAPPED_ASSET_PCT:
        losers = ", ".join(x.name for x in (sp.loss_makers if sp else []))
        view.readings.append(
            LensReading(
                lens=CAPITAL_RETURN,
                claim=(
                    f"부문 자산의 상당 부분이 영업적자 부문({losers})에 묶여 있다. "
                    "자산이 크다는 사실과 그 자산이 번다는 사실은 다르고, "
                    "전사 지표만으로는 이 구분이 보이지 않는다."
                ),
                keys=["trapped_asset_share"],
                stance="negative",
            )
        )
    elif trapped is not None:
        view.readings.append(
            LensReading(
                lens=CAPITAL_RETURN,
                claim=(
                    "부문 자산이 대체로 흑자 부문에 배치돼 있다. "
                    "자산 구성이 수익성을 갉아먹는 구조는 아니다."
                ),
                stance="positive",
            )
        )

    # 부문 간 자본 효율 격차 — 같은 회사 안에서도 자산이 버는 정도가 다르다
    if sp is not None and sp.usable:
        ranked = [x for x in sp.lines if x.asset_return is not None]
        if len(ranked) >= 2:
            best = max(ranked, key=lambda x: x.asset_return or 0)
            worst = min(ranked, key=lambda x: x.asset_return or 0)
            view.readings.append(
                LensReading(
                    lens=CAPITAL_RETURN,
                    claim=(
                        f"자산 대비 영업이익이 가장 높은 부문은 {best.name}, 가장 낮은 부문은 "
                        f"{worst.name}이다. 이익률이 높아도 자산이 무거우면 자본 효율은 "
                        "다르게 읽힌다."
                    ),
                    keys=[],
                    stance="neutral",
                )
            )

        # 상각 부담 격차 — 영업이익률만 비교하면 자본집약도가 가려진다
        spread = [
            (x.ebitda_margin or 0) - (x.op_margin or 0)
            for x in sp.lines
            if x.ebitda_margin is not None and x.op_margin is not None
        ]
        if len(spread) >= 2 and (max(spread) - min(spread)) >= DEPRECIATION_SPREAD_PP:
            view.readings.append(
                LensReading(
                    lens=CAPITAL_RETURN,
                    claim=(
                        "부문 간 감가상각 부담의 차이가 크다. 영업이익률만 나란히 놓으면 "
                        "자본집약도가 다른 사업을 같은 종류의 수익성으로 읽게 된다."
                    ),
                    stance="neutral",
                )
            )

    # ROE와 ROA의 벌어짐은 레버리지에서 온다. 수익성과 차입을 섞어 읽으면 안 된다.
    #
    # **둘 다 양수일 때만 본다.** 적자면 부등호의 뜻이 뒤집힌다 — 손실 상태에서
    # 레버리지가 높으면 ROE는 ROA보다 더 **낮아진다**(더 큰 음수). 롯데케미칼에서
    # 이 조건이 그대로 발화해 반대로 말할 뻔했다.
    roe = valuation.roe if valuation else None
    roa = valuation.roa if valuation else None
    if roe is not None and roa is not None and roe > 0 and roa > 0 and roe > roa * 2:
        view.readings.append(
            LensReading(
                lens=CAPITAL_RETURN,
                claim=(
                    "자기자본이익률이 총자산이익률을 크게 웃돈다. 그 차이는 사업의 "
                    "수익성이 아니라 부채 사용에서 온다."
                ),
                keys=["roe", "roa", "debt_ratio"],
                stance="negative",
            )
        )

    if not view.readings:
        view.silent_reason = "부문 자산이 공시되지 않아 자본이 어디에 묶여 있는지 확인하지 못했다."
    return view


# ── 재현성 렌즈 ──────────────────────────────────────────────────────
def build_durability_view(
    ms: MetricSet,
    bridge: MarginBridge | None,
    sp: SegmentProfitSet | None,
) -> LensView:
    """「이번 이익은 반복되는가」."""
    view = LensView(
        key=DURABILITY,
        label="재현성",
        question="이번 이익은 다음에도 반복되는가",
    )

    if bridge is not None and bridge.reconciled:
        # 원가에서 온 개선은 외부 가격에 좌우된다. 판관비에서 온 개선은 회사가
        # 통제한 것이다. 같은 마진 개선이라도 재현성이 다르다.
        if "원가" in bridge.dominant:
            view.readings.append(
                LensReading(
                    lens=DURABILITY,
                    claim=(
                        "이익률 변화를 주도한 것은 원가율이다. 원가는 원재료·환율 등 "
                        "회사 밖 조건에 좌우되는 부분이 커서, 같은 폭의 개선이 다음 해에도 "
                        "반복된다고 보기 어렵다."
                    ),
                    keys=["bridge_cost_contrib", "cost_ratio_chg"],
                    stance="negative",
                )
            )
        else:
            view.readings.append(
                LensReading(
                    lens=DURABILITY,
                    claim=(
                        "이익률 변화를 주도한 것은 판관비율이다. 판관비는 원가보다 회사가 "
                        "통제할 여지가 커서, 구조적 변화라면 유지될 가능성이 있다."
                    ),
                    keys=["bridge_sga_contrib", "sga_ratio_chg"],
                    stance="positive",
                )
            )
    elif bridge is not None:
        view.readings.append(
            LensReading(
                lens=DURABILITY,
                claim=(
                    "영업이익이 매출에서 원가와 판관비를 뺀 값과 일치하지 않아, "
                    "이번 마진 변화를 비용 항목으로 분해하지 못했다. 무엇이 이익을 "
                    "움직였는지 단정하지 않는다."
                ),
                stance="neutral",
            )
        )

    # 부문별 방향이 갈리면 전사 공통 요인으로 설명할 수 없다
    if sp is not None and sp.usable:
        changed = [x for x in sp.lines if x.margin_change is not None]
        if len(changed) >= 2:
            up = [x for x in changed if (x.margin_change or 0) > 0]
            down = [x for x in changed if (x.margin_change or 0) < 0]
            if up and down:
                view.readings.append(
                    LensReading(
                        lens=DURABILITY,
                        claim=(
                            "부문별 이익률의 방향이 갈렸다. 전사 마진 변화를 업황 같은 "
                            "공통 요인으로 설명할 수 없고, 부문마다 재현 가능성을 따로 "
                            "봐야 한다."
                        ),
                        stance="neutral",
                    )
                )
            else:
                view.readings.append(
                    LensReading(
                        lens=DURABILITY,
                        claim=(
                            "모든 부문의 이익률이 같은 방향으로 움직였다. 회사 고유의 "
                            "실행보다 전사에 걸친 공통 요인이 작용했을 가능성이 크고, "
                            "그 요인이 지속되는지가 재현성을 가른다."
                        ),
                        stance="negative" if down else "positive",
                    )
                )

    # 영업단과 순이익단의 방향 불일치 → 영업외·법인세 요인
    op_yoy = _yoy(ms, "operating_income")
    net_yoy = _yoy(ms, "net_income")
    if op_yoy is not None and net_yoy is not None and (op_yoy > 0) != (net_yoy > 0):
        view.readings.append(
            LensReading(
                lens=DURABILITY,
                claim=(
                    "영업이익과 당기순이익의 증감 방향이 다르다. 차이는 영업외손익 또는 "
                    "법인세에서 오므로, 순이익만 보면 본업의 방향을 잘못 읽는다."
                ),
                keys=["operating_income_yoy", "net_income_yoy"],
                stance="negative",
            )
        )

    if not view.readings:
        view.silent_reason = "마진을 비용 항목으로 분해할 지표가 없어 재현성을 따지지 못했다."
    return view


def _yoy(ms: MetricSet, key: str) -> float | None:
    cur, prior = ms.get(key), ms.get_prior(key)
    if cur is None or prior is None or prior == 0:
        return None
    return (cur - prior) / abs(prior) * 100.0


# ── 충돌 ─────────────────────────────────────────────────────────────
def _stance(view: LensView | None) -> str | None:
    """렌즈의 종합 입장. 판독이 중립뿐이면 **입장이 없다**(None).

    핵심: **부정 판독이 없다는 것은 긍정이 아니다.** 삼성전자는 부문 자산을
    아예 공시하지 않아 자본수익률 렌즈가 확인한 게 없는데, 이걸 "긍정"으로
    세면 "자본은 버는 곳에 놓여 있다"는 **근거 없는 문장**이 리포트에 실린다.
    실제로 그렇게 나왔고 여기서 막는다.
    """
    if view is None or not view.usable:
        return None
    stances = view.stances
    if "negative" in stances:
        return "negative"
    if "positive" in stances:
        return "positive"
    return None


def find_tensions(views: list[LensView]) -> list[LensTension]:
    """렌즈가 갈리는 지점. **같은 말을 하면 싣지 않는다.**

    두 렌즈가 나란히 긍정이면 리포트에 보탤 게 없다. 관전 포인트는 "무엇이
    확인되면 판단이 달라지는가"이므로 **양쪽이 실제로 입장을 가질 때만** 성립한다.
    """
    out: list[LensTension] = []
    cap = next((v for v in views if v.key == CAPITAL_RETURN), None)
    dur = next((v for v in views if v.key == DURABILITY), None)
    cap_s, dur_s = _stance(cap), _stance(dur)
    if cap_s is None or dur_s is None:
        return out

    if cap_s == "negative" and dur_s == "positive":
        out.append(
            LensTension(
                left=CAPITAL_RETURN,
                right=DURABILITY,
                text=(
                    "이번 마진 변화를 이끈 요인은 회사가 통제할 여지가 있는 쪽이었지만, "
                    "자본은 버는 곳에 놓여 있지 않다. 다음 공시에서 적자 부문의 자산이 "
                    "줄어드는지, 아니면 그대로 유지되는지가 이 회사를 어떻게 볼지 가른다."
                ),
            )
        )
    elif cap_s == "positive" and dur_s == "negative":
        out.append(
            LensTension(
                left=DURABILITY,
                right=CAPITAL_RETURN,
                text=(
                    "자본은 버는 부문에 배치돼 있으나 이번 이익의 재현성은 확인되지 않았다. "
                    "다음 공시에서 이익률을 움직인 요인이 같은 방향으로 이어지는지를 "
                    "먼저 확인해야 한다."
                ),
            )
        )
    elif cap_s == "negative" and dur_s == "negative":
        out.append(
            LensTension(
                left=CAPITAL_RETURN,
                right=DURABILITY,
                text=(
                    "자본 배치와 이익의 재현성이 함께 확인되지 않는다. 두 축이 같은 방향을 "
                    "가리키므로, 저평가로 보이는 지표가 있다면 그것이 원인이 아니라 결과일 "
                    "가능성을 먼저 따져야 한다."
                ),
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
    views = [
        build_capital_return_view(ms, valuation, segment_profit),
        build_durability_view(ms, bridge, segment_profit),
    ]
    return LensSet(views=views, tensions=find_tensions(views))


# ── Number Registry ──────────────────────────────────────────────────
def build_lens_entries(
    lenses: LensSet, sp: SegmentProfitSet | None, prov: Provenance, fiscal_year: int
) -> list[NumberEntry]:
    """렌즈가 **새로 계산한** 수치만 등록한다. 나머지는 이미 등록돼 있다."""
    out: list[NumberEntry] = []
    trapped = _trapped_asset_pct(sp)
    if trapped is None or sp is None:
        return out

    known = [x for x in sp.lines if x.assets]
    trapped_amount = sum(x.assets or 0 for x in known if x.is_loss)
    y = fiscal_year
    out.append(
        NumberEntry(
            key=f"trapped_asset_{y}a",
            value=trapped_amount,
            unit="원",
            display=fmt_krw(trapped_amount),
            provenance=prov,
            label=f"영업적자 부문의 자산 ({y}A)",
            formula="영업적자 부문의 부문 자산 합계",
        )
    )
    out.append(
        NumberEntry(
            key=f"trapped_asset_share_{y}a",
            value=trapped,
            unit="%",
            # 라벨에 **분모를 적는다.** 실측: "영업적자 부문의 자산 비중"만 주니
            # LLM이 "자산총계의 75.9%"라고 썼는데, 분모는 부문 자산 합계다.
            # 부문 자산은 내부거래 때문에 자산총계와 다르다(롯데케미칼 35.4조 vs 31.1조).
            # 값이 맞아도 무엇에 대한 비중인지가 틀리면 독자가 오해한다.
            display=fmt_pct(trapped),
            provenance=prov,
            label=f"부문 자산 합계 대비 영업적자 부문의 자산 비중 ({y}A)",
            formula="영업적자 부문 자산 / 부문 자산이 확인된 부문의 합계 (자산총계가 아니다)",
            inputs=[f"trapped_asset_{y}a"],
        )
    )
    return out


# ── 논지 ─────────────────────────────────────────────────────────────
def build_lens_observations(lenses: LensSet) -> list[str]:
    """렌즈 논지. **크기를 쓰지 않는다** (D16)."""
    obs: list[str] = []
    for view in lenses.views:
        if not view.usable:
            continue
        obs.append(f"[{view.label} 관점] {view.question}?")
        obs.extend(f"[{view.label}] {r.claim}" for r in view.readings)
    for t in lenses.tensions:
        obs.append(f"[관점 충돌] {t.text}")
    return obs
