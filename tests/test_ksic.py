"""KSIC 코드 → 업종명.

헤더 표의 「산업」 칸이 계속 `—`였다. DART는 코드만 준다.
"""

from __future__ import annotations

from arc.data.kr.ksic import industry_name


class TestIndustryName:
    def test_maps_real_codes(self):
        """실측 — DART가 실제로 돌려준 코드들."""
        assert industry_name("467") == "도매 및 상품 중개업"  # 삼성물산
        assert industry_name("264").startswith("전자부품")  # 삼성전자
        assert industry_name("213") == "의료용 물질 및 의약품 제조업"  # 파마리서치
        assert industry_name("108") == "식료품 제조업"  # 노바렉스

    def test_four_digit_codes_roll_up(self):
        """세분류도 앞 두 자리로 올린다 — SK하이닉스는 2612다."""
        assert industry_name("2612") == industry_name("26")

    def test_unknown_code_is_none(self):
        """**코드를 그대로 내보내지 않는다** — 빈 칸보다 나쁜 게 뜻 모를 숫자다."""
        assert industry_name("99999") is None or industry_name("99999") == "국제 및 외국기관"
        assert industry_name("04") is None  # 분류에 없는 번호

    def test_missing_input_is_none(self):
        assert industry_name(None) is None
        assert industry_name("") is None
        assert industry_name("A") is None
