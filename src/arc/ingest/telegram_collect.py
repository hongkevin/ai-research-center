"""텔레그램에서 메시지를 가져온다 — **수집기는 갈아끼울 수 있다.**

왜 갈아끼우는가
---------------
[D66](../../../docs/decisions.md#d66)에서 정했다. 두 경로가 있고 성질이 다르다:

* **채널별 내보내기 업로드** — 약관·계정 리스크가 없다. 납품본이 쓸 것
* **MTProto(Telethon)** — 자동이지만 [Content Licensing 약관](https://telegram.org/tos/content-licensing)이
  *"…or **deployment** of artificial intelligence"*를 금지하고, 예외를
  *"non-global **context window**"* 단위 동의로 한정한다. **텔레그램이 LLM
  투입을 명시적으로 사정거리에 넣었다**

그래서 **리스크를 코드가 아니라 배포 경계에 둔다.** 파서·분류·집계는 하나고
(`telegram_parse.py`), 여기서 갈리는 것은 **메시지를 어디서 얻느냐**뿐이다.

같은 모양으로 낸다
------------------
Telethon 메시지를 **내보내기 JSON과 같은 dict**로 옮긴다. 그래야
`parse_messages()`가 그대로 돌고, 수집기를 바꿔도 그 뒤가 안 바뀐다.
특히 `text_entities`를 만들어 준다 — 파서가 `text`가 아니라 그걸 읽는다.

로그인은 CLI에서만
------------------
Telethon 인증은 **전화번호 → 코드 → (2FA면) 비밀번호**로 사람이 개입한다.
웹 요청 안에서 할 수 없고, 해서도 안 된다 — 세션 파일은 계정 그 자체라
서버가 대신 들고 있을 물건이 아니다. `arc telegram login`이 그 자리다.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("arc.ingest.telegram_collect")

# 세션 파일 = 계정 접근권. **저장소 안에 두고 gitignore한다.**
SESSION_NAME = "telegram"

# 한 채널에서 한 번에 가져올 상한. 첫 동기화가 8,638건짜리 채널을 통째로
# 끌어오면 몇 분이 걸리고, 그건 「어제 뭐가 있었나」에 필요 없는 일이다.
DEFAULT_LIMIT = 300


@dataclass
class Fetched:
    """채널 하나에서 가져온 것. `parse_export()`에 그대로 넣을 수 있다."""

    chat_id: int
    name: str
    chat_type: str
    messages: list[dict]

    def as_export(self) -> dict:
        """내보내기 JSON의 Chat 객체와 **같은 모양**."""
        return {
            "id": self.chat_id,
            "name": self.name,
            "type": self.chat_type,
            "messages": self.messages,
        }


def session_path(base: str | Path) -> Path:
    """`{store}/telegram.session`. **사용자 디렉터리 안이다** — 계정은 사람마다다."""
    return Path(base) / SESSION_NAME


def credentials() -> tuple[int, str]:
    """`my.telegram.org`에서 받은 API 자격.

    **봇 토큰이 아니다.** 봇은 남의 채널에 못 들어가고 히스토리도 못 읽는다
    (D66). 개인 계정의 API 자격이라 `.env`에 둔다.
    """
    api_id = os.environ.get("TELEGRAM_API_ID", "").strip()
    api_hash = os.environ.get("TELEGRAM_API_HASH", "").strip()
    if not api_id.isdigit() or not api_hash:
        raise ValueError(
            "TELEGRAM_API_ID·TELEGRAM_API_HASH가 필요합니다 — "
            "https://my.telegram.org 에서 앱을 만들면 나옵니다."
        )
    return int(api_id), api_hash


def _entities(message) -> list[dict]:
    """Telethon 메시지 → 내보내기 JSON의 `text_entities`.

    **파서가 `text`가 아니라 이걸 읽는다.** 링크(`text_link.href`)가 여기
    있어야 봇 채널의 공시 원문 링크가 살아남는다.
    """
    text = message.message or ""
    raw = getattr(message, "entities", None) or []
    if not raw:
        return [{"type": "plain", "text": text}] if text else []

    # Telethon 오프셋은 **UTF-16 코드 유닛**이다. 파이썬 문자열은 코드포인트라
    # 이모지가 하나라도 있으면 자리가 밀린다 — 한 번 UTF-16으로 옮겨서 자른다.
    buf = text.encode("utf-16-le")

    def slice_at(offset: int, length: int) -> str:
        return buf[offset * 2 : (offset + length) * 2].decode("utf-16-le", "ignore")

    out: list[dict] = []
    cursor = 0
    for ent in sorted(raw, key=lambda e: e.offset):
        if ent.offset > cursor:
            out.append({"type": "plain", "text": slice_at(cursor, ent.offset - cursor)})
        piece = slice_at(ent.offset, ent.length)
        kind = type(ent).__name__
        if kind == "MessageEntityTextUrl":
            out.append({"type": "text_link", "text": piece, "href": getattr(ent, "url", "")})
        elif kind == "MessageEntityUrl":
            out.append({"type": "link", "text": piece})
        elif kind == "MessageEntityBold":
            out.append({"type": "bold", "text": piece})
        elif kind == "MessageEntityItalic":
            out.append({"type": "italic", "text": piece})
        elif kind in ("MessageEntityCode", "MessageEntityPre"):
            out.append({"type": "code", "text": piece})
        else:
            out.append({"type": "plain", "text": piece})
        cursor = ent.offset + ent.length
    total = len(buf) // 2
    if cursor < total:
        out.append({"type": "plain", "text": slice_at(cursor, total - cursor)})
    return out


def to_export_dict(message, *, chat_name: str) -> dict:
    """Telethon 메시지 → 내보내기 JSON의 메시지 dict.

    **`date_unixtime`을 정본으로 둔다.** 내보내기의 `date`는 내보낸 기계의
    로컬 시각이라 채널마다 어긋나는데, 유닉스 시각은 안 그렇다.
    """
    when: dt.datetime = message.date
    entities = _entities(message)
    sender = getattr(message, "sender_id", None)
    fwd = getattr(message, "forward", None)
    return {
        "id": message.id,
        "type": "message",
        "date": when.isoformat(timespec="seconds"),
        "date_unixtime": str(int(when.timestamp())),
        "from": chat_name,
        "from_id": f"channel{getattr(message, 'chat_id', 0)}"
        if sender is None
        else f"user{sender}",
        "text": message.message or "",
        "text_entities": entities,
        "reply_to_message_id": getattr(message, "reply_to_msg_id", None),
        "forwarded_from": getattr(getattr(fwd, "chat", None), "title", None) if fwd else None,
        "edited": message.edit_date.isoformat(timespec="seconds") if message.edit_date else None,
    }


def _chat_type(entity) -> str:
    """`public_channel` / `private_channel` / `private_group`.

    파서가 이걸로 딥링크를 만들고(공개 채널은 username이 필요) 내부 대화방을
    가른다.
    """
    if getattr(entity, "megagroup", False) or getattr(entity, "gigagroup", False):
        return "private_group"
    if getattr(entity, "broadcast", False):
        return "public_channel" if getattr(entity, "username", None) else "private_channel"
    return "private_group"


async def fetch_dialogs(client, *, limit: int = 200) -> list[dict]:
    """구독 중인 채널 목록. **가져오기 전에 무엇이 있는지 보여준다.**

    34개를 통째로 긁는 것이 아니라 사람이 고르게 한다 — 그중 대부분은
    이미 DART·뉴스 API로 갖고 있는 것이다(D66).
    """
    out: list[dict] = []
    async for dialog in client.iter_dialogs(limit=limit):
        entity = dialog.entity
        if not (getattr(entity, "broadcast", False) or getattr(entity, "megagroup", False)):
            continue
        out.append(
            {
                "chat_id": int(dialog.id),
                "name": dialog.name or "",
                "chat_type": _chat_type(entity),
                "username": getattr(entity, "username", None),
                "unread": int(getattr(dialog, "unread_count", 0) or 0),
            }
        )
    return out


async def fetch_channel(client, chat_id: int, *, limit: int = DEFAULT_LIMIT, since=None) -> Fetched:
    """채널 하나의 최근 메시지. `since` 이후만."""
    entity = await client.get_entity(chat_id)
    name = getattr(entity, "title", None) or str(chat_id)
    messages: list[dict] = []
    async for message in client.iter_messages(entity, limit=limit):
        if since is not None and message.date < since:
            break
        if not (message.message or "").strip():
            # 사진·스티커만 있는 것은 우리가 읽을 게 없다.
            continue
        messages.append(to_export_dict(message, chat_name=name))
    messages.reverse()  # 오래된 것부터 — 내보내기 JSON과 같은 순서
    return Fetched(
        chat_id=int(chat_id),
        name=name,
        chat_type=_chat_type(entity),
        messages=messages,
    )
