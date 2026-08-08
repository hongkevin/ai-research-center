"""수주 공시 — **미드스몰캡의 최대 사건** (D87).

이 파일이 지키는 것:

* **정형 필드를 읽는 것이지 해석하는 것이 아니다.** 금액·비율·상대방이 공시
  서식의 칸에 있다. 그 칸을 못 찾으면 비워 두고, 지어내지 않는다
* **한 수주가 두 건으로 세어지면 안 된다.** 정정 공시와 거래정지 공시가 같은
  사건에서 나온다
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from arc.data.kr import contracts

# 실물에서 뜯어 온 골격 (코세스 2026-07-13, 접수 20260713900131).
# **셀 이웃 관계만 남긴다** — DART 서식은 xforms 스팬이 중첩된 표라 통째로
# 붙이면 시험이 서식의 사본이 되고, 그러면 파서가 아니라 사본을 시험한다.
REAL = """
<td>조건부 계약여부</td><td>미해당</td>
<td>확정 계약금액</td><td>150,420,000,000</td>
<td>조건부 계약금액</td><td>-</td>
<td>계약금액 총액(원)</td><td>150,420,000,000</td>
<td>최근 매출액(원)</td><td>82,379,163,292</td>
<td>매출액 대비(%)</td><td>183</td>
<td>3. 계약상대방</td><td>Bloom Energy Corporation</td>
<td>- 최근 매출액(원)</td><td>2,904,223,251,000</td>
<td>- 주요사업</td><td>고체 산화물 연료전지(SOFC)의 설계, 제조, 판매 및 설치 등</td>
<td>5. 계약기간</td><td>시작일</td><td>2026-07-10</td><td>종료일</td><td>2026-10-29</td>
"""


@dataclass
class FakeDisclosure:
    title: str
    rcept_no: str
    filed_at: str


class TestWhatTheFilingSays:
    def _parsed(self):
        return contracts.parse(REAL, symbol="089890", rcept_no="20260713900131")

    def test_it_reads_the_numbers_the_filing_already_computed(self):
        """**「최근 매출 대비 183%」는 우리가 계산한 것이 아니다.**

        애널리스트가 리포트에 손으로 쓰는 그 비율이 공시 서식의 칸에 있다.
        우리가 다시 계산하면 분모(최근 매출액의 기준 시점)를 우리가 정하게
        되고, 그러면 공시와 다른 숫자가 나온다.
        """
        c = self._parsed()
        assert c.amount == 150_420_000_000
        assert c.recent_revenue == 82_379_163_292
        assert c.ratio_pct == 183

    def test_the_counterparty_is_the_value_chain_anchor(self):
        """상대방은 숫자가 아니라 **이름**이고, 그 이름이 논리의 뼈대다.

        「블룸에너지향」·「짐머향」·「삼성SDS향」이 그의 모든 리포트 제목에
        들어간다. 상대방의 주요사업까지 공시에 있어 테마가 따라온다.
        """
        c = self._parsed()
        assert c.counterparty == "Bloom Energy Corporation"
        assert "SOFC" in c.counterparty_business

    def test_it_does_not_read_the_counterparty_revenue_as_ours(self):
        """**「최근 매출액(원)」이 두 번 나온다.**

        상대방(블룸에너지) 매출은 2.9조다. 그것을 우리 회사 매출로 읽으면
        「매출 대비 5%」라는 정반대 그림이 나온다 — 1,504억이 사소해 보인다.
        """
        c = self._parsed()
        assert c.recent_revenue == 82_379_163_292, "우리 회사 매출이어야 한다"
        assert c.recent_revenue != 2_904_223_251_000

    def test_the_dates_come_through(self):
        c = self._parsed()
        assert (c.starts_at, c.ends_at) == ("2026-07-10", "2026-10-29")

    def test_a_dash_is_not_zero(self):
        """조건부 계약금액이 `-`인 것은 정상이다. 0으로 읽으면 없는 사실이 생긴다."""
        assert contracts._num("-") is None
        assert contracts._num("") is None
        assert contracts._num("150,420,000,000") == 150_420_000_000

    def test_the_headline_carries_the_ratio(self):
        """**비율을 빼면 금액이 뜻을 잃는다.**

        1,504억은 회사에 따라 사소하기도 하고 회사를 바꾸기도 한다. 그 차이를
        말해 주는 것이 「최근 매출 대비」다.
        """
        head = self._parsed().headline
        assert "Bloom Energy" in head
        assert "1,504억원" in head
        assert "183%" in head


class TestWhatIsNotAContract:
    def test_the_trading_halt_is_a_shadow_not_a_contract(self):
        """`주권매매거래정지 (단일판매공급계약)`은 수주가 아니라 그 결과다.

        세면 한 수주가 두 건이 되고, 브리프가 「수주 2건」이라고 말한다.
        """
        assert contracts.is_contract("단일판매ㆍ공급계약체결")
        assert not contracts.is_contract("주권매매거래정지              (단일판매공급계약)")

    def test_an_attachment_only_correction_has_no_table(self):
        """**실측**: 코세스 2026-04-30 `[첨부정정]`은 표가 없어 전부 빈 값으로
        파싱됐고, 그대로 두니 「계약상대방 미공개」라는 유령 수주가 한 건 생겼다.

        아무것도 안 읽혔으면 그것은 수주가 아니다.
        """
        empty = contracts.parse("<td>참고사항</td><td>-</td>", symbol="089890", rcept_no="x")
        assert empty.amount is None and not empty.counterparty


class TestOneContractCountedOnce:
    def test_a_correction_replaces_the_original(self):
        """정정본이 이긴다 — **금액이 바뀌었을 수 있다.**

        접수번호는 정정마다 새로 나오므로 열쇠가 못 된다. 상대방과 납기가
        같으면 같은 건으로 본다.
        """
        base = {"symbol": "089890", "counterparty": "블룸에너지", "starts_at": "2026-07-10"}
        rows = [
            contracts.Contract(rcept_no="1", filed_at="2026-07-13", amount=100.0, **base),
            contracts.Contract(rcept_no="2", filed_at="2026-08-06", amount=150.0, **base),
        ]
        out = contracts._dedupe(rows)
        assert len(out) == 1
        assert out[0].amount == 150.0, "나중 공시가 맞는 금액이다"

    def test_different_counterparties_are_different_contracts(self):
        rows = [
            contracts.Contract(
                symbol="189330", rcept_no="1", filed_at="2026-07-07", counterparty="삼성에스디에스"
            ),
            contracts.Contract(
                symbol="189330", rcept_no="2", filed_at="2026-08-04", counterparty="비엔아이엔씨"
            ),
        ]
        assert len(contracts._dedupe(rows)) == 2

    def test_recent_drops_the_old_ones(self):
        """브리프는 「어젯밤 사이」를 묻지 작년 수주를 묻지 않는다."""
        rows = [
            contracts.Contract(symbol="x", rcept_no="1", filed_at="2026-07-13"),
            contracts.Contract(symbol="x", rcept_no="2", filed_at="2025-01-02"),
        ]
        kept = contracts.recent(rows, days=90, today=dt.date(2026, 8, 8))
        assert [c.rcept_no for c in kept] == ["1"]
