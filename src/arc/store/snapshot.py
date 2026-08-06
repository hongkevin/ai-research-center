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
# 같은 시각 스냅샷의 조각 번호 구분자. 시각 형식에 없는 문자여야 한다.
_PART_SEP = "__"


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
        snapshot_at = snapshot_at or dt.datetime.now(dt.UTC)
        if snapshot_at.tzinfo is None:
            snapshot_at = snapshot_at.replace(tzinfo=dt.UTC)

        table = pa.Table.from_pylist(records)
        ts_array = pa.array([snapshot_at] * len(records), type=pa.timestamp("us", tz="UTC"))
        table = table.append_column("snapshot_at", ts_array)

        dataset_dir = self.base_dir / dataset
        dataset_dir.mkdir(parents=True, exist_ok=True)
        # **같은 시각에 두 번 저장될 수 있다.** 발간 스냅샷은 시각을 실행
        # 시각이 아니라 **발간일**로 찍기 때문에(point-in-time), 하루에 두
        # 종목을 발간하면 파일 이름이 같아진다. 예전에는 뒤엣것이 앞엣것을
        # 덮어써서 **먼저 발간한 종목의 이력이 통째로 사라졌다** — 실측:
        # 삼성물산을 낸 뒤 파마리서치를 내자 삼성물산 지문이 없어졌다.
        # 시각은 의미이므로 그대로 두고, 파일 이름만 겹치지 않게 한다.
        stamp = snapshot_at.strftime(_TS_FORMAT)
        path = dataset_dir / f"{_SNAPSHOT_PREFIX}{stamp}.parquet"
        n = 0
        while path.exists():
            n += 1
            path = dataset_dir / f"{_SNAPSHOT_PREFIX}{stamp}{_PART_SEP}{n}.parquet"
        pq.write_table(table, path)
        return path

    # -------------------------------------------------------------- 조회

    def list_snapshots(self, dataset: str) -> list[dt.datetime]:
        """데이터셋의 스냅샷 시각 목록 (오름차순)."""
        dataset_dir = self.base_dir / dataset
        if not dataset_dir.exists():
            return []
        seen: set[dt.datetime] = set()
        for p in sorted(dataset_dir.glob(f"{_SNAPSHOT_PREFIX}*.parquet")):
            # 같은 시각의 조각이 여럿일 수 있다 (`…-1.parquet`). 시각은 하나다.
            raw = p.stem.removeprefix(_SNAPSHOT_PREFIX).split(_PART_SEP)[0]
            seen.add(dt.datetime.strptime(raw, _TS_FORMAT).replace(tzinfo=dt.UTC))
        return sorted(seen)

    def read_as_of(self, dataset: str, as_of: dt.datetime | None = None) -> list[dict]:
        """`as_of` 시점 이하 최신 스냅샷의 레코드를 반환한다 (point-in-time 조회).

        as_of 미지정 시 최신 스냅샷. 해당 시점 이전 스냅샷이 없으면 빈 목록.
        """
        if as_of is not None and as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=dt.UTC)
        candidates = [ts for ts in self.list_snapshots(dataset) if as_of is None or ts <= as_of]
        if not candidates:
            return []
        target = max(candidates)
        # 그 시각의 **조각 전부**를 읽는다. 하나만 읽으면 같은 날 발간한 다른
        # 종목이 보이지 않는다.
        pattern = str(
            self.base_dir / dataset / f"{_SNAPSHOT_PREFIX}{target.strftime(_TS_FORMAT)}*.parquet"
        )
        with duckdb.connect() as con:
            table = con.execute(
                "SELECT * FROM read_parquet(?, union_by_name=true)", [pattern]
            ).fetch_arrow_table()
        return table.to_pylist()

    def read_history(self, dataset: str) -> list[dict]:
        """데이터셋의 **모든 스냅샷**을 합쳐 읽는다 (각 행에 snapshot_at 포함).

        `read_as_of`는 스냅샷 **하나**를 통째로 돌려준다. 한 종목만 다룰 때는
        맞지만, 여러 종목을 번갈아 발간하면 마지막 파일이 다른 종목 것이라
        직전 이력을 못 찾는다 — 종목 A를 낸 뒤 B를 내면, A의 다음 노트가
        "직전 발간 없음"이 된다. **종목별 이력에는 이쪽을 쓴다.**

        as-of 재현(백테스트)에는 여전히 `read_as_of`가 맞다. 그쪽은 "그 시점에
        알 수 있던 스냅샷 하나"를 묻는 질문이다.
        """
        dataset_dir = self.base_dir / dataset
        if not dataset_dir.exists() or not self.list_snapshots(dataset):
            return []
        pattern = str(dataset_dir / f"{_SNAPSHOT_PREFIX}*.parquet")
        with duckdb.connect() as con:
            table = con.execute(
                "SELECT * FROM read_parquet(?, union_by_name=true)", [pattern]
            ).fetch_arrow_table()
        return table.to_pylist()

    def query(self, sql: str, params: list | None = None) -> list[dict]:
        """저장소 전체를 대상으로 임의 DuckDB SQL 실행 (분석·백테스트용).

        SQL 안에서 `read_parquet('{base}/<dataset>/*.parquet')` 패턴을 직접 쓴다.
        """
        with duckdb.connect() as con:
            return con.execute(sql, params or []).fetch_arrow_table().to_pylist()
