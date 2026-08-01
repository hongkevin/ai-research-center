"""사업 이해 레이어 테스트.

이 레이어가 존재하는 이유는 갭 분석의 결론이다 — 우리 노트가 "매출이 늘었다"에서
못 벗어난 건 분량 문제가 아니라 **회사가 무엇을 하는 회사인지 몰랐기 때문**이다.

여기서 지키는 계약은 둘이다:
  1. 원문을 프롬프트에 넣되 **숫자는 반드시 가린다** (레지스트리 불변식).
  2. 지분율·출자 비중도 수치이므로 레지스트리를 거친다 (본문 리터럴 금지).
"""

from __future__ import annotations

import datetime as dt

import pytest

from arc.data.base import Provenance
from arc.data.kr.dart_document import Section
from arc.data.kr.dart_reports import (
    Affiliate,
    Affiliates,
    Ownership,
    parse_affiliates,
    parse_ownership,
)
from arc.finmodel.business import (
    build_business_entries,
    build_business_observations,
    build_business_profile,
)
from arc.llm.number_registry import NumberRegistry, mask_numbers

PROV = Provenance(source="opendart", retrieved_at=dt.datetime(2026, 8, 1, tzinfo=dt.UTC))
NOW = PROV.retrieved_at


def _section(body: str) -> Section:
    return Section(title="1. 사업의 개요", start=0, body=body)


def _own(principal="정상수", stake=30.8, total=31.37, holders=8):
    return Ownership(
        fiscal_year=2025,
        principal=principal,
        principal_stake=stake,
        total_stake=total,
        holder_count=holders,
        rcept_no="20260319001417",
        provenance=PROV,
    )


def _aff(entries):
    return Affiliates(fiscal_year=2025, entries=entries, rcept_no="x", provenance=PROV)


# ── 숫자 마스킹: 레지스트리 불변식의 연장 ─────────────────────────────
class TestMaskNumbers:
    def test_amounts_and_ratios_masked(self):
        out = mask_numbers("매출 5,363억원, 영업이익률 40.0%, 지분 46%")
        assert not NumberRegistry().find_unregistered_numbers(out)

    def test_years_survive(self):
        """연도는 사실이고 게이트도 허용한다. 가리면 서술이 무의미해진다."""
        assert "2001년" in mask_numbers("2001년 창업하여 2013년 공장을 지었다")
        assert "2013년" in mask_numbers("2001년 창업하여 2013년 공장을 지었다")

    def test_masking_matches_gate_whitelist(self):
        """탐지와 같은 규칙이어야 한다 — 갈라지면 '가렸는데 걸리는' 일이 생긴다."""
        raw = "1분기 매출은 1,234억원이며 FY2025 기준 3.5배 늘었다. 제30조에 따른다."
        assert not NumberRegistry().find_unregistered_numbers(mask_numbers(raw))

    def test_no_numbers_unchanged(self):
        text = "의약품과 의료기기를 만든다."
        assert mask_numbers(text) == text


# ── 사업 프로필 ──────────────────────────────────────────────────────
class TestBusinessProfile:
    OVERVIEW = (
        "1. 사업의 개요 가. 사업의 개요 (1) 기업 개요 당사는 2001년 3월 의약품, 의료기기 "
        "개발 회사로 창업하여 매출 5,363억원을 기록했습니다. GMP 인증공장을 설립하고 "
        "해외 수출과 연구개발을 병행합니다."
    )

    def test_overview_is_masked(self):
        p = build_business_profile(_section(self.OVERVIEW), 2025)
        assert p.usable
        assert not NumberRegistry().find_unregistered_numbers(p.overview)

    def test_section_title_stripped(self):
        """제목이 남으면 LLM이 그걸 문장으로 읽는다."""
        p = build_business_profile(_section(self.OVERVIEW), 2025)
        assert not p.overview.startswith("1. 사업의 개요")

    def test_boilerplate_stripped(self):
        p = build_business_profile(_section(self.OVERVIEW), 2025)
        assert "당사는" not in p.overview

    def test_signals_detected(self):
        p = build_business_profile(_section(self.OVERVIEW), 2025)
        assert "해외 진출" in p.signals
        assert "생산 설비" in p.signals
        assert "연구개발" in p.signals

    def test_missing_section_is_not_an_error(self):
        p = build_business_profile(None, 2025)
        assert not p.usable
        assert "찾지 못했다" in p.note

    def test_long_overview_cut_at_sentence_boundary(self):
        """중간에서 끊으면 LLM이 잘린 절을 사실로 읽는다."""
        long_text = "의약품을 만든다. " * 400
        p = build_business_profile(_section(long_text), 2025)
        assert len(p.overview) <= 1800
        assert p.overview.rstrip().endswith("다.")


class TestHoldingDetection:
    def _profile(self, book_value, assets):
        entries = [
            Affiliate(name=f"자회사{i}", purpose="경영참여", stake=50.0, book_value=book_value // 3)
            for i in range(3)
        ]
        return build_business_profile(
            _section("의약품을 만든다."), 2025, affiliates=_aff(entries), total_assets=assets
        )

    def test_operating_company_not_flagged(self):
        """**건수로 판정하면 안 된다.** 파마리서치는 경영참여 12건이지만
        장부가는 자산의 10%뿐인 사업회사다 (실측)."""
        p = self._profile(book_value=107_500_000_000, assets=1_043_888_000_000)
        assert not p.is_holding_like
        assert p.affiliate_weight == pytest.approx(10.3, abs=0.3)

    def test_holding_structure_flagged(self):
        p = self._profile(book_value=800_000_000_000, assets=1_000_000_000_000)
        assert p.is_holding_like

    def test_no_assets_means_no_verdict(self):
        p = self._profile(book_value=800_000_000_000, assets=None)
        assert p.affiliate_weight is None
        assert not p.is_holding_like


# ── 지분·출자 파싱 ───────────────────────────────────────────────────
class TestOwnershipParsing:
    ROWS = [
        {
            "rcept_no": "1",
            "stock_knd": "보통주",
            "nm": "정상수",
            "relate": "본인",
            "trmend_posesn_stock_qota_rt": "30.80",
        },
        {
            "rcept_no": "1",
            "stock_knd": "보통주",
            "nm": "정래준",
            "relate": "친인척",
            "trmend_posesn_stock_qota_rt": "0.01",
        },
        {
            "rcept_no": "1",
            "stock_knd": "보통주",
            "nm": "계",
            "relate": "-",
            "trmend_posesn_stock_qota_rt": "31.37",
        },
        {
            "rcept_no": "1",
            "stock_knd": "우선주",
            "nm": "계",
            "relate": "-",
            "trmend_posesn_stock_qota_rt": "10.17",
        },
    ]

    def test_reads_common_stock_only(self):
        """우선주 합계 행이 섞이면 지분율이 실제와 달라진다 (실측: 파마리서치 10.17%)."""
        o = parse_ownership(self.ROWS, 2025, NOW)
        assert o is not None
        assert o.total_stake == 31.37

    def test_principal_identified(self):
        o = parse_ownership(self.ROWS, 2025, NOW)
        assert o is not None
        assert o.principal == "정상수"
        assert o.principal_stake == 30.80

    def test_holder_count_excludes_principal_and_total(self):
        o = parse_ownership(self.ROWS, 2025, NOW)
        assert o is not None
        assert o.holder_count == 1

    @pytest.mark.parametrize(("total", "want"), [("31.37", True), ("12.50", False)])
    def test_owner_control_threshold(self, total, want):
        rows = [{**r} for r in self.ROWS]
        rows[2]["trmend_posesn_stock_qota_rt"] = total
        o = parse_ownership(rows, 2025, NOW)
        assert o is not None and o.is_owner_controlled is want

    def test_empty_returns_none(self):
        assert parse_ownership([], 2025, NOW) is None


class TestAffiliateParsing:
    ROWS = [
        {
            "rcept_no": "1",
            "inv_prm": "파마리서치바이오",
            "invstmnt_purps": "경영참여",
            "trmend_blce_qota_rt": "46.0",
            "trmend_blce_acntbk_amount": "30,200,000,000",
        },
        {
            "rcept_no": "1",
            "inv_prm": "수인투자조합",
            "invstmnt_purps": "단순투자",
            "trmend_blce_qota_rt": "16.7",
            "trmend_blce_acntbk_amount": "2,888,000,000",
        },
    ]

    def test_operating_separated_from_passive(self):
        """경영참여와 단순투자를 섞으면 사업 구조가 흐려진다."""
        a = parse_affiliates(self.ROWS, 2025, NOW)
        assert a is not None
        assert [e.name for e in a.operating] == ["파마리서치바이오"]

    def test_top_sorted_by_book_value(self):
        a = parse_affiliates(self.ROWS, 2025, NOW)
        assert a is not None
        assert a.top(1)[0].name == "파마리서치바이오"

    def test_total_book_value(self):
        a = parse_affiliates(self.ROWS, 2025, NOW)
        assert a is not None
        assert a.total_book_value == 33_088_000_000

    def test_total_rows_skipped(self):
        rows = [*self.ROWS, {"rcept_no": "1", "inv_prm": "계", "invstmnt_purps": "-"}]
        a = parse_affiliates(rows, 2025, NOW)
        assert a is not None and len(a.entries) == 2


# ── 레지스트리 · 논지 ────────────────────────────────────────────────
class TestEntriesAndObservations:
    def _profile(self):
        return build_business_profile(
            _section("의약품과 의료기기를 만들어 해외에 판다."),
            2025,
            ownership=_own(),
            affiliates=_aff(
                [
                    Affiliate(
                        name="파마리서치바이오",
                        purpose="경영참여",
                        stake=46.0,
                        book_value=30_200_000_000,
                    )
                ]
            ),
            total_assets=1_000_000_000_000,
        )

    def test_stakes_go_through_registry(self):
        """지분율도 수치다. 표에 리터럴로 넣었다가 G0에 막혔다 (실측)."""
        reg = NumberRegistry()
        reg.register_all(build_business_entries(self._profile(), PROV))
        assert "owner_stake_2025a" in reg
        assert "owner_total_stake_2025a" in reg
        assert "affiliate1_stake_2025a" in reg

    def test_observations_have_no_unregistered_numbers(self):
        text = " ".join(build_business_observations(self._profile()))
        assert not NumberRegistry().find_unregistered_numbers(text)

    def test_owner_control_surfaced(self):
        obs = " ".join(build_business_observations(self._profile()))
        assert "지배" in obs

    def test_low_stake_surfaces_stability_risk(self):
        p = build_business_profile(
            _section("의약품을 만든다."), 2025, ownership=_own(stake=8.5, total=12.0)
        )
        obs = " ".join(build_business_observations(p))
        assert "경영권 안정성" in obs

    def test_no_data_produces_nothing(self):
        assert build_business_observations(build_business_profile(None, 2025)) == []
