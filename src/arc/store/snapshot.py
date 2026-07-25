"""DuckDB + Parquet point-in-time 저장소 (ARCHITECTURE.md §5.2) — 최소 구현.

처음부터 point-in-time으로 짓는 이유: 이후 백테스트("과거 시점 데이터만으로
생성한 보고서가 유효했는가")가 품질 증명의 핵심이기 때문.

모델:
  - 데이터셋(dataset)별로 스냅샷 단위 append-only 저장.
  - 저장 시 snapshot_at(수집 시각, UTC)을 모든 행에 기록.
  - as_of 조회: `as_of` 시점 이하에서 가장 최신인 스냅샷 하나를 통째로 반환
    → "그 시점에 알 수 있었던 데이터"를 재현한다.

레이아웃:
  {base_dir}/{dataset}/snapshot-YYYYmmddTHHMMSSffffff.parquet
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

_SNAPSHOT_PREFIX = "snapshot-"
_TS_FORMAT = "%Y%m%dT%H%M%S%f"


class SnapshotStore:
    """Parquet 스냅샷 저장 + DuckDB as-of 조회."""

    def __init__(self, base_dir: str | Path) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    # -------------------------------------------------------------- 저장

    def save_snapshot(
        self,
        dataset: str,
        records: list[dict],
        snapshot_at: dt.datetime | None = None,
    ) -> Path:
        """레코드 목록을 스냅샷 1개로 저장하고 파일 경로를 반환한다.

        snapshot_at 미지정 시 현재 UTC. 모든 행에 snapshot_at 컬럼이 추가된다.
        """
        if not records:
            raise ValueError("빈 스냅샷은 저장하지 않는다")
        snapshot_at = snapshot_at or dt.datetime.now(dt.timezone.utc)
        if snapshot_at.tzinfo is None:
            snapshot_at = snapshot_at.replace(tzinfo=dt.timezone.utc)

        table = pa.Table.from_pylist(records)
        ts_array = pa.array([snapshot_at] * len(records), type=pa.timestamp("us", tz="UTC"))
        table = table.append_column("snapshot_at", ts_array)

        dataset_dir = self.base_dir / dataset
        dataset_dir.mkdir(parents=True, exist_ok=True)
        path = dataset_dir / f"{_SNAPSHOT_PREFIX}{snapshot_at.strftime(_TS_FORMAT)}.parquet"
        pq.write_table(table, path)
        return path

    # -------------------------------------------------------------- 조회

    def list_snapshots(self, dataset: str) -> list[dt.datetime]:
        """데이터셋의 스냅샷 시각 목록 (오름차순)."""
        dataset_dir = self.base_dir / dataset
        if not dataset_dir.exists():
            return []
        out = []
        for p in sorted(dataset_dir.glob(f"{_SNAPSHOT_PREFIX}*.parquet")):
            raw = p.stem.removeprefix(_SNAPSHOT_PREFIX)
            out.append(dt.datetime.strptime(raw, _TS_FORMAT).replace(tzinfo=dt.timezone.utc))
        return out

    def read_as_of(self, dataset: str, as_of: dt.datetime | None = None) -> list[dict]:
        """`as_of` 시점 이하 최신 스냅샷의 레코드를 반환한다 (point-in-time 조회).

        as_of 미지정 시 최신 스냅샷. 해당 시점 이전 스냅샷이 없으면 빈 목록.
        """
        if as_of is not None and as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=dt.timezone.utc)
        candidates = [
            ts
            for ts in self.list_snapshots(dataset)
            if as_of is None or ts <= as_of
        ]
        if not candidates:
            return []
        target = max(candidates)
        path = self.base_dir / dataset / f"{_SNAPSHOT_PREFIX}{target.strftime(_TS_FORMAT)}.parquet"
        with duckdb.connect() as con:
            table = con.execute(
                "SELECT * FROM read_parquet(?)", [str(path)]
            ).fetch_arrow_table()
        return table.to_pylist()

    def query(self, sql: str, params: list | None = None) -> list[dict]:
        """저장소 전체를 대상으로 임의 DuckDB SQL 실행 (분석·백테스트용).

        SQL 안에서 `read_parquet('{base}/<dataset>/*.parquet')` 패턴을 직접 쓴다.
        """
        with duckdb.connect() as con:
            return con.execute(sql, params or []).fetch_arrow_table().to_pylist()
