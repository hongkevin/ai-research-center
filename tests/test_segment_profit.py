"""부문별 손익 — IFRS 8 주석 파싱과 검산.

격자는 전부 **실측 원문**에서 뜬 것이다(FY2025 사업보고서). 회사마다 표
모양이 다르고 그 차이가 파서를 깨뜨리는 지점이라, 합성 데이터로는 회귀를
못 잡는다.
"""

from __future__ import annotations

import datetime as dt

import pytest

from arc.data.base import Provenance
from arc.data.kr.dart_document import Section
from arc.finmodel.metrics import MetricSet, MetricValue
from arc.finmodel.segment_profit import (
    SegmentProfitLine,
    SegmentProfitSet,
    build_segment_profit,
    build_segment_profit_entries,
    build_segment_profit_observations,
    read_segment_grid,
)

# ── 실측 격자 ────────────────────────────────────────────────────────
# 삼성전자 「30. 부문별 보고 (연결)」. 단위 백만원.
# 부문명이 **반복된다** — 왼쪽은 영업부문, 오른쪽은 내부거래 조정이다.
SAMSUNG_CURRENT = [
    ["", "기업 전체 총계", "", "", "", "", "", "", "", "", "", "기업 전체 총계 합계"],
    ["", "영업부문", "", "", "", "", "내부거래 조정 등", "", "", "", "", "기업 전체 총계 합계"],
    ["", "부문", "", "", "", "부문 합계", "부문", "", "", "", "부문 합계", "기업 전체 총계 합계"],
    [
        "",
        "보고부문",
        "",
        "",
        "",
        "부문 합계",
        "보고부문",
        "",
        "",
        "",
        "부문 합계",
        "기업 전체 총계 합계",
    ],
    [
        "",
        "DX 부문",
        "DS 부문",
        "SDC",
        "Harman",
        "부문 합계",
        "DX 부문",
        "DS 부문",
        "SDC",
        "Harman",
        "부문 합계",
        "기업 전체 총계 합계",
    ],
    [
        "매출액",
        "187,967,346",
        "130,128,162",
        "29,841,661",
        "15,783,325",
        "363,720,494",
        "",
        "",
        "",
        "",
        "(30,114,556)",
        "333,605,938",
    ],
    [
        "감가상각비",
        "2,670,815",
        "37,957,308",
        "2,442,593",
        "357,912",
        "43,428,628",
        "",
        "",
        "",
        "",
        "0",
        "43,605,740",
    ],
    [
        "무형자산상각비",
        "1,815,140",
        "827,043",
        "233,739",
        "205,660",
        "3,081,582",
        "",
        "",
        "",
        "",
        "0",
        "3,320,852",
    ],
    [
        "영업이익",
        "12,852,650",
        "24,858,075",
        "4,116,308",
        "1,531,094",
        "43,358,127",
        "",
        "",
        "",
        "",
        "0",
        "43,601,051",
    ],
]
SAMSUNG_PRIOR = [
    SAMSUNG_CURRENT[0],
    SAMSUNG_CURRENT[1],
    SAMSUNG_CURRENT[2],
    SAMSUNG_CURRENT[3],
    SAMSUNG_CURRENT[4],
    [
        "매출액",
        "174,887,683",
        "111,065,950",
        "29,157,820",
        "14,274,930",
        "329,386,383",
        "",
        "",
        "",
        "",
        "(28,515,480)",
        "300,870,903",
    ],
    [
        "영업이익",
        "12,439,897",
        "15,094,486",
        "3,733,429",
        "1,307,580",
        "32,575,392",
        "",
        "",
        "",
        "",
        "0",
        "32,725,961",
    ],
]

# LG전자 「4. 부문별 정보 (연결)」. 부문명 행 **아래**에 약칭·제품유형 행이
# 더 있다 — 0열에 라벨이 있어 머리 행이 아니다.
LG_CURRENT = [
    ["", "부문", "", "", "", "", "", "부문 합계"],
    ["", "보고부문", "", "", "", "", "기타부문", "부문 합계"],
    [
        "",
        "Home Appliance Solution",
        "Media Entertainment Solution",
        "Vehicle Solution",
        "Eco Solution",
        "엘지이노텍㈜와 그 종속기업",
        "기타부문",
        "부문 합계",
    ],
    [
        "보고부문을 식별하기 위하여 사용한 요소",
        "",
        "",
        "",
        "",
        "",
        "",
        "연결회사의 보고부문인 사업본부",
    ],
    ["각 보고부문의 약칭", "HS", "MS", "VS", "ES", "이노텍", "기타", ""],
    [
        "주요 제품 유형",
        "냉장고, 세탁기",
        "TV, Audio",
        "자동차 부품",
        "에어컨",
        "카메라모듈",
        "설비제작",
        "",
    ],
    [
        "매출액",
        "25,737,491",
        "19,410,512",
        "11,135,747",
        "9,303,319",
        "21,463,540",
        "2,150,273",
        "89,200,882",
    ],
    [
        "영업이익(손실)",
        "1,279,283",
        "(750,856)",
        "558,987",
        "647,291",
        "665,007",
        "78,680",
        "2,478,392",
    ],
    [
        "감가상각비 및 무형자산상각비",
        "889,660",
        "449,462",
        "652,546",
        "216,185",
        "1,150,301",
        "245,709",
        "3,603,863",
    ],
]

# LG전자 부문별 자산 — **손익과 다른 표**다. 총계는 재무상태표 자산총계와 맞는다.
LG_ASSETS = [
    ["", "기업 전체 총계", "", "", "", "", "", "기업 전체 총계 합계"],
    [
        "",
        "Home Appliance Solution",
        "Media Entertainment Solution",
        "Vehicle Solution",
        "Eco Solution",
        "엘지이노텍㈜와 그 종속기업",
        "기타부문 및 내부거래",
        "기업 전체 총계 합계",
    ],
    [
        "자산",
        "25,455,949",
        "23,300,959",
        "10,452,461",
        "10,015,587",
        "11,930,883",
        "(12,535,672)",
        "68,620,167",
    ],
    [
        "부채",
        "14,376,287",
        "16,691,545",
        "11,092,592",
        "4,167,901",
        "6,167,829",
        "(12,428,012)",
        "40,068,142",
    ],
]

# 롯데케미칼 「3. 부문정보 (연결)」. 단위 **천원**, 라벨은 「매출」, 적자 부문.
LOTTE_CURRENT = [
    ["", "기업 전체 총계", "", "", "", "", "", "", "", "", "기업 전체 총계 합계"],
    ["", "영업부문", "", "", "", "중요한 조정사항", "", "", "", "", "기업 전체 총계 합계"],
    [
        "",
        "기초화학사업부",
        "첨단소재사업부",
        "정밀화학사업부",
        "전지소재사업부",
        "부문 합계",
        "기초화학사업부",
        "첨단소재사업부",
        "정밀화학사업부",
        "전지소재사업부",
        "기업 전체 총계 합계",
    ],
    [
        "매출",
        "12,480,062,735",
        "5,086,712,871",
        "1,752,682,648",
        "677,530,807",
        "19,996,989,061",
        "",
        "",
        "",
        "(1,513,983,746)",
        "18,483,005,315",
    ],
    [
        "영업이익(손실)",
        "(857,663,872)",
        "123,687,307",
        "74,361,153",
        "(158,486,289)",
        "(818,101,701)",
        "",
        "",
        "",
        "(125,014,029)",
        "(943,115,730)",
    ],
]

# 같은 주석에 실리는 **지역별** 표. 영업이익 행이 없다 → 부문 손익이 아니다.
CJ_REGION = [
    ["", "지역", "", "", "지역 합계"],
    ["", "본사 소재지 국가", "아시아", "아메리카", "지역 합계"],
    ["수익(매출액)", "16,381,244,757", "6,369,655,121", "13,074,663,980", "36,979,254,301"],
]

PROV = Provenance(
    source="DART",
    source_url="https://opendart.fss.or.kr",
    retrieved_at=dt.datetime(2026, 8, 1, tzinfo=dt.UTC),
)


def _metrics(year: int, revenue: int, op: int, revenue_prior=None, op_prior=None) -> MetricSet:
    values = {
        "revenue": MetricValue(key="revenue", label="매출액", current=revenue, prior=revenue_prior),
        "operating_income": MetricValue(
            key="operating_income", label="영업이익", current=op, prior=op_prior
        ),
    }
    return MetricSet(fiscal_year=year, values=values)


def _section(*grids: list[list[str]], unit: str = "백만원") -> Section:
    """격자를 DART 표 XML로 되돌린다 — 파서 입구가 원문이므로 거기서 시작한다."""
    body = [f"<P>(단위 : {unit})</P>"]
    for grid in grids:
        rows = "".join("<TR>" + "".join(f"<TD>{c}</TD>" for c in row) + "</TR>" for row in grid)
        body.append(f"<TABLE>{rows}</TABLE>")
    return Section(title="30. 부문별 보고 (연결)", start=0, body="".join(body))


# ── 격자 읽기 ────────────────────────────────────────────────────────
def test_reads_transposed_grid_and_picks_segment_name_row():
    g = read_segment_grid(SAMSUNG_CURRENT)
    assert g.names[1:5] == ["DX 부문", "DS 부문", "SDC", "Harman"]
    assert g.revenue[1] == 187_967_346
    assert g.profit[11] == 43_601_051


def test_header_row_is_the_last_one_with_empty_first_column():
    """LG전자는 부문명 행 아래에 「각 보고부문의 약칭」 행이 더 있다.
    0열에 라벨이 있으므로 머리 행이 아니고, 약칭이 부문명을 덮으면 안 된다."""
    g = read_segment_grid(LG_CURRENT)
    assert g.names[1] == "Home Appliance Solution"
    assert "HS" not in g.names


def test_split_depreciation_rows_are_summed():
    """삼성전자는 「감가상각비」와 「무형자산상각비」를 따로 쓴다. 하나만 읽으면
    EBITDA가 틀린다."""
    g = read_segment_grid(SAMSUNG_CURRENT)
    assert g.depreciation[1] == 2_670_815 + 1_815_140  # DX


def test_combined_depreciation_row_is_not_double_counted():
    """LG전자는 「감가상각비 및 무형자산상각비」로 합쳐 쓴다. 합산 행과 개별
    행을 함께 더하면 이중 계상된다."""
    g = read_segment_grid(LG_CURRENT)
    assert g.depreciation[1] == 889_660


def test_table_without_operating_income_is_not_a_segment_profit_table():
    """지역별 매출·주요 고객 표를 부문 손익으로 오인하면 안 된다."""
    assert read_segment_grid(CJ_REGION) is None


# ── 검산 ─────────────────────────────────────────────────────────────
def test_samsung_reconciles_against_income_statement():
    ms = _metrics(2025, 333_605_938_000_000, 43_601_051_000_000)
    sp = build_segment_profit([_section(SAMSUNG_CURRENT)], ms, rcept_no="R")
    assert sp.usable
    assert [x.name for x in sp.lines] == ["DX 부문", "DS 부문", "SDC", "Harman"]
    assert sp.lines[0].revenue == 187_967_346_000_000
    assert sp.lines[1].operating_income == 24_858_075_000_000
    assert sp.revenue_gap_pct == pytest.approx(0.0, abs=1e-6)
    assert sp.op_gap_pct == pytest.approx(0.0, abs=1e-6)


def test_adjustment_columns_are_excluded_even_though_names_repeat():
    """부문명이 조정 열에서 되풀이된다. 「부문 합계」에서 끊지 않으면 부문이
    두 배로 잡힌다."""
    ms = _metrics(2025, 333_605_938_000_000, 43_601_051_000_000)
    sp = build_segment_profit([_section(SAMSUNG_CURRENT)], ms)
    assert len(sp.lines) == 4


def test_unit_scale_comes_from_caption_but_reconciliation_decides():
    """캡션이 다른 표의 단위여도 검산이 맞는 배율을 고른다 — 단위를 잘못
    읽으면 6자리가 어긋난 금액이 리포트에 실린다."""
    ms = _metrics(2025, 18_483_005_314_922, -943_115_729_953)
    sp = build_segment_profit([_section(LOTTE_CURRENT, unit="백만원")], ms)
    assert sp.usable
    assert sp.unit_scale == 1_000
    assert sp.lines[0].operating_income == -857_663_872_000


def test_parenthesised_amounts_are_negative():
    """회계 표기의 괄호는 음수다. 부호를 놓치면 적자 부문이 흑자가 된다."""
    ms = _metrics(2025, 18_483_005_314_922, -943_115_729_953)
    sp = build_segment_profit([_section(LOTTE_CURRENT)], ms)
    assert [x.name for x in sp.loss_makers] == ["기초화학사업부", "전지소재사업부"]
    assert sp.lines[3].op_margin == pytest.approx(-23.4, abs=0.1)


def test_mismatched_totals_are_rejected():
    """총계가 손익계산서와 다르면 쓰지 않는다 — 별도 주석을 연결 분석에
    끌어다 쓰는 경로가 여기서 막힌다."""
    ms = _metrics(2025, 100_000_000_000_000, 5_000_000_000_000)
    sp = build_segment_profit([_section(SAMSUNG_CURRENT)], ms)
    assert not sp.usable
    assert "맞지 않아" in sp.note


def test_revenue_matching_but_profit_mismatching_is_rejected():
    """매출만 맞고 영업이익이 어긋나면 통과하지 못한다 — 두 값이 같은 열에서
    동시에 맞아야 열을 옳게 고른 것이다."""
    ms = _metrics(2025, 333_605_938_000_000, 20_000_000_000_000)
    assert not build_segment_profit([_section(SAMSUNG_CURRENT)], ms).usable


def test_no_segment_profit_table_is_reported_as_coverage_not_failure():
    """단일 영업부문은 공시 의무가 없다. 파싱 실패와 뭉뚱그리면 진단이 막힌다."""
    ms = _metrics(2025, 27_342_589_100_000, 1_233_604_641_000)
    sp = build_segment_profit([_section(CJ_REGION)], ms)
    assert not sp.usable
    assert "공시하지 않았다" in sp.note


def test_the_right_section_is_chosen_by_reconciliation_not_by_title():
    """연결·별도 주석이 함께 실린다. 제목으로는 못 가르고 검산이 가른다."""
    ms = _metrics(2025, 18_483_005_314_922, -943_115_729_953)
    sp = build_segment_profit([_section(SAMSUNG_CURRENT), _section(LOTTE_CURRENT)], ms)
    assert sp.usable
    assert sp.lines[0].name == "기초화학사업부"


# ── 전기 비교 ────────────────────────────────────────────────────────
def test_prior_year_table_yields_margin_change():
    ms = _metrics(
        2025,
        333_605_938_000_000,
        43_601_051_000_000,
        revenue_prior=300_870_903_000_000,
        op_prior=32_725_961_000_000,
    )
    sp = build_segment_profit([_section(SAMSUNG_CURRENT, SAMSUNG_PRIOR)], ms)
    assert sp.has_prior
    ds = sp.lines[1]
    assert ds.op_margin_prior == pytest.approx(13.59, abs=0.05)
    assert ds.margin_change == pytest.approx(5.51, abs=0.05)


def test_current_table_is_never_reused_as_its_own_prior():
    """실적이 전년과 1% 안쪽이면 같은 표가 전기로도 맞는다. 그걸 전기로
    쓰면 「변화 없음」이라는 거짓이 표에 실린다."""
    ms = _metrics(
        2025,
        333_605_938_000_000,
        43_601_051_000_000,
        revenue_prior=333_605_938_000_000,
        op_prior=43_601_051_000_000,
    )
    sp = build_segment_profit([_section(SAMSUNG_CURRENT)], ms)
    assert sp.usable
    assert not sp.has_prior
    assert all(x.margin_change is None for x in sp.lines)


# ── 레지스트리 ───────────────────────────────────────────────────────
def _built(grid=SAMSUNG_CURRENT) -> SegmentProfitSet:
    ms = _metrics(
        2025,
        333_605_938_000_000,
        43_601_051_000_000,
        revenue_prior=300_870_903_000_000,
        op_prior=32_725_961_000_000,
    )
    return build_segment_profit([_section(grid, SAMSUNG_PRIOR)], ms)


def test_every_number_is_registered_with_provenance():
    entries = build_segment_profit_entries(_built(), PROV)
    keys = {e.key for e in entries}
    assert "opseg2_margin_2025a" in keys
    assert "opseg2_margin_chg_2025a" in keys
    assert all(e.provenance is not None for e in entries)


def test_margin_change_is_signed_and_in_pp():
    """`5.5%`로 쓰면 수준으로 읽힌다. 증감은 부호와 pp로 쓴다."""
    entry = next(
        e
        for e in build_segment_profit_entries(_built(), PROV)
        if e.key == "opseg2_margin_chg_2025a"
    )
    assert entry.unit == "pp"
    assert entry.display == "+5.5pp"


def test_reconciliation_gap_is_internal_only():
    """검산값은 감사용이다 (D17) — 레지스트리에는 남고 카탈로그에서는 빠진다."""
    entries = build_segment_profit_entries(_built(), PROV)
    gaps = [e for e in entries if e.key.startswith("opseg_")]
    assert gaps and all(e.internal for e in gaps)


def test_unusable_set_registers_nothing():
    ms = _metrics(2025, 1, 1)
    assert (
        build_segment_profit_entries(build_segment_profit([_section(SAMSUNG_CURRENT)], ms), PROV)
        == []
    )


def test_profit_share_is_withheld_when_a_segment_loses_money():
    """적자가 섞이면 「이익의 105%」 같은 비중이 나온다. 그건 내지 않는다."""
    ms = _metrics(2025, 18_483_005_314_922, -943_115_729_953)
    sp = build_segment_profit([_section(LOTTE_CURRENT)], ms)
    keys = {e.key for e in build_segment_profit_entries(sp, PROV)}
    assert not any(k.endswith("_op_share_2025a") for k in keys)
    assert "opseg1_rev_share_2025a" in keys


# ── 논지 ─────────────────────────────────────────────────────────────
def test_observations_carry_no_magnitudes():
    """프롬프트에 들어간 숫자는 LLM이 리터럴로 베낀다 (D16). 방향과 우열만 쓴다."""
    import re

    for text in build_segment_profit_observations(_built()):
        assert not re.search(r"\d", text), text


def test_observation_names_the_profit_leader_when_it_differs_from_revenue_leader():
    obs = " ".join(build_segment_profit_observations(_built()))
    assert "매출이 가장 큰 부문은 DX 부문이지만" in obs
    assert "DS 부문" in obs


def test_profit_driver_is_ranked_by_profit_change_not_margin_swing():
    """매출 비중 2%짜리 부문의 이익률이 10pp 흔들려도 전사에는 영향이 거의
    없다. 전사를 끌고 간 부문은 이익 **변화액**으로 가른다."""
    lines = [
        SegmentProfitLine("큰 부문", 900, 90, revenue_prior=900, op_prior=10),  # +80
        SegmentProfitLine("작은 부문", 100, 5, revenue_prior=100, op_prior=20),  # -15, -15pp
    ]
    sp = SegmentProfitSet(fiscal_year=2025, lines=lines, reconciled=True, has_prior=True)
    obs = " ".join(build_segment_profit_observations(sp))
    assert "가장 크게 끌고 간 부문은 큰 부문" in obs


def test_single_leader_claim_is_softened_when_the_leader_is_not_dominant():
    """6개 부문 중 매출 29%짜리 1위에 「전사가 이 부문을 따라간다」를 붙이면
    과장이다 (LG전자 실측)."""
    ms = _metrics(2025, 89_200_882_000_000, 2_478_392_000_000)
    sp = build_segment_profit([_section(LG_CURRENT)], ms)
    assert sp.usable and not sp.leaders_differ
    obs = " ".join(build_segment_profit_observations(sp))
    assert "사실상 같이 움직인다" not in obs
    assert "한 부문으로 설명할 수는 없다" in obs


def test_loss_making_segment_is_called_out():
    ms = _metrics(2025, 89_200_882_000_000, 2_478_392_000_000)
    obs = " ".join(
        build_segment_profit_observations(build_segment_profit([_section(LG_CURRENT)], ms))
    )
    assert "Media Entertainment Solution은 영업적자다" in obs


def test_unusable_set_yields_no_observations():
    assert build_segment_profit_observations(SegmentProfitSet(fiscal_year=2025)) == []


# ── 감가상각·EBITDA ──────────────────────────────────────────────────
def test_ebitda_separates_segments_with_different_capital_intensity():
    """삼성전자 DS의 영업이익률은 19.1%지만 상각 전으로는 48.9%다. 영업이익률만
    비교하면 DX(9.2%)와 같은 종류의 수익성으로 읽힌다."""
    sp = _built()
    ds = next(x for x in sp.lines if x.name == "DS 부문")
    dx = next(x for x in sp.lines if x.name == "DX 부문")
    assert ds.op_margin == pytest.approx(19.1, abs=0.1)
    assert ds.ebitda_margin == pytest.approx(48.9, abs=0.1)
    assert dx.ebitda_margin == pytest.approx(9.2, abs=0.1)


def test_capital_intensity_claim_skips_a_residual_bucket():
    """LG전자 「기타부문」은 매출 비중 2.4%인데 상각 부담이 가장 무겁다.
    회사의 사업을 말하는 문장이 잔여 버킷으로 시작하면 안 된다."""
    ms = _metrics(2025, 89_200_882_000_000, 2_478_392_000_000)
    sp = build_segment_profit([_section(LG_CURRENT)], ms)
    obs = " ".join(build_segment_profit_observations(sp))
    assert "감가상각 부담이 가장 무거운 부문은 기타부문" not in obs
    assert "감가상각 부담이 가장 무거운 부문은 Vehicle Solution" in obs


def test_no_depreciation_row_leaves_ebitda_empty():
    """상각비가 없으면 EBITDA를 만들지 않는다 — 영업이익을 EBITDA로 쓰면
    자본집약도가 0인 것처럼 보인다."""
    grid = [r for r in LOTTE_CURRENT]
    ms = _metrics(2025, 18_483_005_314_922, -943_115_729_953)
    sp = build_segment_profit([_section(grid)], ms)
    assert all(x.ebitda is None for x in sp.lines)
    keys = {e.key for e in build_segment_profit_entries(sp, PROV)}
    assert not any("_ebitda" in k for k in keys)


# ── 부문 자산 ────────────────────────────────────────────────────────
def _lg_with_assets(total_assets: int = 68_620_167_000_000) -> SegmentProfitSet:
    ms = _metrics(2025, 89_200_882_000_000, 2_478_392_000_000)
    ms.values["total_assets"] = MetricValue(
        key="total_assets", label="자산총계", current=total_assets, prior=None
    )
    return build_segment_profit([_section(LG_CURRENT, LG_ASSETS)], ms)


def test_assets_are_matched_through_the_disclosed_abbreviation_row():
    """LG전자의 자산 표는 부문을 약칭(HS·MS)으로 부른다. 위치로 짜맞추지 않고
    손익 표의 「각 보고부문의 약칭」 행이 준 대응을 쓴다."""
    sp = _lg_with_assets()
    hs = next(x for x in sp.lines if x.name == "Home Appliance Solution")
    assert hs.assets == 25_455_949_000_000
    assert hs.asset_return == pytest.approx(5.0, abs=0.1)


def test_a_segment_without_a_counterpart_in_the_asset_table_stays_empty():
    """LG전자 자산 표는 「기타부문 및 내부거래」로 묶여 있어 손익 표의
    「기타부문」과 짝이 없다. 전부 버리면 나머지 다섯의 자산까지 잃는다."""
    sp = _lg_with_assets()
    other = next(x for x in sp.lines if x.name == "기타부문")
    assert other.assets is None
    assert sum(1 for x in sp.lines if x.assets is not None) == 5


def test_assets_are_rejected_when_the_total_misses_the_balance_sheet():
    sp = _lg_with_assets(total_assets=10_000_000_000_000)
    assert all(x.assets is None for x in sp.lines)


def test_assets_are_not_read_without_a_balance_sheet_total():
    """자산총계가 없으면 검산할 근거가 없다. 검산 없이 쓰지 않는다."""
    ms = _metrics(2025, 89_200_882_000_000, 2_478_392_000_000)
    sp = build_segment_profit([_section(LG_CURRENT, LG_ASSETS)], ms)
    assert all(x.assets is None for x in sp.lines)


# ── 부문 구분은 한 리포트에 하나만 ───────────────────────────────────
def _compose(sp: SegmentProfitSet | None):
    """부문 매출 표(D28)와 부문 손익(D33)을 함께 넣고 섹션을 조립한다."""
    from arc.finmodel.segments import SegmentBreakdown, SegmentLine, build_segment_entries
    from arc.llm.number_registry import NumberRegistry
    from arc.pipeline.earnings_review import compose_sections

    ms = _metrics(2025, 333_605_938_000_000, 43_601_051_000_000)
    seg = SegmentBreakdown(
        fiscal_year=2025,
        lines=[
            SegmentLine(name="제ㆍ상품", amount=314_717_100_000_000, share=94.3),
            SegmentLine(name="용역및기타매출", amount=18_888_800_000_000, share=5.7),
        ],
        total=333_605_900_000_000,
        revenue=333_605_938_000_000,
        reconciled=True,
    )
    registry = NumberRegistry()
    registry.register_all(build_segment_entries(seg, PROV))
    if sp is not None:
        registry.register_all(build_segment_profit_entries(sp, PROV))
    return compose_sections(ms, registry, segments=seg, segment_profit=sp)


def test_revenue_segmentation_is_dropped_when_operating_segments_exist():
    """같은 리포트에서 「부문」이 두 뜻이 되면 안 된다 — 실측: 앞은 제ㆍ상품/용역
    2개, 뒤는 DX·DS·SDC·Harman 4개였다."""
    sections = _compose(_built())
    assert sections["business"]["segment_table"] == []
    assert len(sections["earnings"]["segment_profit"]["table"]) == 4


def test_revenue_segmentation_survives_when_there_are_no_operating_segments():
    """주석이 없는 회사에서는 D28 표가 유일한 부문 정보다. 같이 없애면 안 된다."""
    sections = _compose(None)
    assert len(sections["business"]["segment_table"]) == 2
