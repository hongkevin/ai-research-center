"""웹 표면 테스트 — 네트워크 없이 파이프라인을 가짜로 채운다.

화면이 증명해야 하는 것을 그대로 검사한다:

  1. 수치가 **출처를 달고** HTML에 나온다 (평문 치환이면 차별점이 사라진다).
  2. 게이트가 막으면 **본문을 렌더하지 않는다** (검토자가 결과로 착각한다).
  3. 생성과 발간은 다르다 — 생성은 이력에 남지 않는다.
"""

from __future__ import annotations

import datetime as dt

import pytest

from arc.data.base import Provenance
from arc.llm.number_registry import NumberEntry, NumberRegistry
from arc.render.html import render_html, substitute_with_spans

PROV = Provenance(
    source="opendart",
    retrieved_at=dt.datetime(2026, 8, 1, tzinfo=dt.UTC),
    source_ref="20260319001417",
    source_url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260319001417",
)


def _registry():
    reg = NumberRegistry()
    reg.register_all(
        [
            NumberEntry(
                key="revenue_2025a",
                value=536_289_118_812,
                unit="원",
                display="5,363억원",
                provenance=PROV,
                label="매출액 (2025A)",
            ),
            NumberEntry(
                key="operating_margin_2025a",
                value=40.0,
                unit="%",
                display="40.0%",
                provenance=PROV,
                label="영업이익률 (2025A)",
                formula="operating_income_2025a / revenue_2025a",
                inputs=["operating_income_2025a", "revenue_2025a"],
            ),
            NumberEntry(
                key="revenue_2026e",
                value=770_000_000_000,
                unit="원",
                display="7,704억원",
                provenance=PROV,
                label="매출액 (2026E)",
            ),
        ]
    )
    return reg


class TestNumbersCarryProvenance:
    def test_number_is_keyboard_reachable(self):
        """마우스로만 열리면 키보드 검토자에게는 출처가 **아예 없다.**

        "숫자를 누르면 출처가 나온다"가 이 제품의 논증이므로, 그 상호작용에
        닿는 경로가 하나뿐이면 안 된다.
        """
        out = substitute_with_spans("매출은 {{num:revenue_2025a}}이다.", _registry())
        assert 'role="button"' in out
        assert 'tabindex="0"' in out

    def test_value_wrapped_with_source_attributes(self):
        out = substitute_with_spans("매출은 {{num:revenue_2025a}}이다.", _registry())
        assert 'class="num"' in out
        assert 'data-key="revenue_2025a"' in out
        assert 'data-doc="20260319001417"' in out
        assert "5,363억원" in out

    def test_formula_and_inputs_exposed(self):
        out = substitute_with_spans("{{num:operating_margin_2025a}}", _registry())
        assert "data-formula=" in out
        assert "operating_income_2025a" in out

    def test_estimate_marked_differently(self):
        """실적과 추정이 화면에서 구분돼야 한다 — 같아 보이면 독자가 섞어 읽는다."""
        out = substitute_with_spans("{{num:revenue_2026e}}", _registry())
        assert "num--estimate" in out

    def test_particle_correction_matches_plain_render(self):
        """화면과 파일의 문장이 갈라지면 안 된다."""
        reg = _registry()
        text = "영업이익률은 {{num:operating_margin_2025a}}으로 개선됐다."
        assert "40.0%로 개선됐다" in reg.render_text(text)
        assert "40.0%</span>로 개선됐다" in substitute_with_spans(text, reg)

    def test_unregistered_placeholder_left_intact(self):
        out = substitute_with_spans("{{num:nope_2025a}}", _registry())
        assert "{{num:nope_2025a}}" in out

    def test_pipe_in_formula_does_not_break_tables(self):
        """YoY 산식은 절댓값을 `|x|`로 쓴다. 표 안에서 셀 구분자로 해석되면
        뒤 셀이 통째로 이스케이프된 문자열로 렌더된다 (실측)."""
        reg = NumberRegistry()
        reg.register(
            NumberEntry(
                key="revenue_yoy_2025a",
                value=53.2,
                unit="%",
                display="53.2%",
                provenance=PROV,
                label="매출액 YoY",
                formula="(revenue_2025a - revenue_2024a) / |revenue_2024a|",
            )
        )
        md = "| 항목 | 증감률 |\n|---|---|\n| 매출액 | {{num:revenue_yoy_2025a}} |\n"
        html = render_html(md, reg)
        assert "&#124;" in html
        assert "&lt;span" not in html  # 셀이 이스케이프되지 않았다
        assert html.count("<td>") == 2

    def test_value_is_html_escaped(self):
        reg = NumberRegistry()
        reg.register(
            NumberEntry(
                key="x_2025a",
                value=1,
                unit="",
                display="<script>alert(1)</script>",
                provenance=PROV,
            )
        )
        out = substitute_with_spans("{{num:x_2025a}}", reg)
        assert "<script>" not in out
        assert "&lt;script&gt;" in out


class TestMarkdownRendering:
    def test_tables_render_and_spans_survive(self):
        md = "| 항목 | 값 |\n|---|---|\n| 매출 | {{num:revenue_2025a}} |\n"
        html = render_html(md, _registry())
        assert "<table>" in html
        assert 'class="num"' in html

    def test_headings_render(self):
        html = render_html("## 2. 요약\n\n본문.\n", _registry())
        assert "<h2>" in html


class TestViewModel:
    def _result(self, passed=True):
        """파이프라인 결과의 최소 대역 — 웹 레이어만 검사한다."""

        class _Gate:
            def __init__(self, ok):
                self.passed = ok
                self.violations = (
                    []
                    if ok
                    else [
                        type("V", (), {"rule": "unregistered_number", "line": 3, "detail": "x"})()
                    ]
                )

            def summary(self):
                return "G0 통과" if self.passed else "G0 차단 1건"

        class _R:
            symbol, fiscal_year = "214450", 2025
            company = type(
                "C",
                (),
                {
                    "name": "(주)파마리서치",
                    "symbol": "214450",
                    "market": type("M", (), {"value": "KOSDAQ"})(),
                },
            )()
            statement = type("S", (), {"consolidation": type("K", (), {"value": "CFS"})()})()
            metrics = type(
                "M", (), {"values": {}, "missing_labels": ["매출원가"], "fiscal_year": 2025}
            )()
            registry = _registry()
            assembled = "## 2. 요약\n\n매출은 {{num:revenue_2025a}}이다.\n"
            estimates = None
            revisions: list = []
            segments = None
            business = None
            narration = None
            # **엔진이 계산하는 것은 전부 여기 있다** (D73). 가짜가 진짜보다
            # 얇으면 화면이 버리는 필드를 테스트가 못 잡는다 — 실제로 못 잡았다.
            lenses = None
            report_info = None
            valuation = None
            segment_profit = None
            info_error = None
            quarters = None
            # 첫 화면 세 줄 (D87). **실제 ReportResult에 있는 것은 여기도 있어야
            # 한다** — 아래 주석이 말하는 그 이유다.
            headline: dict = {}
            # 파이프라인 단계 기록. 실제 ReportResult는 항상 채운다.
            stages = [
                type(
                    "St",
                    (),
                    {
                        "key": "metrics",
                        "label": "지표 추출·계산",
                        "status": "ok",
                        "summary": "지표 13종",
                        "checks": [],
                        "registered": 3,
                        "note": "",
                    },
                )(),
                type(
                    "St",
                    (),
                    {
                        "key": "segment_profit",
                        "label": "부문별 손익",
                        "status": "absent",
                        "summary": "단일 부문",
                        "checks": [],
                        "registered": 0,
                        "note": "부문이 하나라 전사 손익과 같습니다.",
                    },
                )(),
            ]

        r = _R()
        r.gate = _Gate(passed)
        return r

    def test_passing_gate_renders_body(self):
        from arc.web.app import _to_view

        vm = _to_view(self._result(True))
        assert vm.gate_passed
        assert 'class="num"' in vm.body_html
        assert vm.bindings

    def test_blocked_gate_renders_nothing(self):
        """차단된 초안을 보여주면 검토자가 결과로 착각한다."""
        from arc.web.app import _to_view

        vm = _to_view(self._result(False))
        assert not vm.gate_passed
        assert vm.body_html == ""
        assert vm.bindings == []
        assert vm.violations

    def test_missing_metrics_use_korean_labels(self):
        from arc.web.app import _to_view

        assert _to_view(self._result(True)).metrics_missing == ["매출원가"]


class TestAssumptionParsing:
    @pytest.mark.parametrize(
        ("raw", "want"),
        [
            ("revenue_growth=15", {"revenue_growth": 15.0}),
            ("a=1\nb=2", {"a": 1.0, "b": 2.0}),
            ("a=1, b=2", {"a": 1.0, "b": 2.0}),
            ("", {}),
            ("  \n  ", {}),
        ],
    )
    def test_parses(self, raw, want):
        from arc.web.app import _parse_overrides

        assert _parse_overrides(raw) == want

    @pytest.mark.parametrize("raw", ["revenue_growth", "revenue_growth=abc"])
    def test_bad_input_raises_not_silently_ignored(self, raw):
        """조용히 무시하면 사용자가 가정을 지정했다고 착각한 채 발간한다."""
        from arc.web.app import _parse_overrides

        with pytest.raises(ValueError):
            _parse_overrides(raw)


class TestCompanySearch:
    """종목코드를 외우지 않아도 되게 한다 — corpCode.xml이 전 상장사 목록이다."""

    INDEX = {
        "214450": {"corp_name": "파마리서치", "stock_code": "214450"},
        "217950": {"corp_name": "파마리서치바이오", "stock_code": "217950"},
        "068270": {"corp_name": "셀트리온", "stock_code": "068270"},
        "068760": {"corp_name": "셀트리온제약", "stock_code": "068760"},
        "005930": {"corp_name": "삼성전자", "stock_code": "005930"},
        "000660": {"corp_name": "(주)에스케이하이닉스", "stock_code": "000660"},
    }

    def _search(self, q, limit=10):
        from arc.data.kr.dart import search_corp_index

        return search_corp_index(self.INDEX, q, limit)

    def test_exact_symbol_first(self):
        assert self._search("214450")[0]["symbol"] == "214450"

    def test_exact_name_beats_prefix(self):
        """'셀트리온'은 셀트리온제약보다 셀트리온이 먼저다."""
        assert self._search("셀트리온")[0]["symbol"] == "068270"

    def test_prefix_beats_substring(self):
        names = [h["name"] for h in self._search("파마리서치")]
        assert names[0] == "파마리서치"
        assert "파마리서치바이오" in names

    def test_legal_form_ignored(self):
        """'(주)에스케이하이닉스'를 '에스케이하이닉스'로 찾을 수 있어야 한다."""
        assert self._search("에스케이하이닉스")[0]["symbol"] == "000660"

    def test_empty_query_returns_nothing(self):
        assert self._search("") == []
        assert self._search("   ") == []

    def test_no_match_returns_empty(self):
        assert self._search("존재하지않는회사") == []

    def test_limit_respected(self):
        assert len(self._search("셀", limit=1)) == 1


class TestSymbolResolution:
    def _resolve(self, value, monkeypatch):
        import arc.web.app as web

        class _Fake:
            def search_companies(self, q, limit=6):
                from arc.data.kr.dart import search_corp_index

                return search_corp_index(TestCompanySearch.INDEX, q, limit)

        monkeypatch.setattr(web, "_search_provider", lambda: _Fake())
        return web._resolve_symbol(value)

    def test_six_digit_passes_through(self, monkeypatch):
        assert self._resolve("214450", monkeypatch) == "214450"

    def test_name_resolved(self, monkeypatch):
        assert self._resolve("삼성전자", monkeypatch) == "005930"

    def test_exact_name_wins_over_ambiguity(self, monkeypatch):
        assert self._resolve("셀트리온", monkeypatch) == "068270"

    def test_ambiguous_asks_instead_of_guessing(self, monkeypatch):
        """임의로 고르면 사용자가 다른 회사의 리포트를 자기 것으로 착각한다."""
        with pytest.raises(ValueError, match="여럿"):
            self._resolve("파마", monkeypatch)

    def test_unknown_name_raises(self, monkeypatch):
        with pytest.raises(ValueError, match="찾지 못"):
            self._resolve("없는회사", monkeypatch)


class TestCharts:
    """차트는 **수치의 두 번째 출처가 되면 안 된다.**

    SVG `<text>`는 G0 스캔 대상이 아니라, 값 라벨을 넣으면 게이트 밖에서
    숫자가 생긴다. 형태만 그리고 정확한 값은 표에 둔다.
    """

    def _slices(self):
        from arc.render.charts import Slice

        return [Slice("의료기기", 58.6), Slice("화장품", 24.6), Slice("의약품", 15.4)]

    def test_bar_has_no_value_labels(self):
        from arc.render.charts import segment_bar

        svg = segment_bar(self._slices())
        assert "58.6" not in svg
        assert "의료기기" in svg  # 이름은 괜찮다 — 수치가 아니다

    def test_single_slice_not_drawn(self):
        """100% 막대는 정보가 없다."""
        from arc.render.charts import Slice, segment_bar

        assert segment_bar([Slice("반도체", 100.0)]) == ""

    def test_empty_input_not_drawn(self):
        from arc.render.charts import segment_bar, trend_bars

        assert segment_bar([]) == ""
        assert trend_bars([], []) == ""

    def test_trend_scales_to_peak(self):
        from arc.render.charts import trend_bars

        svg = trend_bars(["2023", "2024", "2025"], [("매출액", [1.0, 2.0, 4.0])])
        assert "<svg" in svg
        assert svg.count("<rect") == 3

    def test_all_zero_series_not_drawn(self):
        from arc.render.charts import trend_bars

        assert trend_bars(["2024", "2025"], [("매출액", [0.0, 0.0])]) == ""

    def test_labels_escaped(self):
        from arc.render.charts import Slice, segment_bar

        svg = segment_bar([Slice("<script>", 60.0), Slice("정상", 40.0)])
        assert "<script>" not in svg
        assert "&lt;script&gt;" in svg


class TestStoreResilience:
    """볼륨이 안 붙어도 **리포트 생성은 계속돼야 한다.**

    추정 이력은 revision 추적을 위한 향상이지 생성의 전제가 아니다.
    배포 직후 볼륨 설정이 틀렸다고 화면 전체가 죽으면 안 된다.
    """

    def test_unwritable_path_returns_none(self, monkeypatch, tmp_path):
        import arc.web.app as web

        blocked = tmp_path / "blocked" / "store"
        monkeypatch.setattr(web, "STORE_DIR", blocked)
        (tmp_path / "blocked").write_text("파일이라 하위 디렉터리를 못 만든다")
        assert web._open_store() is None

    def test_writable_path_opens(self, monkeypatch, tmp_path):
        import arc.web.app as web

        monkeypatch.setattr(web, "STORE_DIR", tmp_path / "store")
        assert web._open_store() is not None

    def test_health_reports_writable(self, monkeypatch, tmp_path):
        """배포 직후 볼륨이 붙었는지 이걸로 확인한다."""
        import arc.web.app as web

        monkeypatch.setattr(web, "STORE_DIR", tmp_path / "store")
        status = web._store_status()
        assert status["writable"] is True
        assert str(tmp_path) in str(status["path"])

    def test_health_reports_reason_when_broken(self, monkeypatch, tmp_path):
        import arc.web.app as web

        (tmp_path / "blocked").write_text("x")
        monkeypatch.setattr(web, "STORE_DIR", tmp_path / "blocked" / "store")
        status = web._store_status()
        assert status["writable"] is False
        assert status["reason"]

    def test_probe_file_cleaned_up(self, monkeypatch, tmp_path):
        """확인용 파일이 남으면 스냅샷 목록에 섞인다."""
        import arc.web.app as web

        monkeypatch.setattr(web, "STORE_DIR", tmp_path / "store")
        web._store_status()
        assert not list((tmp_path / "store").glob(".write-probe"))


class TestProviderSharing:
    """요청마다 DartProvider를 새로 만들면 corpCode.xml(1.5MB)을 매번 다시 받는다.

    로컬(한국)에서는 1초라 안 보이지만 배포 리전이 멀면 그대로 드러난다 —
    실측: Railway에서 '회사 정보 조회' 한 단계에 8.9초가 걸렸다.
    """

    def test_same_instance_reused(self, monkeypatch):
        import arc.web.app as web

        created = []

        class _Fake:
            def __init__(self):
                created.append(self)

            def load_corp_codes(self):
                return {}

        monkeypatch.setattr(web, "_PROVIDER", None)
        monkeypatch.setattr(web, "DartProvider", _Fake)
        a, b = web._shared_provider(), web._shared_provider()
        assert a is b
        assert len(created) == 1

    def test_search_uses_the_same_instance(self, monkeypatch):
        """검색과 생성이 캐시를 나눠 가지면 절반은 여전히 다시 받는다."""
        import arc.web.app as web

        class _Fake:
            def load_corp_codes(self):
                return {}

        monkeypatch.setattr(web, "_PROVIDER", None)
        monkeypatch.setattr(web, "DartProvider", _Fake)
        assert web._search_provider() is web._shared_provider()


class TestJobResultEndpoint:
    """SSE가 `done`을 알린 뒤 화면이 결과를 읽어 가는 경로.

    아직 끝나지 않은 작업에 200을 주면 화면이 **빈 결과를 결과로** 받는다.
    상태를 구분해서 알려야 한다.
    """

    def _web(self, monkeypatch, job=None):
        import arc.web.app as web
        from arc.web.jobs import JobStore

        store = JobStore()
        if job is not None:
            store._jobs[job.id] = job
        monkeypatch.setattr(web, "JOBS", store)
        return web

    def test_unknown_job_is_404(self, monkeypatch):
        """TTL이 지나 정리된 작업도 여기로 온다 — 화면이 다시 생성하도록."""
        web = self._web(monkeypatch)
        assert web.api_job_result("nope").status_code == 404

    def test_unfinished_job_is_409_not_an_empty_result(self, monkeypatch):
        from arc.web.jobs import Job

        web = self._web(monkeypatch, Job(id="j1"))
        assert web.api_job_result("j1").status_code == 409

    def test_failed_job_reports_the_reason(self, monkeypatch):
        from arc.web.jobs import Job

        web = self._web(monkeypatch, Job(id="j2", done=True, error="ValueError: 없는 종목"))
        r = web.api_job_result("j2")
        assert r.status_code == 400
        assert "없는 종목" in r.body.decode()

    def test_finished_job_returns_the_view_model(self, monkeypatch):
        import json

        from arc.web.app import ViewModel
        from arc.web.jobs import Job

        vm = ViewModel(symbol="214450", year=2025, company="파마리서치", gate_passed=True)
        web = self._web(monkeypatch, Job(id="j3", done=True, result=vm))
        r = web.api_job_result("j3")
        assert r.status_code == 200
        body = json.loads(r.body)
        assert body["symbol"] == "214450"
        assert body["gate_passed"] is True


class TestStagesReachTheScreen:
    """파이프라인 기록이 화면까지 오는가.

    엔진이 단계마다 무엇을 검산했는지 이미 알고 있어도 `_to_view`가 버리면
    화면은 그대로 블랙박스다.
    """

    def _vm(self, passed: bool):
        from arc.web.app import _to_view

        return _to_view(TestViewModel()._result(passed))

    def test_stages_are_carried(self):
        vm = self._vm(True)
        assert vm.stages
        assert {"key", "label", "status", "summary", "checks", "registered", "note"} <= set(
            vm.stages[0]
        )

    def test_stages_survive_a_blocked_gate(self):
        """차단됐을 때야말로 어느 단계가 어긋났는지 봐야 한다."""
        vm = self._vm(False)
        assert not vm.gate_passed
        assert vm.body_html == ""
        assert vm.stages, "본문은 숨기더라도 과정은 남겨야 한다"
