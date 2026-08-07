"""피어 비교표 — 여러 종목을 나란히 세운다.

이 파일이 지키는 것은 하나다: **표가 숫자를 새로 만들지 않는다.** 모든 칸이
구성원 카드의 레지스트리에서 온 표시 문자열이어야 하고, 되짚을 수 있어야 한다.
"""

from __future__ import annotations

from arc.finmodel.peer import ROWS, basis_label, build_peer_table
from arc.store.cards import peer_member


def _entry(key, display, value, unit="원", label=""):
    return {
        "key": key,
        "value": value,
        "unit": unit,
        "display": display,
        "label": label or key,
        "provenance": {"source": "DART"},
    }


def _member(symbol, year=2025, period="ANNUAL", *, status="ready", company="", **metrics):
    m = peer_member(
        symbol, company=company or symbol, card_id=symbol * 2, year=year, period=period, status=status
    )
    reg = []
    for base, spec in metrics.items():
        display, value, unit = spec
        reg.append(_entry(f"{base}_{year}a", display, value, unit))
    m["registry"] = reg
    return m


class TestBuild:
    def test_values_come_from_the_registry_verbatim(self):
        m = _member("047810", revenue=("1조 4,575억원", 1457453208000, "원"))
        t = build_peer_table([m])
        row = next(r for r in t.rows if r.label == "매출액")
        # **표시 문자열을 다시 포맷하지 않는다** — 본문과 갈라지면 안 된다
        assert row.cells[0].display == "1조 4,575억원"
        assert row.cells[0].absent is False

    def test_a_cell_can_be_traced_back(self):
        m = _member("047810", revenue=("1조원", 1e12, "원"))
        cell = build_peer_table([m]).rows[0].cells[0]
        assert cell.key == "revenue_2025a"
        assert cell.card_id == "047810" * 2

    def test_the_year_comes_from_each_member_not_the_table(self):
        """키에 연도가 박혀 있고 카드마다 다르다."""
        a = _member("047810", year=2025, revenue=("1조원", 1e12, "원"))
        b = _member("064350", year=2026, revenue=("2조원", 2e12, "원"))
        row = build_peer_table([a, b]).rows[0]
        assert [c.key for c in row.cells] == ["revenue_2025a", "revenue_2026a"]
        assert [c.display for c in row.cells] == ["1조원", "2조원"]

    def test_missing_metric_is_a_dash_not_a_zero(self):
        a = _member("047810", revenue=("1조원", 1e12, "원"))
        b = _member("064350", operating_income=("100억원", 1e10, "원"))
        rows = {r.label: r for r in build_peer_table([a, b]).rows}
        assert rows["매출액"].cells[1].display == "—"
        assert rows["매출액"].cells[1].absent is True
        assert rows["매출액"].coverage == 1

    def test_a_row_nobody_has_is_dropped(self):
        """전부 「—」인 줄이 열둘 서 있으면 표가 아니라 빈칸 격자다."""
        t = build_peer_table([_member("047810", revenue=("1조원", 1e12, "원"))])
        assert [r.label for r in t.rows] == ["매출액"]
        assert len(ROWS) > 1  # 나머지 줄이 있는데도 빠졌다는 뜻

    def test_pending_members_keep_their_column(self):
        """빼 버리면 화면에서 종목이 사라져 「왜 안 나오지」가 된다."""
        a = _member("047810", revenue=("1조원", 1e12, "원"))
        b = peer_member("012450", company="한화에어로스페이스", status="pending")
        t = build_peer_table([a, b])
        assert [c.symbol for c in t.columns] == ["047810", "012450"]
        assert t.columns[1].ready is False
        assert t.rows[0].cells[1].absent is True

    def test_a_pending_member_does_not_leak_numbers(self):
        """준비 안 된 구성원의 레지스트리는 있어도 읽지 않는다."""
        b = _member("012450", status="pending", revenue=("9조원", 9e12, "원"))
        t = build_peer_table([b])
        assert t.rows == []


class TestBasis:
    def test_same_basis_is_not_mixed(self):
        a = _member("047810", year=2025, period="ANNUAL", revenue=("1조원", 1e12, "원"))
        b = _member("064350", year=2025, period="ANNUAL", revenue=("2조원", 2e12, "원"))
        t = build_peer_table([a, b])
        assert t.mixed_basis is False
        assert t.note == "2025년 연간 기준"

    def test_mixed_basis_is_flagged_and_named(self):
        """**표가 조용히 거짓말하는 자리.** 값이 4:3으로 어긋난다."""
        a = _member("047810", year=2025, period="ANNUAL", revenue=("1조원", 1e12, "원"))
        b = _member("064350", year=2026, period="Q1", revenue=("2조원", 2e12, "원"))
        t = build_peer_table([a, b])
        assert t.mixed_basis is True
        assert "2025년 연간" in t.note
        assert "2026년 1분기 누적" in t.note

    def test_pending_members_do_not_make_it_mixed(self):
        a = _member("047810", year=2025, revenue=("1조원", 1e12, "원"))
        b = peer_member("012450", status="pending")
        assert build_peer_table([a, b]).mixed_basis is False

    def test_basis_label(self):
        assert basis_label(2026, "Q1") == "2026년 1분기 누적"
        assert basis_label(2025, "ANNUAL") == "2025년 연간"
        assert basis_label(0, "ANNUAL") == ""


class TestNoNewNumbers:
    def test_the_table_has_no_average_or_rank(self):
        """평균·중앙값을 여기서 만들면 **출처 없는 숫자가 표에 앉는다.**

        내고 싶으면 레지스트리에 등록된 수치여야 한다.
        """
        a = _member("047810", revenue=("1조원", 1e12, "원"))
        b = _member("064350", revenue=("3조원", 3e12, "원"))
        t = build_peer_table([a, b])
        row = t.rows[0]
        assert len(row.cells) == 2  # 평균 칸이 붙지 않았다
        displays = [c.display for c in row.cells]
        assert "2조원" not in displays
