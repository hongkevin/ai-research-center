"""분기·반기보고서의 **전기 컬럼**.

삼성물산 2026년 1분기로 노트를 만들었더니 3개년 추이 차트에 막대가 한 해에만
서 있었다. 원인은 DART 응답의 컬럼 이름이 보고서 종류마다 다르다는 것이다 —
분기 손익에는 `frmtrm_amount`·`bfefrmtrm_amount`가 **아예 없고**
`frmtrm_add_amount`(전년 누적)가 온다. 우리는 앞의 두 개만 읽고 있었다.

전년 대비가 없으면 어닝 리뷰가 성립하지 않는다. 이 파일이 지키는 것은 그것이다.
"""

from __future__ import annotations

import datetime as dt

from arc.data.base import ConsolidationType, PeriodType
from arc.data.kr.dart import DartProvider

NOW = dt.datetime(2026, 8, 5, tzinfo=dt.UTC)


def _parse(rows: list[dict], period: PeriodType = PeriodType.Q1):
    return DartProvider.parse_financials(
        {"list": rows}, "028260", 2026, period, ConsolidationType.CONSOLIDATED, NOW
    )


# 실측 형태 — 삼성물산 2026 1분기 (reprt_code 11013)
QUARTERLY_IS = {
    "account_id": "ifrs-full_Revenue",
    "account_nm": "수익(매출액)",
    "sj_div": "IS",
    "thstrm_nm": "제 57 기 1분기",
    "thstrm_amount": "10465823172864",
    "thstrm_add_amount": "10465823172864",
    "frmtrm_q_nm": "제 56 기 1분기",
    "frmtrm_q_amount": "9736780907943",
    "frmtrm_add_amount": "9736780907943",
    "rcept_no": "20260515001895",
}

# 반기는 단독 분기와 누적이 다르다 — 「반기」라는 이름에 맞는 것은 누적이다
HALF_IS = {
    "account_nm": "수익(매출액)",
    "sj_div": "IS",
    "thstrm_amount": "3179406065745",  # 2분기 단독
    "thstrm_add_amount": "6356223796915",  # 반기 누적
    "frmtrm_q_amount": "4085942678099",
    "frmtrm_add_amount": "8902123221495",
}

# 분기 재무상태표에는 `_add_` 칸이 없다 — 기존 경로를 그대로 타야 한다
QUARTERLY_BS = {
    "account_nm": "자산총계",
    "sj_div": "BS",
    "thstrm_amount": "60000000000000",
    "frmtrm_amount": "58000000000000",
}

ANNUAL_IS = {
    "account_nm": "수익(매출액)",
    "sj_div": "IS",
    "thstrm_amount": "40742240967149",
    "thstrm_add_amount": "",  # 사업보고서에서는 비어 있다
    "frmtrm_nm": "제 55 기",
    "frmtrm_amount": "42103238027336",
    "bfefrmtrm_amount": "41895681215734",
}


class TestQuarterlyColumns:
    def test_quarterly_income_statement_has_a_prior_period(self):
        """이게 비면 전년 대비가 통째로 사라진다."""
        item = _parse([QUARTERLY_IS]).items[0]
        assert item.amount == 10_465_823_172_864
        assert item.prior_amount == 9_736_780_907_943

    def test_quarterly_has_no_second_prior_period(self):
        """분기 손익에 전전기는 없다. **0으로 채우지 않는다** — 없는 것은 없다."""
        assert _parse([QUARTERLY_IS]).items[0].prior2_amount is None

    def test_half_year_uses_the_cumulative_column(self):
        """반기보고서의 `thstrm_amount`는 2분기 단독이라 「반기」와 어긋난다."""
        item = _parse([HALF_IS], PeriodType.HALF).items[0]
        assert item.amount == 6_356_223_796_915
        assert item.prior_amount == 8_902_123_221_495

    def test_balance_sheet_keeps_the_year_end_column(self):
        """재무상태표에는 누적이라는 개념이 없다. 전기말과 비교해야 한다."""
        item = _parse([QUARTERLY_BS]).items[0]
        assert item.amount == 60_000_000_000_000
        assert item.prior_amount == 58_000_000_000_000

    def test_annual_is_unchanged(self):
        """사업보고서 경로가 3개년 그대로여야 한다 — 회귀 방지."""
        item = _parse([ANNUAL_IS], PeriodType.ANNUAL).items[0]
        assert item.amount == 40_742_240_967_149
        assert item.prior_amount == 42_103_238_027_336
        assert item.prior2_amount == 41_895_681_215_734

    def test_major_accounts_path_too(self):
        """주요계정 폴백도 같은 규칙을 타야 한다 — 한쪽만 고치면 종목마다 다르다."""
        stmt = DartProvider.parse_major_accounts(
            {"list": [dict(QUARTERLY_IS, fs_div="CFS")]},
            "028260",
            2026,
            PeriodType.Q1,
            ConsolidationType.CONSOLIDATED,
            NOW,
        )
        assert stmt.items[0].prior_amount == 9_736_780_907_943
