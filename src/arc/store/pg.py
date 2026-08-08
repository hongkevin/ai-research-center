"""Postgres 저장소 — **파일 저장소의 성질을 그대로 옮긴다.**

왜 옮기나
---------
파일은 로컬에서 잘 돈다. 옮기는 이유는 둘이다:

1. **재배포에 살아남는다.** Railway는 볼륨을 안 붙이면 컨테이너가 뜰 때마다
   비어 있다. 「축적」이 전제인 기능에서 이건 치명적이다
2. **시간축 질의가 된다.** *"지난 3개월간 한화오션에 대해 뭘 물었나"*를
   디렉터리 스캔으로 답하는 것은 오래 못 간다

무엇을 안 옮기나
----------------
시세(`prices/`)·corpCode 캐시·텔레그램 원문은 **시장에서 온 것**이지 누구의
것이 아니다. DB에 넣으면 용량만 늘고 얻는 게 없다. 파일로 둔다.

**없어도 돈다**
---------------
`DATABASE_URL`이 비어 있으면 파일 저장소로 떨어진다. 테스트와 로컬 개발이
DB를 요구하면 안 된다 — 1,189건이 지금 DB 없이 돈다.

경로로 가르던 것을 무엇으로 대신하나
------------------------------------
파일 설계의 미덕은 *"거르는 코드를 한 군데라도 빠뜨리면 남의 카드가 새는데,
경로를 나누면 빠뜨릴 수가 없다"*였다([identity.py](../web/identity.py)).
그 성질을 두 겹으로 옮긴다:

1. **uid를 생성 시점에 묶는다.** 저장소 객체는 uid를 인자로 받는 메서드가
   **없다** — 만들 때 한 번 받고 모든 질의에 그걸 쓴다. 파일 저장소가
   디렉터리를 한 번 받는 것과 같은 모양이다
2. **RLS를 켠다.** 앱이 실수해도 DB가 막는다. 연결마다 `request.jwt.claims`를
   세워 `auth.uid()`가 우리 uid를 가리키게 한다

①만으로도 성립하지만 ②가 있어야 「빠뜨릴 수가 없다」가 된다.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import threading
from contextlib import contextmanager

log = logging.getLogger("arc.store.pg")

# **연결을 재사용한다.** 요청마다 새로 열면 TLS+인증에 800ms가 든다 —
# 프로필 화면 한 번이 5.6초였고, 그 대부분이 연결을 세 번 여는 값이었다.
# 질의 자체는 왕복 한 번(현재 리전 기준 130ms)이면 끝난다.
_POOL = None
_POOL_LOCK = threading.Lock()

# 워커가 하나라(Dockerfile 참조) 크게 잡을 이유가 없다. Supabase 무료 등급의
# 풀러 한도도 넉넉하지 않다.
POOL_MIN, POOL_MAX = 1, 4

# 스키마. **`IF NOT EXISTS`로 여러 번 돌려도 안전하다** — 배포 때마다 돈다.
SCHEMA = """
create table if not exists arc_events (
    id          bigserial primary key,
    uid         text        not null,
    at          timestamptz not null default now(),
    kind        text        not null,
    subject     text        not null default '',
    -- **가리거나 플레이스홀더인 것만 들어온다** (D77). 여기 렌더된 숫자가
    -- 들어가면 맥락 조립을 타고 프롬프트에 닿아 불변식 1이 깨진다.
    text        text        not null default '',
    safe        text        not null default 'none',
    detail      jsonb       not null default '{}'::jsonb
);

-- 「내 것을, 최근 것부터」가 거의 모든 질의다
create index if not exists arc_events_uid_at on arc_events (uid, at desc);
-- 「이 종목에 대해 뭘 했나」 — 시간축 질의의 본체
create index if not exists arc_events_uid_subject on arc_events (uid, subject, at desc);

alter table arc_events enable row level security;
-- **소유자에게도 적용한다.** 이것만으로는 부족하지만(BYPASSRLS가 이긴다)
-- 없으면 소유자 연결에서 정책이 아예 안 돈다.
alter table arc_events force row level security;

-- 프로필 — **사람당 한 행, 문서 하나.**
--
-- 종목·채널을 표로 펴지 않는다. 프로필은 통째로 읽고 통째로 쓰는 문서이고,
-- 지금 「누가 005930을 커버하나」 같은 교차 질의가 없다. 펴면 스키마가 필드
-- 추가마다 흔들리는데, 문서로 두면 `_read_stock`의 D65 방어가 그대로 산다.
create table if not exists arc_profiles (
    uid         text primary key,
    doc         jsonb       not null default '{}'::jsonb,
    updated_at  timestamptz not null default now()
);

alter table arc_profiles enable row level security;
alter table arc_profiles force row level security;

-- 카드(리포트) — **문서가 정본이고 열은 파생이다.**
--
-- 목록 조회가 잦아서(보드 화면) 정렬·필터에 쓰는 것만 열로 꺼내 두는데,
-- 값을 따로 넣지 않고 `generated`로 문서에서 뽑는다. 따로 넣으면 저장할 때
-- 한쪽만 갱신하는 실수가 언젠가 나고, 그러면 목록과 내용이 다른 말을 한다.
--
-- 본문(`vm`·`assembled`·`registry`)을 별도 표로 가르지 않은 이유: 지금
-- `list()`를 쓰는 곳이 대부분 본문을 필요로 한다 — 피어 구성원 해석은
-- `registry`를, 채팅 검색은 본문 전체를 본다. 나눠 두면 「목록만 읽는」
-- 경로가 생기기 전까지는 조인만 늘어난다.
create table if not exists arc_cards (
    uid         text        not null,
    id          text        not null,
    doc         jsonb       not null,
    updated_at  timestamptz not null default now(),
    primary key (uid, id),
    kind        text generated always as (doc->>'kind') stored,
    symbol      text generated always as (doc->>'symbol') stored,
    created_at  text generated always as (doc->>'created_at') stored
);

-- 「내 것을 최신순으로」가 보드가 매번 하는 질의다
create index if not exists arc_cards_uid_created on arc_cards (uid, created_at desc);

alter table arc_cards enable row level security;
alter table arc_cards force row level security;

-- 리서치 채팅의 대화 — **브라우저에만 있던 것을 여기로 옮긴다.**
--
-- 프로필과 달리 문서 하나로 안 둔다. 대화는 **뒤에 붙기만 하는 목록**이라
-- 턴 하나가 행 하나로 떨어지고, 목록 화면은 본문 없이 제목과 턴 수만 쓴다.
-- 문서로 두면 턴을 붙일 때마다 대화 전체를 읽고 다시 쓴다.
--
-- 키가 `(uid, id)`인 이유: id는 앱이 만든 16자리 hex라 사람 사이에서 유일할
-- 이유가 없다. uid를 키에 넣으면 남의 id와 부딪힐 수가 없고, 턴의 외래키가
-- **uid까지 같이 물어서** 남의 대화에 턴을 붙이는 경로가 사라진다.
create table if not exists arc_chat_sessions (
    uid         text        not null,
    id          text        not null,
    title       text        not null default '',
    -- 다음 질문의 앵커 (chat.retrieval.Context). 종목·어휘·연도뿐이다
    symbols     jsonb       not null default '[]'::jsonb,
    tokens      jsonb       not null default '[]'::jsonb,
    year        int,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now(),
    primary key (uid, id)
);

-- 「내 것을, 최근 갱신 순」이 목록 화면의 전부다
create index if not exists arc_chat_sessions_uid_at on arc_chat_sessions (uid, updated_at desc);

create table if not exists arc_chat_turns (
    id          bigserial primary key,
    uid         text        not null,
    session_id  text        not null,
    at          timestamptz not null default now(),
    -- **가린 것만 들어온다.** 사람이 친 질문이라 무엇이 들었는지 모른다.
    question    text        not null default '',
    -- **플레이스홀더거나 가린 것만 들어온다.** 여기 렌더된 숫자가 들어가면
    -- 맥락 조립을 타고 프롬프트에 닿아 불변식 1이 깨진다 — arc_events와
    -- 같은 규칙이고, `chats.build_turn()`이 그것을 세운다.
    answer      text        not null default '',
    safe        text        not null default 'none',
    -- `{{num:key}}` → 화면에 보일 문자열. **본문이 아니라 표다** — 프롬프트로
    -- 조립되는 것은 본문뿐이라 여기 값이 있어도 LLM에 닿지 않는다.
    numbers     jsonb       not null default '{}'::jsonb,
    foreign key (uid, session_id) references arc_chat_sessions (uid, id) on delete cascade
);

-- 대화 하나를 열면 그 턴을 시간순으로 전부 읽는다
create index if not exists arc_chat_turns_session on arc_chat_turns (uid, session_id, at);

alter table arc_chat_sessions enable row level security;
alter table arc_chat_sessions force row level security;
alter table arc_chat_turns enable row level security;
alter table arc_chat_turns force row level security;

-- 우리가 트랜잭션마다 내려앉을 역할. Supabase가 PostgREST용으로 이미
-- 만들어 둔 것이고 **BYPASSRLS가 없다** — 그래서 정책이 실제로 적용된다.
grant usage on schema public to authenticated;
grant select, insert, update, delete on arc_events to authenticated;
grant usage, select on sequence arc_events_id_seq to authenticated;
grant select, insert, update, delete on arc_profiles to authenticated;
grant select, insert, update, delete on arc_cards to authenticated;
grant select, insert, update, delete on arc_chat_sessions to authenticated;
grant select, insert, update, delete on arc_chat_turns to authenticated;
grant usage, select on sequence arc_chat_turns_id_seq to authenticated;
"""

# RLS. **앱이 실수해도 DB가 막는다.** 정책을 지웠다 다시 만드는 이유:
# `create policy`에 `if not exists`가 없다.
POLICIES = """
drop policy if exists arc_events_own on arc_events;
create policy arc_events_own on arc_events
    using (uid = current_setting('request.jwt.claims', true)::json->>'sub')
    with check (uid = current_setting('request.jwt.claims', true)::json->>'sub');

drop policy if exists arc_profiles_own on arc_profiles;
create policy arc_profiles_own on arc_profiles
    using (uid = current_setting('request.jwt.claims', true)::json->>'sub')
    with check (uid = current_setting('request.jwt.claims', true)::json->>'sub');

drop policy if exists arc_cards_own on arc_cards;
create policy arc_cards_own on arc_cards
    using (uid = current_setting('request.jwt.claims', true)::json->>'sub')
    with check (uid = current_setting('request.jwt.claims', true)::json->>'sub');

-- 대화도 같은 규칙이다. **턴에도 따로 건다** — 세션에만 걸고 턴을 열어 두면
-- 세션 id만 알면 남의 질문과 답을 읽는다. 외래키가 있으니 괜찮다는 말은
-- 성립하지 않는다: 외래키는 무결성이지 접근 제어가 아니다.
drop policy if exists arc_chat_sessions_own on arc_chat_sessions;
create policy arc_chat_sessions_own on arc_chat_sessions
    using (uid = current_setting('request.jwt.claims', true)::json->>'sub')
    with check (uid = current_setting('request.jwt.claims', true)::json->>'sub');

drop policy if exists arc_chat_turns_own on arc_chat_turns;
create policy arc_chat_turns_own on arc_chat_turns
    using (uid = current_setting('request.jwt.claims', true)::json->>'sub')
    with check (uid = current_setting('request.jwt.claims', true)::json->>'sub');
"""


def database_url() -> str:
    """`DATABASE_URL`. 비어 있으면 빈 문자열 — **그때는 파일로 돈다.**"""
    return (os.environ.get("DATABASE_URL") or "").strip()


def available() -> bool:
    if not database_url():
        return False
    try:
        import psycopg  # noqa: F401
    except ImportError:
        # 키는 있는데 드라이버가 없는 것은 **설정 실수**다. 조용히 파일로
        # 떨어지면 「왜 DB에 안 쌓이지」를 한참 뒤에 안다.
        log.warning("DATABASE_URL이 있는데 psycopg가 없습니다 — `pip install 'arc[db]'`")
        return False
    return True


def _pool():
    """열려 있는 풀. **처음 쓸 때 만든다** — import 시점에 붙지 않는다."""
    global _POOL
    if _POOL is None:
        with _POOL_LOCK:
            if _POOL is None:
                from psycopg_pool import ConnectionPool

                _POOL = ConnectionPool(
                    database_url(),
                    min_size=POOL_MIN,
                    max_size=POOL_MAX,
                    # **체크를 안 한다.** 체크아웃마다 `SELECT 1`을 던지는데
                    # 지금 리전에서 그것만 130ms다 — 요청마다 왕복을 하나 더
                    # 물면서 얻는 것은 「드물게 끊긴 연결」뿐이다. 끊기면
                    # 저장소가 예외를 잡아 경고하고 다음 요청이 새로 받는다.
                    open=True,
                )
                atexit.register(close_pool)
    return _POOL


def close_pool() -> None:
    """풀을 닫는다. 테스트와 종료용."""
    global _POOL
    with _POOL_LOCK:
        if _POOL is not None:
            _POOL.close()
            _POOL = None


@contextmanager
def connect(uid: str):
    """uid가 묶인 연결. **RLS가 이 uid 밖을 안 보여준다.**

    `SET LOCAL`이라 트랜잭션이 끝나면 사라진다 — 연결을 재사용해도 앞 사람의
    uid가 남지 않는다. 풀링을 쓸 때 이게 중요하다.

    **역할을 낮춰 앉는다.** 연결 계정(`postgres`)은 `BYPASSRLS`를 갖고 있어서
    그대로 두면 정책이 **한 줄도 적용되지 않는다** — RLS를 켜 놓고 안 지켜지는,
    가장 나쁜 상태다. `authenticated`는 Supabase가 PostgREST용으로 이미 만들어
    둔 역할이고 BYPASSRLS가 없다. 새 계정을 만들 필요가 없다.

    **중간에 커밋하지 마십시오.** `SET LOCAL`은 트랜잭션 것이라, 커밋하면
    claims와 역할이 사라지고 그 뒤 질의는 `postgres`로 돌아 **RLS를 우회한다.**
    이 블록을 정상적으로 빠져나가면 풀이 커밋하므로 명시적 커밋이 필요 없다 —
    그래서 저장소들에서 `conn.commit()`을 전부 걷어냈다.
    """
    with _pool().connection() as conn:
        # **한 번의 왕복으로 둘 다 세운다.** 나눠 보내면 왕복이 두 번이고,
        # 지금 리전에서 그것만 260ms다. `set_config('role', …)`은
        # `SET LOCAL role`과 같다.
        conn.execute(
            "select set_config('request.jwt.claims', %s, true),"
            "       set_config('role', 'authenticated', true)",
            (json.dumps({"sub": uid}),),
        )
        yield conn


def init_schema() -> None:
    """스키마와 정책을 만든다. **여러 번 돌려도 안전하다.**"""
    import psycopg

    # 스키마는 **소유자로** 만든다 — `authenticated`는 DDL 권한이 없다.
    with psycopg.connect(database_url()) as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA)
            cur.execute(POLICIES)
        conn.commit()
    log.info("스키마를 확인했습니다")


def rls_enforced(uid: str = "rls-probe") -> bool:
    """**정책이 실제로 도는가.** 「켜져 있는가」가 아니라 「지켜지는가」다.

    `postgres`는 `BYPASSRLS`를 갖고 있어 `enable`·`force`를 다 켜도 정책이 한
    줄도 적용되지 않는다. 그래서 설정을 읽어 판단하지 않고 **직접 물어본다** —
    이 트랜잭션에서 정책이 도는가.
    """
    import psycopg

    try:
        with connect(uid) as conn, conn.cursor() as cur:
            cur.execute("select row_security_active('arc_events')")
            row = cur.fetchone()
            return bool(row and row[0])
    except psycopg.Error as exc:
        log.warning("RLS 적용 여부를 못 봤습니다: %s", exc)
        return False


# 내보내기가 다루는 표. **순서가 있다** — 들여올 때 이 순서로 넣는다.
# 대화의 턴은 세션을 외래키로 물으므로 **세션 뒤**여야 한다.
TABLES = (
    "arc_profiles",
    "arc_cards",
    "arc_events",
    "arc_chat_sessions",
    "arc_chat_turns",
)

# `bigserial`로 id를 받는 표. 들여올 때 **id를 버린다** — 옮긴 DB의 시퀀스와
# 충돌하면 두 번째 행부터 전부 거절된다.
_SERIAL_ID = frozenset({"arc_events", "arc_chat_turns"})

# 시험하다 남은 uid. 내보낼 때 뺀다 — 옮긴 DB에 쓰레기를 들고 가지 않는다.
_JUNK_PREFIXES = ("test-", "probe-", "rls-probe", "alice-", "bob-")


def _is_junk(uid: str) -> bool:
    return any(uid.startswith(x) for x in _JUNK_PREFIXES)


def export_all(*, skip_junk: bool = True) -> dict:
    """전부 내보낸다. **소유자 연결로 읽는다** — RLS를 우회해야 전 사용자를 본다.

    리전을 옮기려면 새 프로젝트를 만들어야 하고(Supabase는 리전 변경이 안 된다),
    그때 **DB에만 있는 것**이 사라진다. 파일에 원본이 있는 것들과 달리 로그인
    뒤에 화면에서 넣은 커버리지는 여기밖에 없다.
    """
    import psycopg

    out: dict = {"tables": {}}
    with psycopg.connect(database_url()) as conn, conn.cursor() as cur:
        for table in TABLES:
            cur.execute(f"select * from {table}")
            cols = [d.name for d in cur.description]
            rows = []
            for row in cur.fetchall():
                item = dict(zip(cols, row, strict=True))
                if skip_junk and _is_junk(str(item.get("uid", ""))):
                    continue
                rows.append(item)
            out["tables"][table] = {"columns": cols, "rows": rows}
    return out


def import_all(dump: dict) -> dict[str, int]:
    """내보낸 것을 넣는다. **이미 있는 것은 안 덮는다.**

    두 번 돌려도 안전해야 한다 — 옮기다 중간에 끊기면 다시 돌리게 된다.
    `generated` 열(카드의 kind·symbol·created_at)은 **넣지 않는다**: 문서에서
    파생되므로 넣으려 하면 Postgres가 거절한다.
    """
    import psycopg

    generated = {"kind", "symbol", "created_at"}
    counts: dict[str, int] = {}
    with psycopg.connect(database_url()) as conn:
        for table in TABLES:
            block = (dump.get("tables") or {}).get(table) or {}
            rows = block.get("rows") or []
            done = 0
            for row in rows:
                item = {
                    k: v
                    for k, v in row.items()
                    if not (table == "arc_cards" and k in generated)
                    # 시퀀스가 주는 `id`는 새로 받는다 (`_SERIAL_ID`)
                    and not (table in _SERIAL_ID and k == "id")
                }
                cols = ", ".join(item)
                marks = ", ".join(["%s"] * len(item))
                values = [
                    json.dumps(v, ensure_ascii=False, default=str)
                    if isinstance(v, dict | list)
                    else v
                    for v in item.values()
                ]
                conn.execute(
                    f"insert into {table} ({cols}) values ({marks}) on conflict do nothing",
                    values,
                )
                done += 1
            counts[table] = done
        conn.commit()
    return counts


def purge_junk() -> dict[str, int]:
    """시험하다 남은 행을 지운다. **소유자 연결로 한다.**

    **거꾸로 돈다.** 세션을 먼저 지우면 턴이 `cascade`로 따라 사라져 그 뒤의
    턴 삭제가 0을 세는데, 그러면 「몇 줄 지웠나」가 사실과 달라진다.
    """
    import psycopg

    out: dict[str, int] = {}
    with psycopg.connect(database_url()) as conn:
        for table in reversed(TABLES):
            cur = conn.execute(
                f"delete from {table} where " + " or ".join(["uid like %s"] * len(_JUNK_PREFIXES)),
                [f"{x}%" for x in _JUNK_PREFIXES],
            )
            out[table] = cur.rowcount
        conn.commit()
    return out
