"""테스트는 **개발자의 `.env`를 안 본다.**

`web/app.py`가 import 시점에 `load_dotenv()`를 부른다 — uvicorn이 CLI를
거치지 않고 모듈을 직접 import하기 때문에 필요한 일이다. 그런데 그 부작용으로
**개발자 기계에 어떤 키가 꽂혀 있느냐에 따라 테스트 결과가 달라진다.**

실제로 밟았다: `.env`에 `SUPABASE_JWT_SECRET`을 넣은 순간 33건이 401로 죽었다.
코드는 하나도 안 바뀌었는데. 그리고 CI에는 `.env`가 없으니 **거기서는 안
재현된다** — 가장 나쁜 종류의 실패다.

인증 미들웨어는 **만들어질 때** 시크릿을 읽고(`auth.py:85`), 앱은 모듈 수준에서
미들웨어를 단다(`app.py:191`). 그래서 픽스처로는 늦다 — `arc.web.app`이
import되기 **전에** 지워야 하고, 그 자리가 여기다(conftest는 테스트 모듈보다
먼저 로드된다).

인증을 검사하는 테스트는 자기가 `monkeypatch.setenv`로 켠다. 그쪽이 명시적이고,
그때만 켜지는 편이 맞다.

**지우지 않고 빈 값으로 선점한다.** `load_dotenv`는 `override=False`라 이미
`os.environ`에 있는 이름을 안 덮는다 — 지우기만 하면 app.py가 import될 때
`.env`에서 다시 채워 넣는다. 실제로 그렇게 한 번 헛짚었다.
"""

from __future__ import annotations

import os

# 여기 있는 것은 **테스트가 스스로 켜야** 하는 것들이다. 켜진 채로 물려받으면
# 무엇을 검사하는지가 기계마다 달라진다.
_AMBIENT = (
    "SUPABASE_JWT_SECRET",
    # **프로젝트 URL도 인증 모드를 켠다** (JWKS). 시크릿만 막았더니 Basic
    # 테스트가 401로 죽었다 — 껐다고 생각한 것이 안 꺼져 있었다.
    "SUPABASE_URL",
    "NEXT_PUBLIC_SUPABASE_URL",
    "ARC_PASSWORD",
    "ARC_USERNAME",
    "ARC_LLM_LIMIT",
    # 로그인 전 저장물 이관. 테스트는 tmp_path를 쓰므로 무해하지만,
    # **환경이 동작을 바꾸는 것 자체를** 테스트에서 끊는다.
    "ARC_ADOPT_LOCAL",
    # **저장소가 바뀌면 테스트가 다른 것을 검사한다.** 이게 있으면
    # `open_events`가 Postgres를 고르고, 파일에 써 놓고 DB에서 읽는 상태가
    # 된다 — 실제로 2건이 그렇게 깨졌다. 그리고 전체 실행이 8초에서 40초가
    # 된다. DB를 쓰는 테스트는 `ARC_TEST_DATABASE_URL`로 **따로 켠다.**
    "DATABASE_URL",
)

for _name in _AMBIENT:
    # 빈 문자열도 「있는 값」이라 `load_dotenv`가 안 덮는다
    os.environ[_name] = ""
