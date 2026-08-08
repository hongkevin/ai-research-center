"""시세 자동 갱신 — **아무도 안 눌러도 돌아간다** (D87).

왜 필요한가
-----------
배포 점검에서 나온 것: **서버에 시세를 받아 올 경로가 아예 없었다.** 엔드
포인트도, 스케줄러도, 시작 시 작업도 없었다. 폴백인 `corpus/consensus/prices/`
는 gitignore라 이미지에도 없다.

즉 배포된 앱은 **시세가 0개**였고, 브리프의 등락·섹터 줄·피어 후보·발굴이 전부
빈 채로 돌았다. 화면은 「시세를 아직 못 받았습니다」라고 정직하게 말하지만,
그건 정직한 고장이지 정상이 아니다.

그리고 **최초 1회가 아니다.** 어제 종가는 오늘 아침에 받아야 한다. 손으로
돌리는 절차로 두면 어느 아침엔가 잊고, 그날 브리프는 **조용히 어제치**가 된다.

무엇을 하지 않나
----------------
**크론을 안 쓴다.** 워커가 하나고(`railway.toml`) 프로세스가 살아 있는 동안
도는 스레드면 충분하다. 별도 프로세스를 세우면 볼륨을 두 곳에서 쓰게 되고,
그게 파일 저장소에서 제일 사고 나기 쉬운 자리다.

**시작을 붙들지 않는다.** `_lifespan`의 corpCode 예열과 같은 이유다 — 헬스체크
가 기다리면 플랫폼이 배포를 실패로 본다.

**받은 날은 다시 안 받는다.** `backfill`이 이미 디스크의 날짜를 보고 건너뛴다.
그래서 하루 한 번 도는 것은 대개 **1콜**이고, 휴장일이면 0건이다.
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("arc.web.refresh")

# 얼마마다 볼까. 하루 한 번이면 충분하지만 **컨테이너가 언제 뜰지 모른다** —
# 6시간마다 보면 재배포 시각과 무관하게 장 마감(15:30 KST) 뒤 한 번은 걸린다.
EVERY_SECONDS = 6 * 60 * 60

# 처음 받을 때 며칠치. 상관 계산이 250거래일을 쓰므로(`peer_suggest.WINDOW`)
# 그보다 넉넉해야 한다.
FIRST_DAYS = 400

# 이미 채워진 뒤의 따라잡기. 며칠 쉬어도 메우되 400일을 매번 훑지 않는다.
CATCHUP_DAYS = 30

# 이만큼 있으면 「받아 둔 것이 있다」고 본다. 백필이 중간에 죽어 몇 개만 남은
# 상태를 「다 받았다」로 읽으면 그 뒤로 영영 안 채운다.
ENOUGH = 500


@dataclass
class Status:
    """마지막으로 무슨 일이 있었나. **화면이 이걸 그대로 보여준다.**"""

    running: bool = False
    started_at: str = ""
    finished_at: str = ""
    ok: bool = False
    error: str = ""
    symbols: int = 0
    market_symbols: int = 0
    fetched_days: int = 0
    # 왜 돌았나 — `startup` · `tick` · `manual`
    reason: str = ""
    history: list[str] = field(default_factory=list)

    def snapshot(self) -> dict:
        return {
            "running": self.running,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "ok": self.ok,
            "error": self.error,
            "symbols": self.symbols,
            "market_symbols": self.market_symbols,
            "fetched_days": self.fetched_days,
            "reason": self.reason,
            "history": self.history[-5:],
        }


STATUS = Status()
_LOCK = threading.Lock()


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


def run_once(base: str | Path, *, reason: str = "manual", indices: bool = True) -> dict:
    """한 번 받는다. **두 번 겹쳐 돌지 않는다.**

    같은 볼륨에 두 백필이 동시에 쓰면 `_merge`의 원자적 교체가 서로를 덮는다 —
    마지막에 쓴 쪽이 이기고 그 사이 받은 것이 사라진다.
    """
    from arc.finmodel import market_facts
    from arc.finmodel.price_store import available, backfill, backfill_indices

    if not _LOCK.acquire(blocking=False):
        return {"skipped": "이미 받는 중입니다.", **STATUS.snapshot()}
    try:
        STATUS.running = True
        STATUS.started_at = _now()
        STATUS.reason = reason
        STATUS.error = ""
        # **처음인지 따라잡기인지.** 400일을 매번 훑으면 하루 한 번이 260콜이다
        days = FIRST_DAYS if available(base) < ENOUGH else CATCHUP_DAYS
        got = backfill(base, days=days)
        STATUS.symbols = got["total_symbols"]
        STATUS.market_symbols = got["market_symbols"]
        STATUS.fetched_days = got["fetched_days"]
        if indices:
            try:
                backfill_indices(base, days=days)
            except Exception as exc:  # noqa: BLE001 — 지수가 실패해도 시세는 남는다
                log.warning("지수를 못 받았습니다: %s", exc)
        STATUS.ok = True
        listing = market_facts.load_listing(base)
        STATUS.history.append(
            f"{_now()} · {reason} · 거래일 {got['fetched_days']}일 · "
            f"종목 {got['total_symbols']} · 시장데이터 {got['market_symbols']} · "
            f"코스닥 {sum(1 for v in listing.values() if v == market_facts.KOSDAQ)}"
        )
        log.info("시세 갱신 완료 (%s): %s", reason, STATUS.history[-1])
    except Exception as exc:  # noqa: BLE001 — 갱신 실패가 앱을 죽이지 않는다
        STATUS.ok = False
        STATUS.error = f"{type(exc).__name__}: {exc}"
        STATUS.history.append(f"{_now()} · {reason} · 실패 — {STATUS.error}")
        log.warning("시세 갱신 실패 (%s): %s", reason, exc)
    finally:
        STATUS.running = False
        STATUS.finished_at = _now()
        _LOCK.release()
    return STATUS.snapshot()


def start(base: str | Path, *, every: int = EVERY_SECONDS) -> threading.Thread:
    """백그라운드 갱신 루프를 띄운다. **데몬 스레드다** — 종료를 안 막는다.

    시작하자마자 한 번 받고, 그다음은 `every`마다 본다. 첫 회를 미루면 새로
    띄운 컨테이너가 몇 시간 동안 시세 없이 도는데, 그게 배포 직후의 모습이라
    **가장 자주 보게 되는 상태**다.
    """

    def loop() -> None:
        run_once(base, reason="startup")
        while True:
            time.sleep(every)
            run_once(base, reason="tick")

    thread = threading.Thread(target=loop, daemon=True, name="arc-prices")
    thread.start()
    return thread
