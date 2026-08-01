"""부문별 매출 파싱 테스트.

원문 파싱은 API보다 훨씬 깨지기 쉽다. 그래서 이 레이어의 계약은 "정확히
읽는다"가 아니라 **"확신 없으면 쓰지 않는다"**이다. 픽스처는 전부 실제
사업보고서에서 관측된 표 모양이다:

  파마리서치   구분 | 판매구분 | 금액 | 비율          (2단, 비율 있음)
  SK하이닉스   사업부문 | 매출유형 | 품목 | 금액       (3단, 단일 부문)
  셀트리온제약 사업부문 | 매출유형 | 품목 | 판매구분 | 금액  (4단, 소계 중첩)
  삼성전자     품목 | 금액                              (1단)
"""

from __future__ import annotations

import datetime as dt

import pytest

from arc.data.base import (
    ConsolidationType,
    FinancialLineItem,
    FinancialStatement,
    PeriodType,
    Provenance,
)
from arc.data.kr.dart_document import Section, detect_unit_scale, expand_table
from arc.finmodel.metrics import extract_metrics
from arc.finmodel.segments import (
    build_segment_entries,
    build_segment_observations,
    build_segments,
)
from arc.llm.number_registry import NumberRegistry

PROV = Provenance(source="opendart", retrieved_at=dt.datetime(2026, 8, 1, tzinfo=dt.UTC))


def _ms(revenue: int):
    stmt = FinancialStatement(
        symbol="000000",
        fiscal_year=2025,
        period=PeriodType.ANNUAL,
        consolidation=ConsolidationType.CONSOLIDATED,
        items=[FinancialLineItem(account_name="매출액", amount=revenue, statement_type="IS")],
        provenance=PROV,
    )
    return extract_metrics(stmt)


def _section(body: str) -> Section:
    return Section(title="4. 매출 및 수주상황", start=0, body=body)


def _table(rows: list[list[tuple[str, int]]]) -> str:
    """(값, rowspan) 목록 → DART 표 XML."""
    out = ["<TABLE>"]
    for row in rows:
        out.append("<TR>")
        for value, rowspan in row:
            attr = f' ROWSPAN="{rowspan}"' if rowspan > 1 else ""
            out.append(f"<TD{attr}>{value}</TD>")
        out.append("</TR>")
    out.append("</TABLE>")
    return "".join(out)


def _plain(rows: list[list[str]]) -> str:
    return _table([[(c, 1) for c in r] for r in rows])


# ── 표 펼치기 ────────────────────────────────────────────────────────
class TestExpandTable:
    def test_rowspan_carried_into_following_rows(self):
        """펼치지 않으면 열이 밀려 값이 엉뚱한 행에 붙는다."""
        xml = _table(
            [
                [("의약품", 3), ("내 수", 1), ("51,090", 1)],
                [("수 출", 1), ("31,450", 1)],
                [("계", 1), ("82,540", 1)],
            ]
        )
        grid = expand_table(xml)
        assert grid[1][0] == "의약품"
        assert grid[2] == ["의약품", "계", "82,540"]

    def test_colspan_pads_row(self):
        xml = '<TABLE><TR><TD COLSPAN="2">머리</TD><TD>값</TD></TR></TABLE>'
        assert expand_table(xml)[0] == ["머리", "", "값"]

    def test_note_tables_use_te_cells(self):
        """주석의 표는 셀을 `<TE>`로 쓴다. TD만 보면 본문이 통째로 비어 나온다."""
        xml = (
            "<TABLE><TR><TH>구분</TH><TH>국내</TH></TR>"
            "<TR><TE>제품매출액</TE><TE>289,725,221,349</TE></TR></TABLE>"
        )
        grid = expand_table(xml)
        assert grid[0] == ["구분", "국내"]
        assert grid[1] == ["제품매출액", "289,725,221,349"]

    def test_nested_te_inside_td_not_split(self):
        """TD 안에 TE가 중첩된 표에서 둘 다 매칭하면 셀이 쪼개진다."""
        xml = "<TABLE><TR><TD><TE>값</TE></TD><TD>다음</TD></TR></TABLE>"
        assert expand_table(xml)[0] == ["값", "다음"]

    def test_entities_cleaned(self):
        xml = "<TABLE><TR><TD>가&cr;나</TD></TR></TABLE>"
        assert expand_table(xml)[0] == ["가 나"]


class TestUnitScale:
    @pytest.mark.parametrize(
        ("text", "want"),
        [
            ("(단위 : 백만원, %)", 1_000_000),
            ("(단위: 천원)", 1_000),
            ("(단위 : 억원)", 100_000_000),
            ("(단위 : 원)", 1),
            ("단위가 없는 문장", None),
        ],
    )
    def test_detect(self, text, want):
        assert detect_unit_scale(text) == want

    def test_missing_unit_blocks_segments(self):
        """백만원을 원으로 읽으면 6자리가 어긋난 숫자가 리포트에 실린다."""
        body = _plain([["구분", "금액"], ["의약품", "100"], ["의료기기", "200"]])
        seg = build_segments(_section(body), _ms(300_000_000))
        assert not seg.usable
        assert "단위" in seg.note


# ── 실제 표 모양들 ───────────────────────────────────────────────────
class TestRealTableShapes:
    def test_two_level_with_share(self):
        """파마리서치: 구분 | 판매구분 | 금액 | 비율. 소계 행만 부문 합계다."""
        body = "(단위 : 백만원, %)" + _table(
            [
                [("구 분", 2), ("판 매구 분", 2), ("2025년", 1)],
                [("금액", 1), ("비율", 1)],
                [("의약품", 3), ("내 수", 1), ("51,090", 1), ("9.5", 1)],
                [("수 출", 1), ("31,450", 1), ("5.9", 1)],
                [("계", 1), ("82,540", 1), ("20.8", 1)],
                [("의료기기", 3), ("내 수", 1), ("225,871", 1), ("42.1", 1)],
                [("수 출", 1), ("88,564", 1), ("16.5", 1)],
                [("계", 1), ("314,435", 1), ("79.2", 1)],
                [("합 계", 1), ("", 1), ("396,975", 1), ("100.0", 1)],
            ]
        )
        seg = build_segments(_section(body), _ms(396_975_000_000))
        assert seg.usable
        assert [x.name for x in seg.lines] == ["의약품", "의료기기"]
        assert seg.lines[0].amount == 82_540 * 1_000_000
        assert seg.lines[0].share == 20.8

    def test_channel_rows_not_double_counted(self):
        """내수·수출을 부문으로 세면 합계가 정확히 두 배가 된다."""
        body = "(단위 : 백만원, %)" + _table(
            [
                [("구분", 1), ("판매구분", 1), ("금액", 1), ("비율", 1)],
                [("의약품", 3), ("내 수", 1), ("60", 1), ("60.0", 1)],
                [("수 출", 1), ("40", 1), ("40.0", 1)],
                [("계", 1), ("100", 1), ("100.0", 1)],
            ]
        )
        seg = build_segments(_section(body), _ms(100_000_000))
        assert seg.total == 100_000_000

    def test_nested_subtotals_resolved(self):
        """셀트리온제약: 품목 합계와 유형 소계가 함께 실려 이중 계상된다."""
        body = "(단위: 백만원)" + _plain(
            [
                ["사업부문", "매출유형", "품 목", "", "제26기"],
                ["의약품", "제품", "고덱스", "합 계", "69,864"],
                ["의약품", "제품", "기타 제네릭", "합 계", "130,753"],
                ["의약품", "제품", "제품소계", "합 계", "200,617"],
                ["의약품", "상품", "램시마", "합 계", "45,541"],
                ["의약품", "상품", "기타", "합 계", "165,954"],
                ["의약품", "상품", "상품소계", "합 계", "211,495"],
                ["의약품", "용역매출", "", "소 계", "124,288"],
                ["합 계", "", "", "합 계", "536,400"],
            ]
        )
        seg = build_segments(_section(body), _ms(536_400_000_000))
        assert seg.usable
        assert [x.name for x in seg.lines] == ["제품", "상품", "용역매출"]
        assert seg.total == 536_400_000_000

    def test_single_segment_is_still_a_fact(self):
        """SK하이닉스 = 반도체 단일. 단일 사업 구조도 리포트에 실릴 정보다."""
        body = "(단위: 백만원)" + _plain(
            [
                ["사업부문", "매출유형", "품목", "제78기"],
                ["반도체 부문", "제품 외", "DRAM, NAND Flash", "97,146,675"],
                ["합 계", "", "", "97,146,675"],
            ]
        )
        seg = build_segments(_section(body), _ms(97_146_675_000_000))
        assert seg.usable
        assert seg.single_segment
        assert seg.lines[0].name == "반도체부문"


# ── 검산이 최종 판정이다 ─────────────────────────────────────────────
class TestReconciliation:
    def _body(self, a: str, b: str) -> str:
        return "(단위: 백만원)" + _plain([["구분", "금액"], ["가", a], ["나", b]])

    def test_matching_total_passes(self):
        seg = build_segments(_section(self._body("300", "700")), _ms(1_000_000_000))
        assert seg.usable
        assert abs(seg.gap_pct) < 0.01

    def test_mismatched_total_refused(self):
        """표를 잘못 읽었다는 뜻이다. 잘못 읽은 부문 구성은 되돌릴 수 없다."""
        seg = build_segments(_section(self._body("300", "700")), _ms(2_000_000_000))
        assert not seg.usable
        assert "어긋나" in seg.note

    def test_no_revenue_means_no_verification(self):
        seg = build_segments(_section(self._body("300", "700")), _ms(0))
        assert not seg.usable

    def test_missing_section_is_not_an_error(self):
        seg = build_segments(None, _ms(1_000_000_000))
        assert not seg.usable
        assert "찾지 못했다" in seg.note

    def test_share_sum_must_be_100(self):
        """금액과 비율이 **각각** 맞아야 통과한다."""
        body = "(단위: 백만원, %)" + _plain(
            [["구분", "금액", "비율"], ["가", "300", "20.0"], ["나", "700", "30.0"]]
        )
        seg = build_segments(_section(body), _ms(1_000_000_000))
        assert not seg.usable


# ── 레지스트리·논지 ──────────────────────────────────────────────────
class TestEntriesAndObservations:
    def _seg(self):
        body = "(단위: 백만원)" + _plain([["구분", "금액"], ["의료기기", "700"], ["의약품", "300"]])
        return build_segments(_section(body), _ms(1_000_000_000))

    def test_entries_registered_with_stable_keys(self):
        reg = NumberRegistry()
        reg.register_all(build_segment_entries(self._seg(), PROV))
        assert "segment1_revenue_2025a" in reg
        assert "segment1_share_2025a" in reg

    def test_gap_is_internal(self):
        reg = NumberRegistry()
        reg.register_all(build_segment_entries(self._seg(), PROV))
        assert "segment_gap_2025a" in reg
        assert "segment_gap_2025a" not in {r["key"] for r in reg.catalog()}

    def test_unusable_registers_nothing(self):
        seg = build_segments(None, _ms(1_000_000_000))
        assert build_segment_entries(seg, PROV) == []

    def test_observations_have_no_magnitudes(self):
        text = " ".join(build_segment_observations(self._seg()))
        assert not NumberRegistry().find_unregistered_numbers(text)

    def test_concentration_surfaced(self):
        obs = " ".join(build_segment_observations(self._seg()))
        assert "집중" in obs

    def test_headcount_confusion_warned_against(self):
        """인력 구분을 매출 구성으로 옮겨 말하는 걸 막는다 (D18)."""
        obs = " ".join(build_segment_observations(self._seg()))
        assert "인력 구분이 아니라" in obs
