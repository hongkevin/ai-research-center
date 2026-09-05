class TestQuarterBars:
    """분기 막대 — **음수를 아래로 그린다.**

    4분기에 비용을 몰아 넣으면 실제로 음수가 나온다. 전부 위로 그리면 적자
    분기가 흑자처럼 보인다.
    """

    def test_negative_quarter_draws_a_zero_line(self):
        from arc.render.charts import quarter_bars

        svg = quarter_bars(["1Q", "2Q"], [100.0, -30.0])
        assert "<line" in svg

    def test_no_zero_line_when_all_positive(self):
        """0선은 음수가 있을 때만 의미가 있다."""
        from arc.render.charts import quarter_bars

        assert "<line" not in quarter_bars(["1Q", "2Q"], [100.0, 120.0])

    def test_recent_quarters_are_opaque(self):
        from arc.render.charts import quarter_bars

        svg = quarter_bars(["1Q", "2Q", "3Q", "4Q"], [1.0, 2.0, 3.0, 4.0], highlight_from=2)
        assert 'opacity="1"' in svg and 'opacity="0.45"' in svg

    def test_missing_quarter_is_skipped_not_zero(self):
        """빈 분기를 0으로 그리면 실적이 사라진 것처럼 보인다."""
        from arc.render.charts import quarter_bars

        assert quarter_bars(["1Q", "2Q"], [100.0, None]).count("<rect") == 1

    def test_empty_input_is_empty_output(self):
        from arc.render.charts import quarter_bars

        assert quarter_bars([], []) == ""
        assert quarter_bars(["1Q"], [None]) == ""


class TestMarginLine:
    """**비율은 막대가 아니라 선이다** — 크기가 아니라 수준이다."""

    def test_draws_a_point_per_value(self):
        from arc.render.charts import margin_line

        assert margin_line(["1Q", "2Q", "3Q"], [8.1, 9.3, 7.5]).count("<circle") == 3

    def test_one_point_is_not_a_line(self):
        from arc.render.charts import margin_line

        assert margin_line(["1Q"], [8.1]) == ""

    def test_gaps_do_not_break_the_path(self):
        from arc.render.charts import margin_line

        svg = margin_line(["1Q", "2Q", "3Q"], [8.1, None, 7.5])
        assert svg.count("<circle") == 2 and "<path" in svg

    def test_flat_series_does_not_divide_by_zero(self):
        from arc.render.charts import margin_line

        assert "<path" in margin_line(["1Q", "2Q"], [8.0, 8.0])


class TestTablesScrollInsteadOfPushingThePage:
    """재무 표는 **항목명 + 5개년 = 6열**이다 (D90).

    390px 폰에서 감싸개가 없으면 글자가 뭉개지거나 페이지 전체가 옆으로
    밀린다 — 본문을 읽으려고 좌우로 흔들게 된다.
    """

    def _html(self, md: str) -> str:
        from arc.llm.number_registry import NumberRegistry
        from arc.render.html import render_html

        return render_html(md, NumberRegistry())

    def test_a_table_is_wrapped_in_a_scroll_box(self):
        out = self._html("| a | b |\n|---|---|\n| 1 | 2 |\n")
        assert '<div class="table-scroll"><table>' in out
        assert out.count("</table></div>") == 1

    def test_the_wrapper_closes_exactly_once_per_table(self):
        """두 표가 나오면 상자도 둘이어야 한다 — 하나가 안 닫히면 뒤 문단이
        전부 그 상자 안으로 들어간다."""
        md = "| a |\n|---|\n| 1 |\n\n문단\n\n| b |\n|---|\n| 2 |\n"
        out = self._html(md)
        assert out.count('<div class="table-scroll">') == 2
        assert out.count("</table></div>") == 2

    def test_prose_is_untouched(self):
        """**표가 아닌 것은 안 감싼다.** 문단까지 스크롤 상자에 들어가면
        본문 폭이 화면을 넘긴다."""
        out = self._html("그냥 문단이다.\n")
        assert "table-scroll" not in out

    def test_a_literal_table_word_in_prose_does_not_break_it(self):
        """정규식으로 HTML을 긁었으면 여기서 깨진다."""
        out = self._html("본문에 `<table>` 이라는 글자가 나온다.\n")
        assert "table-scroll" not in out
