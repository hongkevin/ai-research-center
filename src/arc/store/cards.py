"""작업 중인 리포트 = **카드**. 보드가 이걸 칸에 놓는다.

왜 필요한가
-----------
지금까지 생성물은 `JobStore`가 메모리에 **30분만** 들고 있었다(TTL). 떠나면
사라지므로 화면은 "한 번에 끝내야 하는" 모양일 수밖에 없었다 —
입력 → 대기 → 툭.

리포트가 지속되는 객체가 되면 셋이 따라온다:

* 나갔다 돌아올 수 있다 (RA의 하루는 인터럽트 구동이다)
* 여러 건이 동시에 떠 있다 (애널리스트 3~4명 보조 · 어닝시즌 다종목)
* 단계가 진행 표시가 아니라 **상태**가 된다

왜 `SnapshotStore`가 아닌가
---------------------------
그쪽은 append-only Parquet이고 point-in-time 조회용이다 — 추정 이력처럼
**변하지 않는** 기록에 맞다. 카드는 칸을 옮기고 확인 표시가 붙는 **변하는**
상태라 맞지 않는다. 그래서 JSON 파일 하나에 카드 하나를 둔다.

**워커 1개를 전제한다** (Dockerfile·jobs.py와 같은 전제). 파일 잠금을 걸지
않으므로 워커를 늘리면 마지막 쓰기가 이긴다.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("arc.store.cards")

# 칸. 순서가 곧 보드의 왼→오다.
RUNNING = "running"  # 수집됨 — 생성 중
ATTENTION = "attention"  # 확인 필요 — 카드가 실제로 쌓이는 곳
REVIEW = "review"  # 검토 대기
PUBLISHED = "published"  # 발간됨 (D27의 그 발간)
COLUMNS = (RUNNING, ATTENTION, REVIEW, PUBLISHED)

_ID_RE = re.compile(r"^[a-f0-9]{16}$")


@dataclass
class Card:
    """작업 중인 리포트 하나."""

    id: str
    symbol: str
    year: int
    # 어느 정기보고서로 만들었나. 가정을 바꿔 **다시 계산**하려면 있어야 한다.
    period: str = "ANNUAL"
    created_at: str = ""  # ISO8601 UTC
    column: str = RUNNING
    confirmed: bool = False  # 사람이 「확인함」을 눌렀는가
    company: str = ""
    attention: list[str] = field(default_factory=list)  # 확인이 필요한 이유
    error: str = ""
    vm: dict = field(default_factory=dict)  # ViewModel 전체 (본문 포함)

    # ── 문서 상태 — 나중에 다시 게이트·치환하기 위한 것 ──────────────
    # 이게 있어야 코멘트를 받아 문단을 고쳐 쓸 수 있다. 없으면 카드는
    # 읽기 전용 스냅샷이고, 리뷰 루프가 성립하지 않는다.
    assembled: str = ""  # 치환 **전** 마크다운 (플레이스홀더 살아 있음)
    registry: list[dict] = field(default_factory=list)  # NumberRegistry.dump()

    # ── 버전 ─────────────────────────────────────────────────────────
    # 버전 이력은 감사 흔적이 아니라 **콘텐츠**다 — "조정 방향과 시점은
    # 추정치 자체만큼 중요한 기록"이다 (D25).
    version: str = "v0.1"
    versions: list[dict] = field(default_factory=list)

    # 발간할 때 추정 이력으로 남길 재료 (D27). 발간은 읽고 고친 뒤에 하는
    # 일이라 카드에 있어야 하고, 그러려면 그때까지 들고 있어야 한다.
    estimate_snapshot: dict = field(default_factory=dict)
    published_path: str = ""

    def summary(self) -> dict:
        """목록용 — 본문을 뺀다. 카드 하나에 60KB가 붙어 있다."""
        return {
            "id": self.id,
            "symbol": self.symbol,
            "year": self.year,
            "period": self.period,
            "created_at": self.created_at,
            "column": self.column,
            "confirmed": self.confirmed,
            "company": self.company,
            "attention": self.attention,
            "error": self.error,
            "gate_passed": bool(self.vm.get("gate_passed")),
            "registry_size": self.vm.get("registry_size", 0),
            "stage_count": len(self.vm.get("stages") or []),
            "version": self.version,
            "revision_count": len(self.versions),
            "published_path": self.published_path,
        }


def next_version(current: str) -> str:
    """v0.1 → v0.2. 자릿수가 넘으면 v0.10이 아니라 그대로 이어간다."""
    m = re.fullmatch(r"v(\d+)\.(\d+)", current or "")
    if not m:
        return "v0.1"
    major, minor = int(m.group(1)), int(m.group(2))
    return f"v{major}.{minor + 1}"


def attention_reasons(vm: dict) -> list[str]:
    """**기계가 이미 아는 것에서만 뽑는다.** 새로 추론하지 않는다.

    `absent`(정상 부재)는 이유가 아니다 — 단일 부문 회사에 부문 손익이 없는
    것은 정상이고(D33), 그걸 확인 필요로 올리면 보드가 늘 빨갛다.
    """
    out: list[str] = []
    if vm.get("error"):
        out.append(f"생성 실패 — {vm['error']}")
        return out
    if not vm.get("gate_passed"):
        n = len(vm.get("violations") or [])
        out.append(f"G0 차단 {n}건 — 발간할 수 없습니다")
    for s in vm.get("stages") or []:
        if s.get("status") == "failed":
            out.append(f"{s.get('label')} 실패 — {s.get('note') or '사유 미상'}")
        elif s.get("status") == "partial":
            # 검산이 어긋난 것만 올린다. 「미확인 계정」 같은 커버리지 알림은
            # 카드를 멈춰 세울 일이 아니다.
            bad = [c for c in (s.get("checks") or []) if not c.get("ok")]
            if bad:
                first = bad[0]
                out.append(
                    f"{s.get('label')} 검산 불일치 — {first.get('label')} {first.get('value')}"
                )
    return out


def column_for(vm: dict, *, confirmed: bool, published: bool) -> str:
    """카드가 어느 칸에 있어야 하는가. **자동 판정한다.**

    옮기는 것이 일이 되면 아무도 안 옮기고 보드는 버려진다. 대신 사람의 판단은
    남긴다 — `confirmed`(「확인함」) 한 번으로 확인 필요를 벗어난다.

    수동으로 카드를 옮기는 것은 **열어두되 지금 만들지 않는다** (D40). 자동
    판정이 틀리는 경우가 실제로 얼마나 되는지 보고 나서 붙이는 편이 낫다.
    """
    if published:
        return PUBLISHED
    if attention_reasons(vm) and not confirmed:
        return ATTENTION
    return REVIEW


class CardStore:
    """`{base}/cards/{id}.json` 하나에 카드 하나."""

    def __init__(self, base_dir: str | Path) -> None:
        self.dir = Path(base_dir) / "cards"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, card_id: str) -> Path:
        # 경로 조작 방지 — id는 우리가 만든 16자리 hex만 받는다
        if not _ID_RE.match(card_id):
            raise ValueError(f"잘못된 카드 id: {card_id!r}")
        return self.dir / f"{card_id}.json"

    def new_id(self) -> str:
        return uuid.uuid4().hex[:16]

    def save(self, card: Card) -> None:
        tmp = self._path(card.id).with_suffix(".tmp")
        # 원자적 교체 — 쓰다 죽으면 반쪽 JSON이 남아 목록 전체가 깨진다
        tmp.write_text(json.dumps(card.__dict__, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self._path(card.id))

    def get(self, card_id: str) -> Card | None:
        # id 검증은 **try 밖에서** 한다. 안에 두면 잘못된 경로가 "없는 카드"로
        # 조용히 삼켜져 경로 검증이 무력해진다.
        path = self._path(card_id)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return Card(**raw)

    def list(self) -> list[Card]:
        """최신순. 깨진 파일 하나가 목록 전체를 막지 않게 한다."""
        cards: list[Card] = []
        for p in self.dir.glob("*.json"):
            try:
                cards.append(Card(**json.loads(p.read_text(encoding="utf-8"))))
            except (OSError, ValueError, TypeError) as exc:
                log.warning("카드를 읽지 못했습니다 (%s): %s", p.name, exc)
        cards.sort(key=lambda c: c.created_at, reverse=True)
        return cards

    def delete(self, card_id: str) -> bool:
        path = self._path(card_id)
        try:
            path.unlink()
        except OSError:
            return False
        return True


def now_iso() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
