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

import json
import logging
import os
from contextlib import contextmanager

log = logging.getLogger("arc.store.pg")

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
"""

# RLS. **앱이 실수해도 DB가 막는다.** 정책을 지웠다 다시 만드는 이유:
# `create policy`에 `if not exists`가 없다.
POLICIES = """
drop policy if exists arc_events_own on arc_events;
create policy arc_events_own on arc_events
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


@contextmanager
def connect(uid: str):
    """uid가 묶인 연결. **RLS가 이 uid 밖을 안 보여준다.**

    `SET LOCAL`이라 트랜잭션이 끝나면 사라진다 — 연결을 재사용해도 앞 사람의
    uid가 남지 않는다. 풀링을 쓸 때 이게 중요하다.
    """
    import psycopg

    with psycopg.connect(database_url()) as conn:
        with conn.cursor() as cur:
            # RLS 정책이 읽을 자리에 uid를 세운다. Supabase의 `auth.uid()`가
            # 보는 것과 **같은 키**라 정책을 나중에 그대로 옮길 수 있다.
            cur.execute(
                "select set_config('request.jwt.claims', %s, true)",
                (json.dumps({"sub": uid}),),
            )
        yield conn


def init_schema() -> None:
    """스키마와 정책을 만든다. **여러 번 돌려도 안전하다.**"""
    import psycopg

    with psycopg.connect(database_url()) as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA)
            cur.execute(POLICIES)
        conn.commit()
    log.info("스키마를 확인했습니다")


def owner_bypass() -> bool:
    """지금 연결이 RLS를 우회하는가 (테이블 소유자·superuser).

    **경고를 위해 있다.** Supabase의 `postgres` 역할은 테이블 소유자라 RLS가
    적용되지 않는다 — 그 상태로 「RLS가 지켜 준다」고 믿으면 안 된다.
    앱 전용 역할을 따로 만들어 쓰는 것이 맞고, 그때까지는 uid를 생성 시점에
    묶는 ①이 실질적인 방어다.
    """
    import psycopg

    with psycopg.connect(database_url()) as conn, conn.cursor() as cur:
        cur.execute("select rolsuper or rolbypassrls from pg_roles where rolname = current_user")
        row = cur.fetchone()
        return bool(row and row[0])
