"""어느 주소로 열 것인가 — **tailnet만 열고 카페 WiFi는 안 연다** (D88).

왜 필요한가
-----------
폰에서 개발 중인 화면을 보려면 개발 서버가 루프백 밖으로 나와야 한다. 기본값은
`127.0.0.1`이라 맥 자기 자신만 볼 수 있다.

흔한 처방은 `--host 0.0.0.0`인데, 그건 **모든 인터페이스**를 연다는 뜻이다.
카페 WiFi에 붙어 있으면 그 망의 아무나 개발 서버에 닿는다. 이 서버는 DART·
금융위·ECOS·LLM 키를 들고 있고, 인증이 꺼져 있으면(`ARC_PASSWORD`도 Supabase
URL도 없을 때) **그대로 무방비다** — `web/auth.py`가 그 경고를 하는 이유다.

**Tailscale 주소로만 연다.** tailnet 인터페이스에 붙은 `100.x.x.x` 하나에만
바인딩하면, 그 주소는 내 tailnet 안에서만 라우팅되므로 같은 카페 WiFi에 있는
사람은 애초에 도달 경로가 없다. `0.0.0.0`과 결과가 전혀 다르다.

무엇을 안 하나
--------------
**Tailscale을 대신 켜 주지 않는다.** 로그인이 필요한 일이고, 여기서 조용히
켜면 사람이 모르는 네트워크가 생긴다. 안 켜져 있으면 **그렇다고 말하고 멈춘다.**
"""

from __future__ import annotations

import socket

# Tailscale이 나눠 주는 대역(CGNAT). 100.64.0.0/10 — 즉 100.64.x.x ~ 100.127.x.x.
# 이 대역은 공인 인터넷에 라우팅되지 않는다.
_TAILNET_FIRST = 100
_TAILNET_LO, _TAILNET_HI = 64, 127

# 루프백이 아닌 곳에 열 때 이 이름으로 부른다
TAILNET = "tailnet"


def is_tailnet(ip: str) -> bool:
    """`100.64.0.0/10` 안인가. **`100.0.0.1` 같은 것에 속지 않는다.**"""
    parts = ip.split(".")
    if len(parts) != 4:
        return False
    try:
        first, second = int(parts[0]), int(parts[1])
    except ValueError:
        return False
    return first == _TAILNET_FIRST and _TAILNET_LO <= second <= _TAILNET_HI


def tailnet_address() -> str:
    """이 기기의 tailnet 주소. 없으면 빈 문자열.

    `psutil` 같은 것을 안 쓰려고 소켓으로 인터페이스를 훑는다 — 파이썬
    표준만으로 되고, Tailscale CLI가 깔려 있는지와 무관하다(앱 스토어판에는
    `tailscale` 명령이 PATH에 없다).
    """
    seen: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            seen.add(info[4][0])
    except OSError:
        pass
    # `gethostname()`이 tailnet 주소를 안 줄 때가 있어 한 번 더 본다
    try:
        import subprocess

        out = subprocess.run(
            ["ifconfig"], capture_output=True, text=True, timeout=5, check=False
        ).stdout
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("inet "):
                seen.add(line.split()[1])
    except (OSError, subprocess.SubprocessError):
        pass
    return next((ip for ip in sorted(seen) if is_tailnet(ip)), "")


def resolve_host(host: str) -> tuple[str, str]:
    """`--host` 값 → `(실제 바인딩 주소, 사람에게 할 말)`.

    `tailnet`이면 tailnet 주소를 찾아 넣는다. 못 찾으면 **빈 주소를 돌려주고**
    부르는 쪽이 멈춘다 — 못 찾았다고 `0.0.0.0`으로 떨어지면 정확히 피하려던
    일이 벌어진다.
    """
    if host != TAILNET:
        return host, ""
    found = tailnet_address()
    if found:
        return found, f"tailnet 주소 {found}에만 엽니다 — 이 망 밖에서는 안 보입니다."
    return "", (
        "tailnet 주소를 찾지 못했습니다. Tailscale이 켜져 있는지 확인하십시오"
        " (메뉴 막대 아이콘 → Connected).\n"
        "  카페 WiFi에서 `--host 0.0.0.0`으로 대신 열지 마십시오 — 그 망의"
        " 아무나 이 서버의 API 키에 닿습니다."
    )
