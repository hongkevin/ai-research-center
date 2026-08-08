"""대화 저장소 — **리퀘스트 이력이 브라우저에만 있으면 맥락이 될 수 없다.**

왜 옮기나
---------
리서치 채팅의 세션은 `localStorage`("arc.ask.sessions.v1")에 있었다. 그때는
그게 맞는 판단이었다 — *"서버에 두면 사용자 축·정리 정책·용량이 따라오는데,
리퀘스트 이력을 장기기억으로 쌓는 것은 별도 결정"*(ask-widget.tsx). 그 별도
결정이 지금이다. 사용자 축은 [identity.py](../web/identity.py)가, 저장소는
[pg.py](pg.py)가 이미 있다.

브라우저에 두면 셋을 못 한다:

1. 브라우저를 지우면 **사라진다.** 클라이언트 리퀘스트가 하루 10~15건인데
   그게 사람의 자산이 아니라 캐시가 된다
2. 기기 간 동기화가 없다 — 사무실에서 물은 것을 집에서 못 본다
3. **나중에 맥락으로 못 쓴다.** 이게 제일 크다. [D77](../../../docs/decisions.md#d77)이
   *"같은 질문을 세 번 다르게 물었다 = 답이 부족했다"*를 사건으로 세기 시작했는데,
   정작 **무엇을 물었고 무엇을 답했는지**는 어디에도 없었다

불변식 1을 여기서 깨면 안 된다
------------------------------
대화 답변에는 **렌더된 숫자**가 들어 있다. 그대로 저장해 두면 언젠가 맥락
조립을 타고 프롬프트에 닿고, 그 순간 LLM이 값을 본다 — 이 제품의 전제가
무너지는 자리다. [events.py](events.py)가 이미 푼 문제라 **같은 규칙을 그대로
받는다**:

* `SAFE_PLACEHOLDER` — 조립본에서 온 것. `{{num:key}}` 꼴이라 **구조적으로**
  값이 없다. 답변은 되도록 이 형태로 남긴다
* `SAFE_MASKED` — 사람이 친 것·조립본이 아닌 것. `mask_numbers()`로 가린다.
  **질문은 언제나 이쪽이다**

가리지 않은 텍스트는 `add_turn()`이 **애초에 안 받는다.**

그러면 화면은 무엇을 그리나
---------------------------
플레이스홀더만 남기면 다시 열었을 때 "매출은 {{num:c1.rev}}이다"가 보인다.
그래서 값을 **자유 텍스트가 아니라 키 붙은 표**(`Turn.numbers`)에 따로 둔다.
Number Registry가 하는 일과 같은 모양이다 — 본문에는 키만, 값은 옆에.

이 구분이 불변식을 **구조적으로** 지킨다: 프롬프트에 들어가는 것은 텍스트뿐이고
그 텍스트에는 값이 없다. 표는 화면이 그릴 때만 `Turn.rendered()`가 끼운다.

Postgres로 갈 때
----------------
`DATABASE_URL`이 있으면 `PgChatStore`가 같은 자리를 대신한다. **두 저장소가
같은 인터페이스를 갖는다** — 부르는 쪽은 어느 쪽인지 모른다.

세션과 턴을 두 표로 나눈 것은 프로필(문서 하나)과 다른 판단이다. 대화는
**뒤에 붙기만 하는 목록**이라 턴 하나가 행 하나로 떨어지고, 목록 화면이
본문 없이 제목과 턴 수만 필요로 한다. 문서 하나로 두면 턴을 붙일 때마다
대화 전체를 읽고 다시 쓴다.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from arc.llm.number_registry import mask_numbers, render_with
from arc.store.events import SAFE_MASKED, SAFE_NONE, SAFE_PLACEHOLDER

log = logging.getLogger("arc.store.chats")

# 목록에 낼 최대 대화 수. **자동으로 지우지는 않는다** — 지우는 것은 사람이
# 한다. 여기 상한은 목록이 끝없이 길어지는 것을 막는 것뿐이다.
MAX_SESSIONS = 200

# 대화 하나의 최대 턴 수. 이걸 넘기면 새 대화를 시작하는 편이 맞다 —
# 「세션 하나 = 리퀘스트 하나」가 이 채팅의 전제고(ask-widget.tsx), 한 리퀘스트가
# 200턴이면 그건 이미 다른 리퀘스트가 섞인 것이다.
MAX_TURNS = 200

# 제목은 첫 질문에서 딴다. 목록에서 리퀘스트를 알아보는 유일한 단서다.
TITLE_LEN = 40

# id는 우리가 만든 16자리 hex만 받는다 — 파일 경로가 되므로 경로 조작을 막는다.
# 카드와 같은 규칙이다(cards.py).
_ID_RE = re.compile(r"^[a-f0-9]{16}$")


def _now() -> str:
    """**초로 끊지 않는다.** 사건 로그(`events.py`)와 다른 점이다.

    목록이 「최근 갱신 순」인데 초 단위로 끊으면 같은 초에 만든 대화 둘이
    동률이 되고, 그때 순서는 디렉터리 나열 순서가 정한다 — 실제로 테스트가
    한 번 그렇게 흔들렸다. Postgres의 `now()`는 마이크로초라 두 저장소가
    **다르게 정렬되는** 상태이기도 했다.
    """
    return dt.datetime.now(dt.UTC).isoformat()


def new_id() -> str:
    return uuid.uuid4().hex[:16]


def check_id(session_id: str) -> str:
    """id 검증. **Postgres 판도 같이 쓴다.**

    파일 판에서는 경로 조작을 막는 장치지만, Postgres 판에는 경로가 없어서
    빼 둘 뻔했다. 그러면 같은 요청이 한쪽에서는 400, 다른 쪽에서는 404가 되고
    「두 저장소가 같은 인터페이스」가 조용히 거짓이 된다.
    """
    if not _ID_RE.match(session_id or ""):
        raise ValueError(f"잘못된 대화 id: {session_id!r}")
    return session_id


@dataclass
class Turn:
    """질문 하나와 그 답 하나. **텍스트에는 값이 없다.**"""

    # 사람이 친 것이라 언제나 가린다
    question: str = ""
    # 플레이스홀더(`{{num:key}}`)거나 가린 것. 그 외는 저장되지 않는다
    answer: str = ""
    safe: str = SAFE_NONE
    # `{{num:key}}` → 화면에 보일 문자열. **본문이 아니라 표다** — 프롬프트로
    # 조립되는 것은 본문뿐이라 여기 값이 있어도 LLM에 닿지 않는다.
    numbers: dict[str, str] = field(default_factory=dict)
    at: str = ""

    def as_row(self) -> dict:
        return {
            "at": self.at,
            "question": self.question,
            "answer": self.answer,
            "safe": self.safe,
            "numbers": self.numbers,
        }

    def rendered(self) -> str:
        """화면용 본문. **저장된 것은 그대로 두고 여기서만 값을 끼운다.**

        조사 교정까지 같이 일어난다(`render_with`) — 안 하면 다시 연 대화에서만
        "40.0%으로"가 된다.
        """
        return render_with(self.answer, self.numbers) if self.numbers else self.answer


@dataclass
class Session:
    """대화 하나. **세션 하나 = 리퀘스트 하나**가 이 채팅의 전제다.

    인터뷰에서 나온 하루 10~15건이 서로 다른 클라이언트의 서로 다른 질문이라,
    한 줄로 이어 붙이면 맥락이 섞인다.
    """

    id: str = ""
    title: str = ""
    turns: list[Turn] = field(default_factory=list)
    # 다음 질문의 앵커. `chat.retrieval.Context`의 셋을 편 것이다 — 누구
    # 얘기였나 · 무슨 얘기였나 · 어느 해였나. **숫자 값이 아니다.**
    symbols: list[str] = field(default_factory=list)
    tokens: list[str] = field(default_factory=list)
    year: int | None = None
    # 목록이 본문 없이 알아야 하는 것. 목록에서는 `turns`가 비어 있다.
    turn_count: int = 0
    created_at: str = ""
    updated_at: str = ""

    def as_row(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "turns": [t.as_row() for t in self.turns],
            "symbols": self.symbols,
            "tokens": self.tokens,
            "year": self.year,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def summary(self) -> dict:
        """목록 한 줄. **본문은 빼고 준다** — `/api/cards`와 같은 판단이다."""
        return {
            "id": self.id,
            "title": self.title,
            "turn_count": self.turn_count or len(self.turns),
            "symbols": self.symbols,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def build_turn(
    question: str,
    *,
    template: str = "",
    text: str = "",
    numbers: dict[str, str] | None = None,
) -> Turn:
    """답변 하나를 **저장 가능한 모양으로** 만든다.

    질문은 언제나 가린다 — 사람이 친 것이라 무엇이 들었는지 모른다.

    답변은 둘로 갈린다:

    * `template`이 있으면 그것을 쓴다. 치환 **전**의 본문이라 `{{num:key}}`만
      들어 있고 값이 구조적으로 없다 (`SAFE_PLACEHOLDER`)
    * 없으면 `text`를 가린다 (`SAFE_MASKED`). 거부·근거 없음·호출 실패처럼
      **조립본이 아닌 문장**이 여기로 온다. 그때는 애초에 숫자가 없지만,
      「없을 것이다」에 기대지 않고 가린다
    """
    answer = (template or "").strip()
    if answer:
        safe = SAFE_PLACEHOLDER
    else:
        answer = mask_numbers((text or "").strip())
        safe = SAFE_MASKED if answer else SAFE_NONE
    return Turn(
        question=mask_numbers((question or "").strip()),
        answer=answer,
        safe=safe,
        numbers=dict(numbers or {}),
        at=_now(),
    )


def _refuses(turn: Turn) -> bool:
    """저장을 거부해야 하나. **가리지 않은 텍스트는 애초에 안 받는다.**

    여기서 통과시키면 언젠가 프롬프트에 닿고 불변식 1이 조용히 깨진다.
    `events.record()`와 같은 검사다.
    """
    if turn.answer and turn.safe not in (SAFE_PLACEHOLDER, SAFE_MASKED):
        log.warning("가리지 않은 답변이라 안 적습니다 (safe=%s)", turn.safe)
        return True
    return False


def _title_of(question: str) -> str:
    """첫 질문이 대화의 이름이 된다. **따로 짓게 하지 않는다.**"""
    return (question or "").strip()[:TITLE_LEN] or "새 질문"


def _clean_turn(raw: dict) -> Turn:
    """저장된 턴 한 줄 → `Turn`. **모르는 필드는 버린다** (D65)."""
    numbers = raw.get("numbers")
    return Turn(
        question=str(raw.get("question", "")),
        answer=str(raw.get("answer", "")),
        safe=str(raw.get("safe", SAFE_NONE)),
        numbers={str(k): str(v) for k, v in numbers.items()} if isinstance(numbers, dict) else {},
        at=str(raw.get("at", "")),
    )


class ChatStore:
    """`{user_dir}/chats/{session_id}.json` 하나에 대화 하나.

    경로가 이미 사람마다 갈려 있으므로(`identity.user_dir`) 여기서 uid를 다시
    다루지 않는다 — 필터를 한 군데라도 빠뜨리면 남의 대화가 샌다.

    **파일 하나에 대화 하나**인 이유는 카드와 같다: 턴을 붙일 때마다 전체를
    다시 쓰지만, 대화 하나는 크지 않고 대화 여럿이 서로를 막지 않는다.
    """

    def __init__(self, base_dir: str | Path) -> None:
        self.dir = Path(base_dir) / "chats"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        return self.dir / f"{check_id(session_id)}.json"

    # ── 쓰기 ─────────────────────────────────────────────────────────
    def create(self, title: str = "") -> Session:
        """새 대화. **실패하면 예외가 올라간다.**

        사람이 「새 질문」을 누른 결과라 조용히 실패하면 안 된다 — 사건 로그와
        다른 점이고, `ProfileStore.save()`와 같은 판단이다.
        """
        session = Session(
            id=new_id(),
            title=title.strip()[:TITLE_LEN],
            created_at=_now(),
            updated_at=_now(),
        )
        self._write(session)
        return session

    def _write(self, session: Session) -> None:
        path = self._path(session.id)
        tmp = path.with_suffix(".tmp")
        # 원자적 교체 — 쓰다 죽으면 반쪽 JSON이 남아 대화가 통째로 사라진다
        tmp.write_text(json.dumps(session.as_row(), ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    def add_turn(
        self,
        session_id: str,
        turn: Turn,
        *,
        symbols: list[str] | tuple[str, ...] = (),
        tokens: list[str] | tuple[str, ...] = (),
        year: int | None = None,
    ) -> bool:
        """턴 하나를 붙인다. **실패해도 예외를 안 던진다.**

        답은 이미 화면에 갔다. 기록을 못 했다고 500을 내면 사용자는 답을 받고도
        오류를 보는데, 그건 잘못된 교환이다 — `EventStore.record()`와 같은 약속이다.
        """
        if _refuses(turn):
            return False
        # **여기서는 잘못된 id도 삼킨다.** 턴 기록은 답변의 부산물이라
        # 예외가 올라가면 이미 만든 답을 못 돌려준다.
        try:
            session = self.get(session_id)
        except ValueError as exc:
            log.warning("대화를 못 찾았습니다: %s", exc)
            return False
        if session is None:
            log.warning("없는 대화라 턴을 안 적었습니다: %s", session_id)
            return False
        if len(session.turns) >= MAX_TURNS:
            log.warning("대화가 %d턴을 넘어 더 안 적습니다: %s", MAX_TURNS, session_id)
            return False

        turn.at = turn.at or _now()
        if not session.turns and not session.title:
            session.title = _title_of(turn.question)
        session.turns.append(turn)
        session.symbols = [str(s) for s in symbols]
        session.tokens = [str(t) for t in tokens]
        session.year = year
        session.updated_at = _now()
        try:
            self._write(session)
        except OSError as exc:
            log.warning("턴을 못 적었습니다 (%s): %s", session_id, exc)
            return False
        return True

    def delete(self, session_id: str) -> bool:
        """없으면 False. **id 검증은 try 밖에서 한다** — `get()`과 같은 이유다."""
        path = self._path(session_id)
        try:
            path.unlink()
        except OSError:
            return False
        return True

    # ── 읽기 ─────────────────────────────────────────────────────────
    def get(self, session_id: str) -> Session | None:
        """없으면 None. **id 검증은 try 밖에서 한다** — 안에 두면 잘못된 경로가
        「없는 대화」로 조용히 삼켜져 경로 검증이 무력해진다 (cards.py와 같다)."""
        path = self._path(session_id)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return _session_of(raw)

    def list(self, limit: int = MAX_SESSIONS) -> list[Session]:
        """최근 갱신 순. **본문은 빼고 준다.**

        깨진 파일 하나가 목록 전체를 막지 않는다 — 카드와 같은 규칙이다.
        """
        out: list[Session] = []
        for p in self.dir.glob("*.json"):
            try:
                session = _session_of(json.loads(p.read_text(encoding="utf-8")))
            except (OSError, ValueError, TypeError) as exc:
                log.warning("대화를 읽지 못했습니다 (%s): %s", p.name, exc)
                continue
            session.turn_count = len(session.turns)
            session.turns = []
            out.append(session)
        out.sort(key=lambda s: s.updated_at or s.created_at, reverse=True)
        return out[: max(1, limit)]


def _session_of(raw: dict) -> Session:
    """저장된 대화 → `Session`. **모르는 필드는 버린다** (D65).

    필드를 바꾸기 **전에** 저장된 것이 반드시 있다. 카드와 프로필에서 이미
    세 번 밟은 자리라 여기서는 처음부터 아는 것만 취한다.
    """
    turns = [_clean_turn(t) for t in (raw.get("turns") or []) if isinstance(t, dict)]
    year = raw.get("year")
    return Session(
        id=str(raw.get("id", "")),
        title=str(raw.get("title", "")),
        turns=turns,
        symbols=[str(s) for s in (raw.get("symbols") or [])],
        tokens=[str(t) for t in (raw.get("tokens") or [])],
        year=year if isinstance(year, int) else None,
        turn_count=len(turns),
        created_at=str(raw.get("created_at", "")),
        updated_at=str(raw.get("updated_at", "")),
    )


class PgChatStore:
    """Postgres 판. **`ChatStore`와 같은 인터페이스다.**

    uid를 **만들 때 한 번** 받고 모든 질의에 그걸 쓴다 — uid를 인자로 받는
    메서드가 없다. 파일 저장소가 디렉터리를 한 번 받는 것과 같은 모양이고,
    그래서 「거르는 것을 빠뜨릴 수」가 없다. RLS가 그 위에 한 겹 더 있다.
    """

    def __init__(self, uid: str) -> None:
        self.uid = uid

    # ── 쓰기 ─────────────────────────────────────────────────────────
    def create(self, title: str = "") -> Session:
        """**실패하면 예외가 올라간다.** 파일 판과 같은 약속이다.

        `conn.commit()`을 부르지 않는다 — `pg.connect()`가 블록을 정상적으로
        빠져나갈 때 커밋하고, **중간에 커밋하면 `SET LOCAL`이 사라져 RLS를
        우회한다.** 다른 저장소들도 같은 이유로 명시적 커밋이 없다.
        """
        from arc.store import pg

        session = Session(
            id=new_id(),
            title=title.strip()[:TITLE_LEN],
            created_at=_now(),
            updated_at=_now(),
        )
        try:
            with pg.connect(self.uid) as conn:
                conn.execute(
                    "insert into arc_chat_sessions (id, uid, title) values (%s, %s, %s)",
                    (session.id, self.uid, session.title),
                )
        except Exception as exc:
            # **여기는 삼키면 안 된다.** 사람이 「새 질문」을 눌렀는데 조용히
            # 실패하면 다음 턴이 갈 곳이 없다.
            log.error("대화를 못 만들었습니다: %s", exc)
            raise
        return session

    def add_turn(
        self,
        session_id: str,
        turn: Turn,
        *,
        symbols: list[str] | tuple[str, ...] = (),
        tokens: list[str] | tuple[str, ...] = (),
        year: int | None = None,
    ) -> bool:
        """**실패해도 예외를 안 던진다.** 파일 판과 같은 약속이다.

        **거절을 먼저 다 판단한 뒤에 쓴다.** 쓰다 말고 `return`하면
        `pg.connect()`가 정상 종료로 보고 반쪽 상태를 커밋한다 — 「턴은 안
        들어갔는데 갱신 시각만 올라간 대화」가 그것이다.
        """
        if _refuses(turn):
            return False

        from arc.store import pg

        turn.at = turn.at or _now()
        try:
            check_id(session_id)
            with pg.connect(self.uid) as conn, conn.cursor() as cur:
                # **왕복 한 번에 둘을 묻는다** — 대화가 있는가, 몇 턴인가.
                # 행이 없으면 없는 대화고, 있으면 그 값이 턴 수다. 지금
                # 리전에서 왕복 하나가 130ms라 나눠 묻는 것이 비싸다.
                cur.execute(
                    "select count(t.id) from arc_chat_sessions s"
                    " left join arc_chat_turns t on t.uid = s.uid and t.session_id = s.id"
                    " where s.uid = %s and s.id = %s"
                    " group by s.uid, s.id",
                    (self.uid, session_id),
                )
                row = cur.fetchone()
                if row is None:
                    log.warning("없는 대화라 턴을 안 적었습니다: %s", session_id)
                    return False
                if row[0] >= MAX_TURNS:
                    log.warning("대화가 %d턴을 넘어 더 안 적습니다: %s", MAX_TURNS, session_id)
                    return False

                # 제목·앵커를 세운다. **첫 턴이면 제목을 딴다** — 파일 판과
                # 같은 규칙이고, 조건을 SQL에 두면 왕복이 한 번 준다.
                cur.execute(
                    "update arc_chat_sessions"
                    " set title = case when title = '' then %s else title end,"
                    "     symbols = %s, tokens = %s, year = %s, updated_at = now()"
                    " where uid = %s and id = %s",
                    (
                        _title_of(turn.question),
                        json.dumps(list(symbols), ensure_ascii=False),
                        json.dumps(list(tokens), ensure_ascii=False),
                        year,
                        self.uid,
                        session_id,
                    ),
                )
                cur.execute(
                    "insert into arc_chat_turns"
                    " (session_id, uid, at, question, answer, safe, numbers)"
                    " values (%s, %s, %s, %s, %s, %s, %s)",
                    (
                        session_id,
                        self.uid,
                        turn.at,
                        turn.question,
                        turn.answer,
                        turn.safe,
                        json.dumps(turn.numbers, ensure_ascii=False),
                    ),
                )
        except Exception as exc:  # noqa: BLE001 — 연결·권한·형식 전부 「못 적었다」다
            log.warning("턴을 못 적었습니다 (%s): %s", session_id, exc)
            return False
        return True

    def delete(self, session_id: str) -> bool:
        """턴은 `on delete cascade`가 같이 지운다 — 지우다 만 대화를 안 남긴다.

        **id 검증은 try 밖에서 한다** — 파일 판과 같은 이유고, 그래야 같은
        요청이 양쪽에서 같은 응답을 받는다.
        """
        from arc.store import pg

        check_id(session_id)
        try:
            with pg.connect(self.uid) as conn, conn.cursor() as cur:
                cur.execute(
                    "delete from arc_chat_sessions where uid = %s and id = %s",
                    (self.uid, session_id),
                )
                deleted = cur.rowcount
        except Exception as exc:  # noqa: BLE001
            log.warning("대화를 못 지웠습니다 (%s): %s", session_id, exc)
            return False
        return bool(deleted)

    # ── 읽기 ─────────────────────────────────────────────────────────
    def get(self, session_id: str) -> Session | None:
        """없으면 None. **못 읽어도 None이다** — 화면이 죽지 않는다."""
        from arc.store import pg

        check_id(session_id)
        try:
            with pg.connect(self.uid) as conn, conn.cursor() as cur:
                cur.execute(
                    "select id, title, symbols, tokens, year, created_at, updated_at"
                    " from arc_chat_sessions where uid = %s and id = %s",
                    (self.uid, session_id),
                )
                head = cur.fetchone()
                if head is None:
                    return None
                cur.execute(
                    "select at, question, answer, safe, numbers from arc_chat_turns"
                    " where uid = %s and session_id = %s order by at, id",
                    (self.uid, session_id),
                )
                rows = cur.fetchall()
        except Exception as exc:  # noqa: BLE001
            log.warning("대화를 못 읽었습니다 (%s): %s", session_id, exc)
            return None

        session = _head_of(head)
        session.turns = [
            Turn(
                question=question,
                answer=answer,
                safe=safe,
                numbers=dict(numbers or {}),
                at=_iso(at),
            )
            for at, question, answer, safe, numbers in rows
        ]
        session.turn_count = len(session.turns)
        return session

    def list(self, limit: int = MAX_SESSIONS) -> list[Session]:
        """최근 갱신 순. **본문은 빼고 준다** — 턴 수만 세어 온다."""
        from arc.store import pg

        try:
            with pg.connect(self.uid) as conn, conn.cursor() as cur:
                cur.execute(
                    "select s.id, s.title, s.symbols, s.tokens, s.year,"
                    "       s.created_at, s.updated_at, count(t.id)"
                    " from arc_chat_sessions s"
                    " left join arc_chat_turns t on t.uid = s.uid and t.session_id = s.id"
                    " where s.uid = %s"
                    " group by s.uid, s.id"
                    " order by s.updated_at desc limit %s",
                    (self.uid, max(1, limit)),
                )
                rows = cur.fetchall()
        except Exception as exc:  # noqa: BLE001
            log.warning("대화 목록을 못 읽었습니다: %s", exc)
            return []

        out: list[Session] = []
        for *head, count in rows:
            session = _head_of(tuple(head))
            session.turn_count = int(count)
            out.append(session)
        return out


def _iso(value) -> str:
    """마이크로초까지 그대로 낸다 — 파일 판과 같은 눈금이어야 정렬이 같다."""
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _head_of(row: tuple) -> Session:
    """`arc_chat_sessions` 한 행 → 턴 없는 `Session`."""
    session_id, title, symbols, tokens, year, created_at, updated_at = row
    return Session(
        id=session_id,
        title=title or "",
        symbols=[str(s) for s in (symbols or [])],
        tokens=[str(t) for t in (tokens or [])],
        year=year,
        created_at=_iso(created_at),
        updated_at=_iso(updated_at),
    )


def open_chats(base_dir: str | Path, uid: str) -> ChatStore | PgChatStore:
    """저장소를 고른다. **부르는 쪽은 어느 쪽인지 모른다.**

    `DATABASE_URL`이 있으면 Postgres, 없으면 파일. 로컬 개발과 테스트가 DB를
    요구하면 안 된다.
    """
    from arc.store import pg

    return PgChatStore(uid) if pg.available() else ChatStore(base_dir)


def migrate_chats(base_dir: str | Path, uid: str) -> int:
    """파일에 쌓인 대화를 Postgres로. **복사한다, 지우지 않는다.**

    이 저장소의 카드를 이미 두 번 잃었다. 원본을 두면 잘못돼도 되돌릴 수 있다.
    두 번 돌리면 두 번 들어가므로 **한 번만 돌린다** — 옮긴 뒤 파일에 표시를
    남기지 않는 것은, 표시를 믿기보다 사람이 한 번 확인하는 편이 낫아서다.

    **id를 그대로 들고 간다.** 새로 발급하면 화면이 들고 있던 세션 id가 죽고,
    사용자는 열어 두었던 대화를 잃는다.
    """
    from arc.store import pg

    if not pg.available():
        return 0
    source = ChatStore(base_dir)
    sessions = source.list(limit=MAX_SESSIONS)
    if not sessions:
        return 0

    target = PgChatStore(uid)
    moved = 0
    for head in reversed(sessions):  # 오래된 것부터
        full = source.get(head.id)
        if full is None:
            continue
        try:
            _insert_as_is(target, full)
        except Exception as exc:  # noqa: BLE001 — 하나가 막혀도 나머지는 옮긴다
            log.warning("대화를 못 옮겼습니다 (%s): %s", full.id, exc)
            continue
        moved += 1
    log.info("대화 %d건을 Postgres로 옮겼습니다 (원본은 그대로 둡니다)", moved)
    return moved


def _insert_as_is(target: PgChatStore, session: Session) -> None:
    """이관 전용. **id와 시각을 그대로 쓴다** — `create()`는 새로 발급한다.

    이관은 복사이지 새로 만드는 것이 아니다. 여기서 id가 바뀌면 화면이 들고
    있던 세션이 사라진 것처럼 보인다.
    """
    from arc.store import pg

    with pg.connect(target.uid) as conn, conn.cursor() as cur:
        cur.execute(
            "insert into arc_chat_sessions"
            " (id, uid, title, symbols, tokens, year, created_at, updated_at)"
            " values (%s, %s, %s, %s, %s, %s, coalesce(%s, now()), coalesce(%s, now()))"
            " on conflict (uid, id) do nothing",
            (
                session.id,
                target.uid,
                session.title,
                json.dumps(session.symbols, ensure_ascii=False),
                json.dumps(session.tokens, ensure_ascii=False),
                session.year,
                session.created_at or None,
                session.updated_at or None,
            ),
        )
        for turn in session.turns:
            if _refuses(turn):
                continue
            cur.execute(
                "insert into arc_chat_turns"
                " (session_id, uid, at, question, answer, safe, numbers)"
                " values (%s, %s, coalesce(%s, now()), %s, %s, %s, %s)",
                (
                    session.id,
                    target.uid,
                    turn.at or None,
                    turn.question,
                    turn.answer,
                    turn.safe,
                    json.dumps(turn.numbers, ensure_ascii=False),
                ),
            )
