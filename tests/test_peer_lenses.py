"""피어 표의 관점 층 (D73).

요구가 그대로였다: *"피어 분석이 사실 실적 분석이라고 생각하지는 않고,
'실적 분석도 있겠으나, 예측도 있겠으나' 주가 차이, 분석에 대한 관점도 일부
있다고 생각한다"*.

지키는 것 셋:

  1. **침묵은 「중립」이 아니다** — 「모른다」가 「보통이다」로 바뀌면 안 된다
  2. **한 종목도 답 못 한 관점은 안 싣는다** — 빈칸 격자가 표는 아니다
  3. **갈리는 줄이 보인다** — 다 같으면 볼 것이 없다
"""

from __future__ import annotations

from arc.finmodel.peer import VERDICT_LABEL, build_peer_table


def _member(symbol: str, company: str, card_id: str, lenses: list[dict] | None = None) -> dict:
    return {
        "symbol": symbol,
        "company": company,
        "card_id": card_id,
        "year": 2025,
        "period": "ANNUAL",
        "status": "ready",
        "registry": [],
        "lenses": lenses or [],
    }


def _lens(label: str, verdict: str, *, question: str = "질문", headline: str = "발견") -> dict:
    return {
        "label": label,
        "question": question,
        "verdict": verdict,
        "headline_text": headline,
    }


class TestLensRows:
    def test_the_same_question_lines_up_across_companies(self):
        t = build_peer_table(
            [
                _member("005930", "삼성전자", "c1", [_lens("자본", "supportive")]),
                _member("000660", "SK하이닉스", "c2", [_lens("자본", "adverse")]),
            ]
        )
        assert len(t.lens_rows) == 1
        row = t.lens_rows[0]
        assert [c.label for c in row.cells] == ["받쳐 줌", "부담"]
        assert row.split is True

    def test_silence_is_not_neutral(self):
        """**결론이 없으면 없는 것이다.**

        침묵한 렌즈를 「중립」으로 적으면 「모른다」가 「보통이다」로 바뀐다 —
        이 제품이 피하려는 것 그 자체다.
        """
        t = build_peer_table(
            [
                _member("005930", "삼성전자", "c1", [_lens("집중", "adverse")]),
                # 결론을 못 낸 렌즈는 verdict가 빈 문자열로 온다
                _member("042660", "한화오션", "c2", [_lens("집중", "")]),
            ]
        )
        cells = t.lens_rows[0].cells
        assert cells[0].absent is False
        assert cells[1].absent is True
        assert cells[1].verdict == ""
        assert cells[1].label == ""

    def test_a_lens_nobody_answered_is_not_a_row(self):
        """전부 「—」인 줄이 서 있으면 표가 아니라 빈칸 격자다."""
        t = build_peer_table(
            [
                _member("005930", "삼성전자", "c1", [_lens("자본", "")]),
                _member("000660", "SK하이닉스", "c2", [_lens("자본", "")]),
            ]
        )
        assert t.lens_rows == []

    def test_agreement_is_not_a_split(self):
        t = build_peer_table(
            [
                _member("005930", "삼성전자", "c1", [_lens("재현성", "adverse")]),
                _member("000660", "SK하이닉스", "c2", [_lens("재현성", "adverse")]),
            ]
        )
        assert t.lens_rows[0].split is False

    def test_order_follows_the_first_card_that_has_it(self):
        """**렌즈 순서는 첫 카드가 정한다.** 회사마다 달라지면 표가 흔들린다."""
        t = build_peer_table(
            [
                _member(
                    "005930",
                    "삼성전자",
                    "c1",
                    [_lens("자본", "supportive"), _lens("집중", "adverse")],
                ),
                _member(
                    "000660",
                    "SK하이닉스",
                    "c2",
                    [_lens("집중", "adverse"), _lens("자본", "supportive")],
                ),
            ]
        )
        assert [r.label for r in t.lens_rows] == ["자본", "집중"]

    def test_a_member_without_a_card_gets_an_empty_cell(self):
        """카드가 없는 구성원도 **열은 있다** — 빼면 「왜 안 나오지」가 된다."""
        t = build_peer_table(
            [
                _member("005930", "삼성전자", "c1", [_lens("자본", "supportive")]),
                {"symbol": "042660", "company": "한화오션", "status": "pending"},
            ]
        )
        assert len(t.lens_rows[0].cells) == 2
        assert t.lens_rows[0].cells[1].absent is True

    def test_verdicts_are_not_investment_opinions(self):
        """**「긍정/부정」이 아니다.** 그건 투자의견처럼 읽힌다 (D4)."""
        assert VERDICT_LABEL["supportive"] == "받쳐 줌"
        assert VERDICT_LABEL["adverse"] == "부담"
        assert "긍정" not in VERDICT_LABEL.values()
        assert "매수" not in VERDICT_LABEL.values()

    def test_numbers_and_lenses_do_not_share_rows(self):
        """숫자 칸은 레지스트리 키로 되짚기가 되고 관점 칸은 아니다.

        섞으면 「이 판정의 출처는?」에 답할 수 없는 칸이 표에 앉는다.
        """
        t = build_peer_table([_member("005930", "삼성전자", "c1", [_lens("자본", "supportive")])])
        assert all(not hasattr(c, "key") for r in t.lens_rows for c in r.cells)
