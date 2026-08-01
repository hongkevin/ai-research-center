"""정기보고서 주요정보 파싱 테스트.

픽스처는 **실제 OpenDART 응답에서 관측된 형태**다. 여기 있는 엣지 케이스는
전부 실측에서 나왔고, 각각 리포트에 틀린 숫자를 넣을 뻔했다:

  * 자기주식 `-` (파마리서치) → 검산 실패로 주식수를 못 쓸 뻔
  * `성별합계` 행 (삼성전자) → 총원이 정확히 2배
  * `fo_bbm`이 직군 (셀트리온제약) → 직군을 사업부문으로 서술할 뻔
  * KAM 구분자가 개행 없이 붙음 → 두 항목이 한 문장으로
  * `isu_stock_totqy`는 수권주식수 → 삼성전자 주식수가 3배로
"""

from __future__ import annotations

import datetime as dt

import pytest

from arc.data.kr.dart_reports import (
    parse_audit_opinion,
    parse_dividend,
    parse_number,
    parse_ratio,
    parse_share_counts,
    parse_workforce,
    split_kam,
)

NOW = dt.datetime(2026, 8, 1, tzinfo=dt.UTC)


def _shares(se, istc, tes, distb):
    return {
        "rcept_no": "20260311000001",
        "se": se,
        "isu_stock_totqy": "20,000,000,000",  # 수권주식수 — 쓰면 안 되는 필드
        "istc_totqy": istc,
        "tesstk_co": tes,
        "distb_stock_co": distb,
    }


# ── 값 파싱 ──────────────────────────────────────────────────────────
class TestValueParsing:
    @pytest.mark.parametrize(
        ("raw", "want"),
        [
            ("1,234", 1234),
            ("5,919,637,922", 5_919_637_922),
            ("0", 0),
            ("(1,234)", -1234),  # 회계 음수 표기
            ("-", None),
            ("", None),
            ("—", None),
            (None, None),
        ],
    )
    def test_parse_number(self, raw, want):
        assert parse_number(raw) == want

    def test_zero_and_absent_are_different(self):
        """`-`는 0이 아니다. 0으로 읽으면 '자기주식 없음'과 '미공시'가 섞인다."""
        assert parse_number("0") == 0
        assert parse_number("-") is None

    @pytest.mark.parametrize(
        ("raw", "want"), [("25.10", 25.1), ("1.5", 1.5), ("0.32", 0.32), ("-", None), ("", None)]
    )
    def test_parse_ratio(self, raw, want):
        assert parse_ratio(raw) == want


# ── 주식의 총수 ──────────────────────────────────────────────────────
class TestShareCounts:
    def test_uses_issued_not_authorized(self):
        """`isu_stock_totqy`(수권주식수)를 쓰면 삼성전자 주식수가 3배가 된다."""
        rows = [
            _shares("보통주", "5,919,637,922", "91,828,987", "5,827,808,935"),
            _shares("합계", "6,735,612,586", "105,432,448", "6,630,180,138"),
        ]
        s = parse_share_counts(rows, 2025, NOW)
        assert s is not None
        assert s.issued == 6_735_612_586
        assert s.issued != 20_000_000_000
        assert s.reconciled

    def test_treasury_dash_resolved_by_identity(self):
        """자기주식 `-`는 0이다 — 발행 == 유통이므로 항등식이 확정한다."""
        rows = [_shares("합계", "11,565,295", "-", "11,565,295")]
        s = parse_share_counts(rows, 2025, NOW)
        assert s is not None
        assert s.treasury == 0
        assert s.reconciled

    def test_mismatch_is_flagged_not_hidden(self):
        rows = [_shares("합계", "1,000,000", "10,000", "900,000")]
        s = parse_share_counts(rows, 2025, NOW)
        assert s is not None
        assert not s.reconciled

    def test_falls_back_to_common_when_no_total(self):
        rows = [_shares("보통주", "728,002,365", "26,310,845", "701,691,520")]
        s = parse_share_counts(rows, 2025, NOW)
        assert s is not None
        assert s.issued == 728_002_365

    def test_preferred_detected(self):
        rows = [
            _shares("보통주", "5,919,637,922", "91,828,987", "5,827,808,935"),
            _shares("우선주", "815,974,664", "13,603,461", "802,371,203"),
            _shares("합계", "6,735,612,586", "105,432,448", "6,630,180,138"),
        ]
        s = parse_share_counts(rows, 2025, NOW)
        assert s is not None and s.has_preferred

    def test_no_preferred(self):
        rows = [_shares("합계", "728,002,365", "26,310,845", "701,691,520")]
        s = parse_share_counts(rows, 2025, NOW)
        assert s is not None and not s.has_preferred

    def test_empty_returns_none(self):
        assert parse_share_counts([], 2025, NOW) is None


# ── 배당 ─────────────────────────────────────────────────────────────
def _div_rows():
    def r(se, thstrm, kind="-"):
        return {"rcept_no": "20260311000001", "se": se, "stock_knd": kind, "thstrm": thstrm}

    return [
        r("주당액면가액(원)", "100"),
        r("(연결)당기순이익(백만원)", "44,260,956"),
        r("(연결)주당순이익(원)", "6,605"),
        r("현금배당금총액(백만원)", "11,107,906"),
        r("(연결)현금배당성향(%)", "25.10"),
        r("현금배당수익률(%)", "1.50", "보통주"),
        r("현금배당수익률(%)", "1.90", "우선주"),
        r("주식배당수익률(%)", "-", "보통주"),
        r("주당 현금배당금(원)", "1,668", "보통주"),
        r("주당 현금배당금(원)", "1,669", "우선주"),
    ]


class TestDividend:
    def test_reads_common_share_values(self):
        d = parse_dividend(_div_rows(), 2025, NOW)
        assert d is not None
        assert d.dps_common == 1668
        assert d.dps_preferred == 1669
        assert d.dividend_yield_common == 1.50
        assert d.payout_ratio == 25.10
        assert d.eps_reported == 6605
        assert d.par_value == 100

    def test_total_dividend_converted_from_millions(self):
        """공시는 백만원 단위다. 환산하지 않으면 3자리가 어긋난다."""
        d = parse_dividend(_div_rows(), 2025, NOW)
        assert d is not None
        assert d.total_cash_dividend == 11_107_906 * 1_000_000

    def test_stock_dividend_yield_not_mistaken_for_cash(self):
        """'주식배당수익률'이 '현금배당수익률' 검색에 걸리면 안 된다."""
        d = parse_dividend(_div_rows(), 2025, NOW)
        assert d is not None
        assert d.dividend_yield_common == 1.50  # 주식배당수익률의 '-'가 아님

    def test_implied_price_from_dps_and_yield(self):
        d = parse_dividend(_div_rows(), 2025, NOW)
        assert d is not None
        assert d.implied_price == round(1668 / 0.015)

    def test_implied_price_none_without_yield(self):
        rows = [
            r
            for r in _div_rows()
            if "수익률" not in r["se"]  # 수익률 행 제거
        ]
        d = parse_dividend(rows, 2025, NOW)
        assert d is not None
        assert d.implied_price is None

    def test_no_dividend_company(self):
        rows = [
            {"rcept_no": "x", "se": "주당 현금배당금(원)", "stock_knd": "보통주", "thstrm": "-"}
        ]
        d = parse_dividend(rows, 2025, NOW)
        assert d is not None
        assert d.dps_common is None
        assert d.implied_price is None


# ── 감사의견 · KAM ───────────────────────────────────────────────────
class TestSplitKam:
    def test_numbered_items_without_newline(self):
        """실측: 삼성전자 제55기는 '…평가2. 재화의…'처럼 붙어서 온다."""
        items = split_kam("1. 메모리 반도체 재고자산 순실현가치 평가2. 재화의 판매장려활동")
        assert items == ["메모리 반도체 재고자산 순실현가치 평가", "재화의 판매장려활동"]

    def test_numbered_items_with_newline(self):
        items = split_kam("1. 건설중인자산의 감가상각개시시점 평가\n2. 매출차감의 정확성")
        assert items == ["건설중인자산의 감가상각개시시점 평가", "매출차감의 정확성"]

    def test_hangul_ordinals(self):
        """실측: SK하이닉스는 '가. …' 형식이다."""
        items = split_kam("가. 기계장치의 감가상각개시시점 검토 나. 재고자산 평가")
        assert items == ["기계장치의 감가상각개시시점 검토", "재고자산 평가"]

    def test_stray_number_not_treated_as_marker(self):
        """본문의 숫자를 구분자로 오인하면 문장이 잘린다."""
        items = split_kam("제3자 배정 유상증자 관련 회계처리의 적정성")
        assert items == ["제3자 배정 유상증자 관련 회계처리의 적정성"]

    def test_unnumbered_single_item_kept_whole(self):
        items = split_kam("영업권의 회수가능가액")
        assert items == ["영업권의 회수가능가액"]

    @pytest.mark.parametrize("raw", ["", "-", None, "해당사항 없음", "  "])
    def test_absent_returns_empty(self, raw):
        assert split_kam(raw) == []


class TestAuditOpinion:
    def _rows(self, opinion="적정의견"):
        return [
            {
                "rcept_no": "20260311000001",
                "bsns_year": "제57기 \n(당기)",
                "adtor": "삼정회계법인",
                "adt_opinion": opinion,
                "core_adt_matter": "1. 건설중인자산 평가\n2. 매출차감의 정확성",
                "emphs_matter": "-",
            },
            {
                "rcept_no": "20260311000001",
                "bsns_year": "제56기\n(전기)",
                "adtor": "삼정회계법인",
                "adt_opinion": "적정의견",
                "core_adt_matter": "1. 다른 항목",
                "emphs_matter": "-",
            },
        ]

    def test_picks_current_period_only(self):
        """3개 사업연도 × 연결/별도로 중복돼 온다. 당기만 써야 한다."""
        a = parse_audit_opinion(self._rows(), 2025, NOW)
        assert a is not None
        assert "당기" in a.period_label
        assert a.kam_items == ["건설중인자산 평가", "매출차감의 정확성"]

    def test_newline_in_period_label_normalized(self):
        a = parse_audit_opinion(self._rows(), 2025, NOW)
        assert a is not None
        assert "\n" not in a.period_label

    def test_clean_opinion(self):
        a = parse_audit_opinion(self._rows(), 2025, NOW)
        assert a is not None and a.is_clean

    @pytest.mark.parametrize("opinion", ["의견거절", "한정의견", "부적정의견"])
    def test_non_clean_opinion_flagged(self, opinion):
        """비적정 의견은 그 자체가 1급 리스크다. 놓치면 안 된다."""
        a = parse_audit_opinion(self._rows(opinion), 2025, NOW)
        assert a is not None and not a.is_clean

    def test_dash_emphasis_is_none(self):
        a = parse_audit_opinion(self._rows(), 2025, NOW)
        assert a is not None and a.emphasis is None


# ── 직원 현황 ────────────────────────────────────────────────────────
def _emp(fo_bbm, sexdstn, sm, tenure="10.0"):
    return {
        "rcept_no": "20260311000001",
        "fo_bbm": fo_bbm,
        "sexdstn": sexdstn,
        "sm": sm,
        "avrg_cnwk_sdytrn": tenure,
    }


class TestWorkforce:
    def test_gender_rows_merged_into_division(self):
        w = parse_workforce([_emp("DX", "남", "38,119"), _emp("DX", "여", "12,698")], 2025, NOW)
        assert w is not None
        assert w.total == 50_817
        assert len(w.divisions) == 1

    def test_total_row_excluded_from_sum(self):
        """실측: 삼성전자에는 DX·DS와 함께 '성별합계' 행이 온다. 세면 2배가 된다."""
        rows = [
            _emp("DX", "남", "38,119"),
            _emp("DX", "여", "12,698"),
            _emp("DS", "남", "56,154"),
            _emp("DS", "여", "21,910"),
            _emp("성별합계", "남", "94,273"),
            _emp("성별합계", "여", "34,608"),
        ]
        w = parse_workforce(rows, 2025, NOW)
        assert w is not None
        assert w.total == 128_881  # 257,762가 아니다
        assert w.division_names == ["DS", "DX"]

    def test_tenure_weighted_by_headcount(self):
        """단순평균은 인원이 적은 쪽에 과대 가중된다."""
        w = parse_workforce(
            [_emp("DX", "남", "90", "20.0"), _emp("DX", "여", "10", "10.0")], 2025, NOW
        )
        assert w is not None
        assert w.divisions[0].avg_tenure_years == pytest.approx(19.0)

    def test_business_division_grouping(self):
        rows = [_emp("DX", "남", "50,817"), _emp("DS", "남", "78,064")]
        w = parse_workforce(rows, 2025, NOW)
        assert w is not None
        assert w.grouping == "사업부문"
        assert w.has_segments

    def test_job_function_grouping_is_not_a_segment(self):
        """실측: 셀트리온제약은 생산직·영업직으로 온다. 사업부문이 아니다."""
        rows = [
            _emp("생산직", "남", "563"),
            _emp("관리사무직", "남", "225"),
            _emp("영업직", "남", "160"),
            _emp("연구개발직", "남", "58"),
        ]
        w = parse_workforce(rows, 2025, NOW)
        assert w is not None
        assert w.grouping == "직군"
        assert not w.has_segments

    def test_single_division_company(self):
        w = parse_workforce([_emp("전사", "남", "279"), _emp("전사", "여", "247")], 2025, NOW)
        assert w is not None
        assert w.grouping == "단일"
        assert not w.has_segments
        assert w.total == 526

    def test_share_of_sums_to_100(self):
        rows = [_emp("DX", "남", "50,817"), _emp("DS", "남", "78,064")]
        w = parse_workforce(rows, 2025, NOW)
        assert w is not None
        assert sum(w.share_of(n) for n in w.division_names) == pytest.approx(100.0)

    def test_empty_returns_none(self):
        assert parse_workforce([], 2025, NOW) is None
