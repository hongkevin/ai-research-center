"""SnapshotStore point-in-time 저장/조회 라운드트립 테스트."""

import datetime as dt

import pytest

from arc.store.snapshot import SnapshotStore

T0 = dt.datetime(2026, 7, 1, 9, 0, tzinfo=dt.timezone.utc)
T1 = dt.datetime(2026, 7, 15, 9, 0, tzinfo=dt.timezone.utc)


@pytest.fixture
def store(tmp_path):
    return SnapshotStore(tmp_path)


def test_roundtrip(store):
    records = [{"symbol": "078890", "close": 5150.0}, {"symbol": "005930", "close": 71000.0}]
    store.save_snapshot("prices", records, snapshot_at=T0)
    out = store.read_as_of("prices")
    assert len(out) == 2
    assert {r["symbol"] for r in out} == {"078890", "005930"}
    assert all("snapshot_at" in r for r in out)  # 모든 행에 수집 시각 기록


def test_as_of_returns_snapshot_known_at_that_time(store):
    """point-in-time 핵심 계약: as_of 시점에 '알 수 있었던' 데이터만 반환."""
    store.save_snapshot("fin", [{"symbol": "078890", "rev": 100}], snapshot_at=T0)
    store.save_snapshot("fin", [{"symbol": "078890", "rev": 120}], snapshot_at=T1)

    # T0와 T1 사이 시점 → T0 스냅샷 (수정 전 데이터)
    mid = T0 + dt.timedelta(days=3)
    assert store.read_as_of("fin", as_of=mid)[0]["rev"] == 100
    # 최신 조회 → T1 스냅샷
    assert store.read_as_of("fin")[0]["rev"] == 120
    # 첫 스냅샷 이전 → 아무것도 몰랐던 시점
    assert store.read_as_of("fin", as_of=T0 - dt.timedelta(days=1)) == []


def test_empty_snapshot_rejected(store):
    with pytest.raises(ValueError):
        store.save_snapshot("prices", [])


def test_list_snapshots_sorted(store):
    store.save_snapshot("d", [{"x": 2}], snapshot_at=T1)
    store.save_snapshot("d", [{"x": 1}], snapshot_at=T0)
    assert store.list_snapshots("d") == [T0, T1]
    assert store.list_snapshots("nonexistent") == []
