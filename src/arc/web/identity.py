"""누구의 저장소인가 — **로그인이 뜻을 갖게 하는 자리.**

왜 필요한가
-----------
`auth.py`가 토큰에서 `sub`를 꺼내 `request.state.user_id`에 넣어 두고 있었지만
**아무 데서도 안 썼다.** `CardStore(STORE_DIR)`가 전역이라 RA 둘이 로그인하면
같은 보드를 봤다. 「내 커버 섹터」·「내 피어 그룹」을 얹기 전에 이것부터
갈라야 한다 — 안 그러면 그 「내」가 모두의 것이다.

경로로 가른다, 필드로 가르지 않는다
-----------------------------------
`Card.owner`를 두고 읽을 때 거르는 방법도 있다. **안 쓴다.** 거르는 코드를
한 군데라도 빠뜨리면 남의 카드가 새고, 그건 조용히 샌다. 경로를 나누면
빠뜨릴 수가 없다 — 애초에 다른 디렉터리를 열지 않는다.

    .arc-store/
      users/{uid}/cards/…      ← 사람마다
      users/{uid}/estimates/…
      prices/                  ← 시장 데이터는 공용
      cache/                   ← corpCode 캐시도 공용

시세와 corpCode는 **누구의 것도 아니다.** 사람마다 복제하면 디스크와 API
호출만 늘어난다.

인증이 꺼져 있을 때
-------------------
로컬 개발과 CLI에는 토큰이 없다. 그때는 `local` 한 사람으로 본다 — 「사용자
없음」을 따로 만들면 그 갈래가 영원히 남는다.

로그인을 켜는 날
----------------
그때 `local`에 쌓인 것이 **사라지면 안 된다.** 커버리지도 카드도 채널도 거기
있는데, 새 uid 디렉터리는 비어 있다. 그래서 `adopt_local()`이 한 번만, 처음
로그인한 사람에게 넘긴다.

**자격증명은 안 넘긴다.** 텔레그램 세션 파일은 데이터가 아니라 **계정 접근
권한**이다. 이걸 따라 옮기면 공유 배포에서 처음 로그인한 사람이 남의 텔레그램
계정을 쥔다. 한 번 `arc telegram login`을 다시 하는 마찰이 그보다 싸다.
"""

from __future__ import annotations

import contextvars
import logging
import re
import shutil
from pathlib import Path

log = logging.getLogger("arc.web.identity")

# 인증이 꺼져 있을 때의 한 사람. 로컬 개발·CLI가 여기로 온다.
SOLO = "local"

# 사람마다 나뉘는 것. 나머지(prices·cache)는 공용이다.
PERSONAL = ("cards", "estimates", "note_facts")

# uid가 곧 디렉터리 이름이 되므로 경로 조작을 막는다. Supabase `sub`는 UUID다.
_SAFE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

_CURRENT: contextvars.ContextVar[str] = contextvars.ContextVar("arc_user", default=SOLO)


def set_current_user(uid: str) -> contextvars.Token:
    return _CURRENT.set(safe_uid(uid))


def reset_current_user(token: contextvars.Token) -> None:
    """요청이 끝나면 원래대로. 워커 스레드가 재사용돼도 안 샌다."""
    _CURRENT.reset(token)


def current_user() -> str:
    return _CURRENT.get()


def safe_uid(uid: str) -> str:
    """디렉터리 이름으로 쓸 수 있는 형태로. 아니면 `SOLO`.

    **거부하지 않고 SOLO로 떨어뜨린다.** 여기서 예외를 던지면 토큰이 조금
    이상한 것만으로 화면 전체가 죽는다 — 인증은 이미 `auth.py`가 했다.
    """
    uid = (uid or "").strip()
    return uid if _SAFE.match(uid) else SOLO


def user_dir(base: str | Path, uid: str | None = None) -> Path:
    """이 사람의 저장소 경로. 없으면 만든다."""
    path = Path(base) / "users" / safe_uid(uid if uid is not None else current_user())
    path.mkdir(parents=True, exist_ok=True)
    return path


# **따라 옮기지 않는 것.** 데이터가 아니라 자격증명이다.
CREDENTIALS = ("telegram.session",)

# 누가 `local`을 가져갔는지 적어 두는 자리. 두 번 넘기지 않기 위한 것이다.
_CLAIM = ".claimed-by"


def adopt_local(base: str | Path, uid: str) -> list[str]:
    """로그인을 켜는 날, `local`에 쌓인 것을 **처음 로그인한 사람**에게 넘긴다.

    인증을 켜기 전에 만든 커버리지·카드·채널이 `users/local/`에 있다. 로그인
    뒤 uid가 바뀌면 그 전부가 안 보이게 되는데, **사용자에게는 사라진 것과
    같다.**

    **한 번만 넘긴다.** `.claimed-by`를 남겨 두 번째 사람은 빈 저장소로
    시작한다. 공유 배포에서 이건 「처음 로그인한 사람이 개발 데이터를
    가져간다」는 뜻이므로, 그게 곤란하면 `ARC_ADOPT_LOCAL=0`으로 끈다.

    **자격증명은 안 넘긴다** (`CREDENTIALS`). 텔레그램 세션은 계정 접근
    권한이라 데이터 이관을 따라가면 안 된다.

    **복사한다, 옮기지 않는다.** 원본을 두면 잘못돼도 되돌릴 수 있다 — 이
    저장소의 카드를 이미 두 번 잃었다.
    """
    uid = safe_uid(uid)
    if uid == SOLO:
        return []

    base = Path(base)
    src = base / "users" / SOLO
    claim = src / _CLAIM
    if not src.is_dir() or claim.exists():
        return []

    dst = base / "users" / uid
    took: list[str] = []
    for item in sorted(src.iterdir()):
        if item.name in CREDENTIALS or item.name == _CLAIM:
            continue
        target = dst / item.name
        if target.exists():
            continue  # **덮어쓰지 않는다** — 이미 자기 것이 있으면 그게 맞다
        target.parent.mkdir(parents=True, exist_ok=True)
        if item.is_dir():
            shutil.copytree(item, target)
        else:
            shutil.copy2(item, target)
        took.append(item.name)

    if took:
        claim.write_text(uid, encoding="utf-8")
        log.info(
            "로그인 전 저장물을 %s에게 넘겼습니다: %s (원본은 그대로 둡니다). "
            "텔레그램 세션은 자격증명이라 안 넘깁니다 — `arc telegram login`을 다시 하십시오.",
            uid,
            ", ".join(took),
        )
    return took


def claimed_by(base: str | Path) -> str:
    """`local`을 누가 가져갔나. 아무도 안 가져갔으면 빈 문자열."""
    claim = Path(base) / "users" / SOLO / _CLAIM
    try:
        return claim.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def migrate_legacy(base: str | Path, uid: str = SOLO) -> list[str]:
    """사용자 축이 없던 시절의 저장물을 한 사람 밑으로 **복사**한다.

    **옮기지 않고 복사한다.** 원본을 그대로 두면 잘못돼도 되돌릴 수 있다 —
    이 저장소의 카드를 이미 두 번 잃었다. 디스크 몇 MB가 그보다 싸다.

    이미 옮겨 놓은 것이 있으면 아무것도 하지 않는다(덮어쓰지 않는다).
    """
    base = Path(base)
    target = base / "users" / safe_uid(uid)
    moved: list[str] = []
    for name in PERSONAL:
        src = base / name
        dst = target / name
        if not src.is_dir() or dst.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dst)
        moved.append(name)
        log.info("사용자 축으로 복사했습니다: %s → %s (원본은 그대로 둡니다)", src, dst)
    return moved
