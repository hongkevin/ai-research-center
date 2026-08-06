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
