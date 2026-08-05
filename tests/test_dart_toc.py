"""공시 목차 파싱과 절 링크.

「원문 공시 열기」가 8MB 사업보고서의 **첫 장**으로 갔다. 부문 매출 하나
확인하려고 143절짜리 목차를 사람이 다시 뒤져야 했으니, 검증 경로가 있다는
말이 반만 참이었다.

여기서 지키는 것은 하나다: **틀린 절로 보내느니 안 보낸다.** 엉뚱한 자리가
열리면 링크가 없느니만 못하다.
"""

from __future__ import annotations

from arc.data.kr.dart_toc import locate, parse_toc

# 실측 형태 — dsaf001/main.do 가 스크립트에 심어 두는 목차 트리
HTML = """
    var node1 = {};
    node1['text'] = "I. 회사의 개요";
    node1['rcpNo'] = "20260312000856";
    node1['dcmNo'] = "11114893";
    node1['eleId'] = "3";
    node1['offset'] = "1000";
    node1['length'] = "500";
    node1['dtd'] = "dart4.xsd";
        var node2 = {};
        node2['text'] = "4. 주식의 총수 등";
        node2['rcpNo'] = "20260312000856";
        node2['dcmNo'] = "11114893";
        node2['eleId'] = "7";
        node2['offset'] = "86590";
        node2['length'] = "3000";
        node2['dtd'] = "dart4.xsd";
    var node1 = {};
    node1['text'] = "III. 재무에 관한 사항";
    node1['rcpNo'] = "20260312000856";
    node1['dcmNo'] = "11114893";
    node1['eleId'] = "17";
    node1['offset'] = "485431";
    node1['length'] = "8279578";
    node1['dtd'] = "dart4.xsd";
        var node2 = {};
        node2['text'] = "3. 연결재무제표 주석";
        node2['rcpNo'] = "20260312000856";
        node2['dcmNo'] = "11114893";
        node2['eleId'] = "24";
        node2['offset'] = "759200";
        node2['length'] = "900000";
        node2['dtd'] = "dart4.xsd";
        var node2 = {};
        node2['text'] = "6. 배당에 관한 사항";
        node2['rcpNo'] = "20260312000856";
        node2['dcmNo'] = "11114893";
        node2['eleId'] = "107";
        node2['offset'] = "8460306";
        node2['length'] = "18652";
        node2['dtd'] = "dart4.xsd";
    var node1 = {};
    node1['text'] = "VII. 주주에 관한 사항";
    node1['rcpNo'] = "20260312000856";
    node1['dcmNo'] = "11114893";
    node1['eleId'] = "120";
    node1['offset'] = "9000000";
    node1['length'] = "40000";
    node1['dtd'] = "dart4.xsd";
"""

TOC = parse_toc(HTML)


class TestParse:
    def test_reads_every_node(self):
        assert [e.text for e in TOC] == [
            "I. 회사의 개요",
            "4. 주식의 총수 등",
            "III. 재무에 관한 사항",
            "3. 연결재무제표 주석",
            "6. 배당에 관한 사항",
            "VII. 주주에 관한 사항",
        ]

    def test_depth_comes_from_the_variable_name(self):
        assert [e.depth for e in TOC[:2]] == [1, 2]

    def test_builds_a_viewer_url_with_the_position(self):
        url = TOC[4].url
        assert "rcpNo=20260312000856" in url
        assert "eleId=107" in url and "offset=8460306" in url and "length=18652" in url

    def test_empty_html_is_not_an_error(self):
        assert parse_toc("") == []


class TestLocate:
    def test_segment_numbers_go_to_the_notes(self):
        assert locate(TOC, "사업보고서 원문 · 영업부문 (연결)").text == "3. 연결재무제표 주석"

    def test_dividend_goes_to_the_dividend_section(self):
        assert locate(TOC, "정기보고서 · 배당에 관한 사항").text == "6. 배당에 관한 사항"

    def test_largest_shareholder_is_not_the_share_count_section(self):
        """실측으로 틀렸던 자리 — 「주식」 규칙이 먼저 걸려 엉뚱한 절로 갔다."""
        assert (
            locate(TOC, "정기보고서 · 최대주주 및 특수관계인 주식소유 현황").text
            == "VII. 주주에 관한 사항"
        )

    def test_share_counts_still_go_to_their_own_section(self):
        assert locate(TOC, "정기보고서 · 주식의 총수 현황").text == "4. 주식의 총수 등"

    def test_unknown_dataset_gets_no_link(self):
        """추정치처럼 원문에 없는 것은 **보내지 않는다.**"""
        assert locate(TOC, "추정 (기준선 · 과거 실적의 기계적 연장)") is None

    def test_no_dataset_no_link(self):
        assert locate(TOC, None) is None

    def test_empty_toc_no_link(self):
        assert locate([], "정기보고서 · 배당에 관한 사항") is None
