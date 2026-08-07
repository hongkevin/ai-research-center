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

# 칸. 순서가 곧 보드의 왼→오다 (D51).
#
# 넷이었다가 셋으로 줄였다:
#
# * `수집됨`은 칸이 아니었다 — 1.5초 머무는 곳은 칸이 아니라 **카드 위
#   스피너**다. 칸반의 칸은 일이 *머무는* 곳이어야 한다.
# * `확인 필요`도 칸이 아니라 **속성**이었다. 검토 중인 카드가 확인이 필요할
#   수도 아닐 수도 있는데, 다른 칸으로 두면 「검토는 하는데 게이트가 막힌
#   카드」가 갈 곳이 없다. 배지로 내린다.
#
# **종착점이 「발간」이 아닌 이유**: 조사분석자료는 공표 전 심의가 법정
# 절차이고(금투협 IR·조사분석 업무처리강령), 해외 RMS도 애널리스트 초안 →
# 어소시에이트 → Supervisory Analyst → 컴플라이언스로 간다. **RA는 발간
# 권한이 없다.** RA의 종착점은 「넘김」이다.
DRAFT = "draft"  # 초안 — 기계가 만들어 놨고 아직 사람이 안 봤다
REVIEW = "review"  # 검토 중 — 사람이 열어서 읽고 고치는 중
HANDOFF = "handoff"  # 넘김 — 확정해서 내보냈다 (D27의 그 발간)
COLUMNS = (DRAFT, REVIEW, HANDOFF)

# 예전 이름. 저장된 카드가 이 값을 들고 있어서 읽을 때 옮겨 준다.
_LEGACY_COLUMN = {"running": DRAFT, "attention": REVIEW, "published": HANDOFF}
RUNNING = "running"  # 생성 중 — 칸이 아니라 카드의 상태다
PUBLISHED = HANDOFF  # 옛 이름 (import 호환)

_ID_RE = re.compile(r"^[a-f0-9]{16}$")

# 카드의 종류. 보드에는 둘이 나란히 선다.
#
# **왜 새 칸이 아니라 새 종류인가**: 피어 뷰는 진행 단계가 아니다. 초안이고,
# 검토하고, 넘긴다 — 종목 카드와 **똑같은 수명**을 산다. 칸을 늘리면 그
# 수명을 두 번 구현하게 되고, 버전·직전 대비 변화·내보내기가 전부 갈라진다.
#
# 인터뷰가 꺼낸 고통이 *"커버 밖 종목"*이었다. 그건 종목 카드 안의 탭으로는
# 안 된다 — 「방산 섹터」가 종목 하나에 종속돼 버린다.
SINGLE = "single"  # 종목 하나
PEER = "peer"  # 여러 종목을 한 표로
KINDS = (SINGLE, PEER)


def peer_member(
    symbol: str,
    *,
    company: str = "",
    card_id: str = "",
    year: int = 0,
    period: str = "ANNUAL",
    status: str = "pending",
    error: str = "",
) -> dict:
    """피어 카드의 구성원 한 줄.

    **`card_id`가 핵심이다.** 피어 카드는 숫자를 자기가 만들지 않고 종목
    카드를 **가리킨다**. 그래야 표의 모든 칸이 이미 게이트를 통과한 수치이고
    (불변식 1), 클릭하면 원문 절까지 되짚힌다(D44). 피어 뷰가 자기 파이프라인을
    따로 가지면 그 둘을 다시 만들어야 하고, 그 순간 검증 안 된 표가 된다.

    커버 밖 종목이라 카드가 아직 없으면 `card_id`가 비고 `status`가
    `pending`이다 — 그때 종목 카드를 만들어 채운다.
    """
    return {
        "symbol": symbol,
        "company": company,
        "card_id": card_id,
        "year": year,
        "period": period,
        "status": status,  # pending | running | ready | failed
        "error": error,
    }


@dataclass
class Card:
    """작업 중인 리포트 하나."""

    id: str
    symbol: str
    year: int
    # 어느 정기보고서로 만들었나. 가정을 바꿔 **다시 계산**하려면 있어야 한다.
    period: str = "ANNUAL"
    created_at: str = ""  # ISO8601 UTC
    column: str = DRAFT
    # **생성 중은 칸이 아니라 상태다** (D51). 1.5초 머무는 곳은 칸이 아니다.
    running: bool = False
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
    # 발간할 때 남길 노트 지문 (D46) — 다음 노트가 「직전 대비 무엇이
    # 달라졌는가」를 이 줄들과 비교해서 만든다.
    note_facts: list[dict] = field(default_factory=list)
    # 사용자가 올린 직전 노트 (D48). 원문 마크다운 + 읽어낸 것.
    # **여기 숫자는 본문에 안 들어간다** — 비교 패널과 구성 힌트에만 쓴다.
    prior_note: dict = field(default_factory=dict)
    published_path: str = ""

    # ── 피어 카드 ────────────────────────────────────────────────────
    # 종목 카드는 `kind`가 없던 시절에 만들어졌다. 기본값이 `SINGLE`이라
    # 옛 카드는 읽는 순간 종목 카드가 된다 — 이관이 필요 없다.
    kind: str = SINGLE
    members: list[dict] = field(default_factory=list)  # peer_member() 목록

    def attention_now(self) -> list[str]:
        """이 카드에서 사람이 봐야 하는 것. 종류에 따라 **보는 곳이 다르다.**

        종목 카드는 `vm`의 단계 진단을 읽고, 피어 카드는 구성원을 읽는다.
        (`attention` 필드는 저장된 옛 값이라 이름을 못 쓴다.)
        """
        if self.kind == PEER:
            return peer_attention_reasons(self.members)
        return attention_reasons(self.vm) if self.vm else self.attention

    def summary(self) -> dict:
        """목록용 — 본문을 뺀다. 카드 하나에 60KB가 붙어 있다."""
        return {
            "id": self.id,
            "kind": self.kind,
            "symbol": self.symbol,
            "year": self.year,
            "period": self.period,
            "created_at": self.created_at,
            "column": self.column,
            "running": self.running,
            "confirmed": self.confirmed,
            "company": self.company,
            # **저장된 문구를 쓰지 않고 지금 다시 계산한다.** 생성 시점에
            # 굳혀 두면 문구를 고쳐도 이미 만든 카드에는 영영 옛말이 남는다
            # (실측으로 밟았다 — D51에서 문구를 고쳤는데 보드가 안 바뀌었다).
            "attention": self.attention_now(),
            "error": self.error,
            "gate_passed": bool(self.vm.get("gate_passed")),
            "registry_size": self.vm.get("registry_size", 0),
            "stage_count": len(self.vm.get("stages") or []),
            "version": self.version,
            "revision_count": len(self.versions),
            "published_path": self.published_path,
            # 보드가 피어 카드를 다르게 그리려면 본문 없이도 알아야 한다.
            "member_count": len(self.members),
            "member_symbols": [m.get("symbol", "") for m in self.members],
        }


def next_version(current: str) -> str:
    """v0.1 → v0.2. 자릿수가 넘으면 v0.10이 아니라 그대로 이어간다."""
    m = re.fullmatch(r"v(\d+)\.(\d+)", current or "")
    if not m:
        return "v0.1"
    major, minor = int(m.group(1)), int(m.group(2))
    return f"v{major}.{minor + 1}"


# 검산 항목 → **그래서 뭘 하면 되는가**. 이게 없으면 「불일치」만 보고 끝난다.
_WHAT_TO_DO = {
    "부문별 매출": "사업보고서 부문 표를 확인하십시오. 내부거래가 섞이면 합계가 어긋납니다.",
    "부문별 손익": "영업부문 주석의 총계 열을 확인하십시오.",
    "주식수·배당·지분 등": "발행주식·자기주식 공시를 확인하십시오.",
    "재무제표": "연결/별도 기준이 섞였는지 확인하십시오.",
}

# 화면에 올리지 않는 단계. 우리 내부 QA이지 RA가 할 일이 아니다.
_INTERNAL_ONLY = ("관점 분석", "출처 링크", "사업 이해")


def attention_reasons(vm: dict) -> list[str]:
    """이 카드에서 **사람이 봐야 하는 것**. 없으면 빈 목록.

    옛 문구는 이랬다:

        G0 차단 1건 — 발간할 수 없습니다
        부문별 매출 검산 불일치 — 부문 합계 vs 손익계산서 매출액 -31.8011%
        발간 전 점검 실패 — 차단 1건 — 발간할 수 없습니다.
        관점 분석 검산 불일치 — 집중 부문 매출과 지분 구조를 확인하지 못해
                             무엇에 기대고 있는지 가리지 못했다.

    문제가 넷이었다. `G0`는 내부 코드명이고, 1번과 3번은 **같은 사건을 두 번**
    말하고, 소수점 넷째 자리는 사람이 읽는 숫자가 아니고, 마지막 줄은 시스템이
    자기 사정을 문학적으로 말한다. 그리고 전부 **「그래서 뭘 해야 하나」가 없다.**

    **`absent`(정상 부재)는 이유가 아니다** — 단일 부문 회사에 부문 손익이 없는
    것은 정상이고(D33), 그걸 올리면 보드가 늘 빨갛다.
    """
    if vm.get("error"):
        return [f"생성이 중단됐습니다 — {vm['error']}"]

    out: list[str] = []
    if not vm.get("gate_passed"):
        n = len(vm.get("violations") or [])
        out.append(
            f"내보낼 수 없습니다 — 본문에 출처 없는 숫자가 {n}건 있습니다"
            if n
            else "내보낼 수 없습니다 — 발간 전 점검을 통과하지 못했습니다"
        )

    for s in vm.get("stages") or []:
        label = str(s.get("label") or "")
        # 게이트는 위에서 이미 한 번 말했다. 두 번 쓰지 않는다.
        if label.startswith("발간 전 점검") or label in _INTERNAL_ONLY:
            continue
        if s.get("status") == "failed":
            note = str(s.get("note") or "").split(" — ")[0]
            out.append(f"{label}을(를) 가져오지 못했습니다{f' — {note}' if note else ''}")
        elif s.get("status") == "partial":
            bad = [c for c in (s.get("checks") or []) if not c.get("ok")]
            if not bad:
                continue
            gap = _readable_gap(str(bad[0].get("value") or ""))
            todo = _WHAT_TO_DO.get(label, "")
            head = f"{label} 합계가 {gap} 어긋납니다" if gap else f"{label} 검산이 맞지 않습니다"
            out.append(f"{head}{f' — {todo}' if todo else ''}")
    return out


_PERIOD_LABEL = {
    "ANNUAL": "연간",
    "HALF": "반기",
    "Q1": "1분기",
    "Q3": "3분기",
}


def peer_attention_reasons(members: list[dict]) -> list[str]:
    """피어 카드에서 사람이 봐야 하는 것.

    **가장 중요한 것이 마지막 항목이다 — 기준 기간이 섞이면 표가 거짓말을
    한다.** 한 종목은 2025 연간이고 다른 종목은 2025 3분기 누적인데 매출이
    나란히 서면, 화면상 아무 이상이 없으면서 값이 4:3으로 어긋난다. 종목
    카드에는 없던 종류의 결함이다 — 카드 하나짜리에서는 기간이 섞일 수가
    없었다.

    검산 불일치를 자동으로 고치지 않고 **표시만 한다**는 점은 종목 카드와
    같다(D51). 피어 그룹을 다시 짤지 기간을 맞출지는 사람이 정한다.
    """
    out: list[str] = []

    failed = [m for m in members if m.get("status") == "failed"]
    for m in failed:
        name = m.get("company") or m.get("symbol") or "종목"
        note = str(m.get("error") or "").split(" — ")[0]
        out.append(f"{name}을(를) 가져오지 못했습니다{f' — {note}' if note else ''}")

    pending = [m for m in members if m.get("status") in ("pending", "running")]
    if pending:
        out.append(f"{len(pending)}종목이 아직 준비되지 않았습니다 — 비교표가 비어 있습니다")

    # 기간 정합성은 **준비된 구성원끼리만** 본다. 아직 안 만들어진 것의
    # 기간은 아직 정해지지 않은 것이라 섞였다고 말할 수 없다.
    ready = [m for m in members if m.get("status") == "ready"]
    basis = {(m.get("year"), m.get("period")) for m in ready}
    if len(basis) > 1:
        shown = ", ".join(
            sorted(f"{y}년 {_PERIOD_LABEL.get(str(p), p)}" for y, p in basis if y)
        )
        out.append(f"기준 기간이 섞여 있어 나란히 비교할 수 없습니다 — {shown}")

    blocked = sum(1 for m in ready if m.get("gate_passed") is False)
    if blocked:
        out.append(f"{blocked}종목이 내보낼 수 없는 상태입니다 — 출처 없는 숫자가 있습니다")
    return out


def _readable_gap(value: str) -> str:
    """`-31.8011%` → `32%`. **소수점 넷째 자리는 사람이 읽는 숫자가 아니다.**"""
    m = re.search(r"-?\d+(?:\.\d+)?", value)
    if m is None:
        return ""
    n = abs(float(m.group()))
    return f"{n:.0f}%" if "%" in value else f"{n:,.0f}"


def column_for(vm: dict, *, confirmed: bool, published: bool) -> str:
    """카드가 어느 칸에 있어야 하는가.

    **사람이 옮긴다.** 예전에는 게이트 통과 여부로 자동 판정했는데, 그러면
    칸이 「기계의 상태」를 말하지 「내가 어디까지 봤는가」를 말하지 않는다.
    어닝시즌에 여덟 종목이 굴러갈 때 RA가 알고 싶은 것은 후자다
    (`research/06-ra-workflow.md`: 애널리스트 3~4명 보조 · 여러 종목 동시).

    확인이 필요한지는 칸이 아니라 **배지**로 낸다 — `attention_reasons()`.
    """
    if published:
        return HANDOFF
    return REVIEW if confirmed else DRAFT


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
        return _migrate(Card(**raw))

    def list(self) -> list[Card]:
        """최신순. 깨진 파일 하나가 목록 전체를 막지 않게 한다."""
        cards: list[Card] = []
        for p in self.dir.glob("*.json"):
            try:
                cards.append(_migrate(Card(**json.loads(p.read_text(encoding="utf-8")))))
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


def _migrate(card: Card) -> Card:
    """예전 칸 이름을 지금 것으로. **저장된 카드를 고쳐 쓰지 않는다** — 읽을 때만."""
    if card.column in _LEGACY_COLUMN:
        # 예전 `running` 칸에 있던 것은 생성이 끝났거나 중단된 것이다.
        card.running = False
        card.column = _LEGACY_COLUMN[card.column]
    return card


def now_iso() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
