"""시세 자동 갱신 — **아무도 안 눌러도 돌아간다** (D87).

이 파일이 지키는 것:

* **두 번 겹쳐 돌지 않는다.** 같은 볼륨에 두 백필이 쓰면 원자적 교체가
  서로를 덮어 그사이 받은 것이 사라진다
* **중간에 쓴다.** 400일치가 5분인데 끝에 한 번만 쓰면, 그사이 컨테이너가
  재시작할 때 받은 것이 통째로 사라진다
* **실패가 앱을 죽이지 않는다.** 갱신은 부산물이고, 부산물이 본 일을
  멈추게 하면 안 된다
"""

from __future__ import annotations

import datetime as dt
import threading

import pytest

from arc.finmodel import market_facts as mf
from arc.finmodel.price_store import backfill
from arc.web import refresh


class _Provider:
    """받은 날짜를 기록하는 가짜. **네트워크를 안 탄다.**"""

    api_key = "x"

    def __init__(self, rows: int = 3, fail_at: str = "") -> None:
        self.asked: list[str] = []
        self.rows = rows
        self.fail_at = fail_at


def _fake_fetch(provider, stamp):
    provider.asked.append(stamp)
    if stamp == provider.fail_at:
        raise RuntimeError("끊김")
    from arc.finmodel.price_store import _Full

    return [
        (
            f"00000{i}",
            _Full(
                close=1000.0 + i,
                high=1100.0,
                low=900.0,
                turnover=1e9,
                cap=2e11,
                shares=1e7,
                board="KOSDAQ",
            ),
        )
        for i in range(provider.rows)
    ]


@pytest.fixture
def patched(monkeypatch):
    monkeypatch.setattr("arc.finmodel.price_store._fetch_day_full", _fake_fetch)
    return _Provider()


class TestItWritesAsItGoes:
    def test_a_crash_halfway_keeps_what_landed(self, tmp_path, patched, monkeypatch):
        """**컨테이너가 5분을 못 버틸 수도 있다.**

        중간에 안 쓰면 재시작할 때 받은 것이 통째로 사라지고, 다음 실행이
        처음부터 다시 받는다 — 영원히 못 끝날 수도 있다.
        """
        # 3거래일째에 끊는다. 그 앞 2일은 남아 있어야 한다
        stamps = []

        def fetch(provider, stamp):
            stamps.append(stamp)
            if len(stamps) > 2:
                raise RuntimeError("끊김")
            return _fake_fetch(provider, stamp)

        monkeypatch.setattr("arc.finmodel.price_store._fetch_day_full", fetch)
        with pytest.raises(RuntimeError):
            backfill(
                tmp_path,
                days=10,
                provider=patched,
                interval=0,
                flush_every=1,
                today=dt.date(2026, 8, 7),
            )
        assert mf.available(tmp_path) == 3, "끊기기 전 것은 남아야 한다"

    def test_the_next_run_does_not_refetch(self, tmp_path, patched):
        """**이미 받은 날은 건너뛴다.** 매일 400일을 훑으면 260콜이 매일이다."""
        kw = {
            "days": 5,
            "provider": patched,
            "interval": 0,
            "today": dt.date(2026, 8, 7),
        }
        first = backfill(tmp_path, **kw)
        asked = len(patched.asked)
        assert first["fetched_days"] > 0

        second = backfill(tmp_path, **kw)
        assert second["fetched_days"] == 0
        assert len(patched.asked) == asked, "같은 날을 다시 묻지 않아야 한다"

    def test_symbols_are_counted_once(self, tmp_path, patched):
        """**중간에 쓰기 시작한 뒤로 같은 종목이 여러 번 세어졌다.**

        더하면 「종목 59,740개」 같은 수가 나온다 — 집합으로 센다.
        """
        got = backfill(
            tmp_path,
            days=10,
            provider=patched,
            interval=0,
            flush_every=1,
            today=dt.date(2026, 8, 7),
        )
        assert got["symbols"] == 3, "받은 날이 몇이든 종목은 셋이다"


class TestRunOnce:
    def test_two_runs_do_not_overlap(self, tmp_path, patched, monkeypatch):
        """**같은 볼륨에 둘이 쓰면 원자적 교체가 서로를 덮는다.**"""
        started = threading.Event()
        release = threading.Event()

        def slow(base, **kwargs):
            started.set()
            release.wait(timeout=5)
            return {"total_symbols": 1, "market_symbols": 1, "fetched_days": 1}

        monkeypatch.setattr("arc.finmodel.price_store.backfill", slow)
        monkeypatch.setattr("arc.finmodel.price_store.backfill_indices", lambda *a, **k: {})

        first = threading.Thread(target=refresh.run_once, args=(tmp_path,), kwargs={"reason": "a"})
        first.start()
        assert started.wait(timeout=5)

        second = refresh.run_once(tmp_path, reason="b")
        assert "skipped" in second, "두 번째는 그냥 물러나야 한다"

        release.set()
        first.join(timeout=5)

    def test_a_failure_is_recorded_not_raised(self, tmp_path, monkeypatch):
        """**갱신 실패가 앱을 죽이면 안 된다.** 부산물이 본 일을 멈추게 한다."""
        monkeypatch.setattr(
            "arc.finmodel.price_store.backfill",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("DART가 막았다")),
        )
        got = refresh.run_once(tmp_path, reason="test")
        assert got["ok"] is False
        assert "DART가 막았다" in got["error"]
        assert not got["running"], "실패해도 잠금이 풀려야 한다"

    def test_the_index_failing_does_not_lose_the_prices(self, tmp_path, monkeypatch):
        """지수는 곁가지다 — 그것 때문에 시세를 버리지 않는다."""
        monkeypatch.setattr(
            "arc.finmodel.price_store.backfill",
            lambda *a, **k: {"total_symbols": 7, "market_symbols": 7, "fetched_days": 2},
        )
        monkeypatch.setattr(
            "arc.finmodel.price_store.backfill_indices",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("지수 실패")),
        )
        got = refresh.run_once(tmp_path, reason="test")
        assert got["ok"] is True
        assert got["symbols"] == 7
