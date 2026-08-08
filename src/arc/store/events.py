"""사건 로그 — **개인화의 핵심은 목록이 아니라 사건이다.**

왜 필요한가
-----------
커버 종목 목록은 「내 것」이지만 개인화가 아니다. 그건 **설정**이다. 사람이
직접 넣었고, 6개월이 지나도 넣은 그대로다.

도구가 사람을 알게 되는 것은 다른 데서 온다:

| 사건 | 무엇을 말하나 |
|---|---|
| 생성 문장을 고쳤다 | **문체·판단 선호.** 가장 값진 신호다 |
| 피어 후보를 안 넣었다 | 「이건 내 피어가 아니다」 |
| 같은 종목을 여섯 번 열었다 | 지금 집중하는 것 |
| 같은 질문을 세 번 다르게 물었다 | 답이 부족했다 |
| 리포트를 넘겼다 | 이 형태는 통과했다 |

지금까지 이 중 **하나도 안 남았다.** 안 남기면 6개월 뒤에도 「내가 넣은
목록」밖에 없다.

불변식 1을 여기서 깨면 안 된다
------------------------------
사건은 언젠가 프롬프트로 들어간다(맥락 조립). 그런데 편집한 문장에는 렌더된
숫자가 들어 있고, 그게 프롬프트에 닿으면 **LLM이 값을 보게 된다** — 이 제품의
전제가 무너지는 자리다.

그래서 텍스트를 담는 사건은 둘 중 하나여야 한다:

* `SAFE_PLACEHOLDER` — 조립본에서 온 것. `{{num:key}}` 꼴이라 **구조적으로**
  값이 없다
* `SAFE_MASKED` — 사람이 친 것·바깥에서 온 것. `mask_numbers()`로 가린다

가리지 않은 텍스트는 **애초에 안 받는다**(`record()`가 거부한다).

왜 JSONL인가
------------
한 줄 한 사건. 추가만 하므로 잠금이 필요 없고, 중간이 깨져도 그 줄만 잃는다.
그리고 **Postgres로 옮길 때 한 줄이 한 행**이라 이관이 기계적이다.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from arc.llm.number_registry import mask_numbers

log = logging.getLogger("arc.store.events")

# 텍스트가 안전한 이유. **둘 중 하나가 아니면 안 받는다.**
SAFE_PLACEHOLDER = "placeholder"  # 조립본에서 왔다 — 구조적으로 값이 없다
SAFE_MASKED = "masked"  # 가렸다
SAFE_NONE = "none"  # 텍스트가 없는 사건

# 사건 종류. **새로 만들 때 여기 적는다** — 오타로 만든 종류는 영영 안 세어진다.
OPENED = "opened"  # 카드를 열었다
ASKED = "asked"  # 질문했다
EDITED = "edited"  # 생성 문장을 고쳤다
ACCEPTED = "accepted"  # 제안을 받아들였다
PUBLISHED = "published"  # 넘겼다
COVERED = "covered"  # 커버·관심을 바꿨다
PEER_PICKED = "peer_picked"  # 피어 후보를 넣었다
PEER_SKIPPED = "peer_skipped"  # 피어 후보를 **안** 넣었다
GENERATED = "generated"  # 리포트를 만들었다

KINDS = frozenset(
    {
        OPENED,
        ASKED,
        EDITED,
        ACCEPTED,
        PUBLISHED,
        COVERED,
        PEER_PICKED,
        PEER_SKIPPED,
        GENERATED,
    }
)

# 한 파일에 담아 둘 최대 줄 수. 사람 하나가 1년에 수천 건이라 넉넉하지만,
# **끝없이 자라게 두지는 않는다** — 읽는 쪽이 언젠가 전부 읽는다.
MAX_LINES = 50_000


@dataclass
class Event:
    """한 사건. **작아야 한다** — 자주 쓰고 오래 남는다."""

    kind: str
    subject: str = ""  # 종목코드 · 카드 id · 섹터 · 채널
    text: str = ""  # 플레이스홀더거나 가려진 것만
    safe: str = SAFE_NONE
    detail: dict = field(default_factory=dict)
    at: str = ""

    def as_row(self) -> dict:
        return {
            "at": self.at,
            "kind": self.kind,
            "subject": self.subject,
            "text": self.text,
            "safe": self.safe,
            "detail": self.detail,
        }


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


class EventStore:
    """`{user_dir}/events.jsonl` 하나. **사람마다 따로다.**

    경로가 이미 사람마다 갈려 있으므로(`identity.user_dir`) 여기서 uid를 다시
    다루지 않는다 — 필터를 한 군데라도 빠뜨리면 남의 기록이 샌다.
    """

    def __init__(self, base_dir: str | Path) -> None:
        self.path = Path(base_dir) / "events.jsonl"

    # ── 쓰기 ─────────────────────────────────────────────────────────
    def record(self, event: Event) -> bool:
        """한 줄 붙인다. **실패해도 예외를 안 던진다.**

        기록은 본 일의 **부산물**이다. 로그를 못 써서 리포트 생성이 막히면
        그건 잘못된 교환이다 — 실패는 로그로 남기고 넘어간다.
        """
        if event.kind not in KINDS:
            log.warning("모르는 사건 종류라 안 적습니다: %s", event.kind)
            return False
        if event.text and event.safe not in (SAFE_PLACEHOLDER, SAFE_MASKED):
            # **가리지 않은 텍스트는 애초에 안 받는다.** 여기서 통과시키면
            # 언젠가 프롬프트에 닿고 불변식 1이 조용히 깨진다.
            log.warning("가리지 않은 텍스트라 안 적습니다 (%s)", event.kind)
            return False

        event.at = event.at or _now()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event.as_row(), ensure_ascii=False) + "\n")
        except OSError as exc:
            log.warning("사건을 못 적었습니다 (%s): %s", event.kind, exc)
            return False
        return True

    def note(self, kind: str, subject: str = "", **detail) -> bool:
        """텍스트 없는 사건 한 줄. 대부분이 이것이다."""
        return self.record(Event(kind=kind, subject=subject, detail=detail))

    def note_text(self, kind: str, text: str, *, subject: str = "", **detail) -> bool:
        """사람이 친 것·바깥에서 온 텍스트. **가려서 적는다.**"""
        return self.record(
            Event(
                kind=kind,
                subject=subject,
                text=mask_numbers(text),
                safe=SAFE_MASKED,
                detail=detail,
            )
        )

    def note_draft(self, kind: str, assembled: str, *, subject: str = "", **detail) -> bool:
        """조립본에서 온 텍스트. **가릴 필요가 없다** — 플레이스홀더뿐이다."""
        return self.record(
            Event(
                kind=kind,
                subject=subject,
                text=assembled,
                safe=SAFE_PLACEHOLDER,
                detail=detail,
            )
        )

    # ── 읽기 ─────────────────────────────────────────────────────────
    def read(self, *, limit: int = MAX_LINES, since: dt.datetime | None = None) -> list[Event]:
        """최근 것부터. **깨진 줄은 건너뛴다** — 한 줄 때문에 전부를 잃지 않는다."""
        if not self.path.is_file():
            return []
        out: list[Event] = []
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            log.warning("사건을 못 읽었습니다: %s", exc)
            return []

        cut = since.isoformat(timespec="seconds") if since else ""
        for raw in reversed(lines[-MAX_LINES:]):
            if len(out) >= limit:
                break
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            at = str(row.get("at", ""))
            if cut and at < cut:
                break  # 시간순이라 여기서 멈춰도 된다
            out.append(
                Event(
                    kind=str(row.get("kind", "")),
                    subject=str(row.get("subject", "")),
                    text=str(row.get("text", "")),
                    safe=str(row.get("safe", SAFE_NONE)),
                    detail=row.get("detail") or {},
                    at=at,
                )
            )
        return out


@dataclass
class Summary:
    """**쌓인 것이 보여야 쌓을 마음이 든다.**

    사건 로그를 눈에 안 보이는 데 두면 그게 맞게 쌓이는지 아무도 모른다.
    그리고 여기 나오는 것이 곧 나중에 맥락으로 들어갈 것들이다.
    """

    days: int = 30
    total: int = 0
    # 자주 연 종목 — **지금 집중하는 것**
    focus: list[tuple[str, int]] = field(default_factory=list)
    # 편집이 몰린 섹션 — **생성이 약한 자리**
    edited_sections: list[tuple[str, int]] = field(default_factory=list)
    # 반복해서 물은 것 — **답이 부족했다는 신호**
    repeated: list[tuple[str, int]] = field(default_factory=list)
    # 제안했는데 안 넣은 피어 — **「이건 내 피어가 아니다」**
    skipped_peers: list[tuple[str, int]] = field(default_factory=list)
    by_kind: dict[str, int] = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return self.total == 0


def summarize(events: list[Event], *, days: int = 30, top: int = 5) -> Summary:
    """사건 → 사람. **순수 함수다.**

    세는 것만 한다. 「그래서 무엇을 하라」는 여기 없다 — 그건 판단이고, 판단은
    화면이 아니라 사람이 한다.
    """
    out = Summary(days=days, total=len(events))
    if not events:
        return out

    out.by_kind = dict(Counter(e.kind for e in events))

    opened = Counter(e.subject for e in events if e.kind == OPENED and e.subject)
    out.focus = opened.most_common(top)

    sections = Counter(str(e.detail.get("section", "")) for e in events if e.kind == EDITED)
    sections.pop("", None)
    out.edited_sections = sections.most_common(top)

    # 같은 종목을 여러 번 물었나. **질문 문장이 아니라 대상으로 센다** —
    # 문장은 매번 다르지만 「또 이걸 묻고 있다」가 신호다.
    asked = Counter(e.subject for e in events if e.kind == ASKED and e.subject)
    out.repeated = [(s, n) for s, n in asked.most_common(top) if n >= 2]

    skipped = Counter(e.subject for e in events if e.kind == PEER_SKIPPED and e.subject)
    out.skipped_peers = skipped.most_common(top)
    return out
