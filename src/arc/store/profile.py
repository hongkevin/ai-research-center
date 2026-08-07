"""내 커버리지 — **RA가 쌓아 가는 것.**

왜 필요한가
-----------
지금까지 이 제품은 매번 처음부터 시작했다. 종목코드를 넣고, 리포트를 만들고,
떠난다. **RA는 그렇게 일하지 않는다** — 같은 20~30종목을 몇 년 본다. 어느
섹터를 맡고, 어느 종목에 리포트를 내고, 어느 피어를 옆에 두는지가 그 사람의
자산이고, 그게 쌓이지 않으면 로그인할 이유가 없다.

**무엇을 쌓고 무엇을 안 쌓는가**
--------------------------------
이 제품이 유일하게 안 하기로 한 것은 **출처 없는 것을 사실처럼 두는 것**이다
(불변식 1). 장기기억도 같은 규칙을 받는다:

| 쌓는다 | 왜 안전한가 |
|---|---|
| 사람이 정한 것 (커버 종목·섹터, 고정한 피어 그룹) | RA 본인의 결정이라 출처가 본인이다 |
| 출처 있는 사실 (카드·레지스트리) | 이미 게이트를 통과했다 |
| 리퀘스트 이력 (질문·답·그때의 출처) | 되짚을 수 있다 |

**LLM이 만든 요약은 안 쌓는다.** 필요하면 그때 다시 만들면 된다 — 건당
$0.002고, 쌓아 두면 **틀린 것이 굳는다.** 이 파일에 요약 필드가 없는 것은
빠뜨린 게 아니라 정한 것이다.

피어 그룹이 왜 여기 있는가
--------------------------
[D68](../../../docs/decisions.md#d68)에서 *"확정한 그룹은 고정한다, 매번
재계산 금지"*로 정했다 — 상관 top15가 기간이 바뀌면 4/15까지 흔들려서
표가 조용히 바뀌면 「직전 대비 변화」(D46)가 무의미해지기 때문이다. 그
「고정」이 놓일 자리가 지금까지 없어서 카드로만 떠 있었다. 여기가 그 자리다.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

log = logging.getLogger("arc.store.profile")

# 한 사람이 실제로 커버하는 규모. 실무에서 애널리스트 1인이 20~30종목이고
# RA는 3~4명을 보조하니 넉넉히 잡아도 이 언저리다. 상한은 실수를 막는
# 장치이지 정책이 아니다.
MAX_STOCKS = 120
MAX_SECTORS = 20

# 커버 종목과 관심 종목. **둘은 다른 일이다** — 커버는 내가 리포트를 내는
# 종목이고, 관심은 옆에서 보는 종목이다.
COVER = "cover"
WATCH = "watch"
KINDS = (COVER, WATCH)


@dataclass
class Covered:
    """커버하는 종목 하나. **왜 보는지**를 함께 둔다.

    코드만 모아 두면 몇 달 뒤에 「이건 왜 넣었지」가 된다. `note`는 사람이
    쓰는 것이라 검증 대상이 아니고, 그래서 본문에 안 들어간다.
    """

    symbol: str
    company: str = ""
    sector: str = ""
    # **커버냐 관심이냐.** 「발간 여부」로 뒀던 것을 고쳤다 — 커버 종목이면
    # 리포트를 내는 것이 자명해서 체크박스가 있을 이유가 없었다. 실제로
    # 갈리는 축은 **내가 책임지는 종목인가, 옆에서 보는 종목인가**다:
    #
    #   cover — 내가 리포트를 낸다. 실적 시즌에 반드시 봐야 한다
    #   watch — 피어·경쟁사·모니터링. 안 봐도 사고가 안 난다
    #
    # 모닝 브리프·알림의 우선순위가 여기서 갈린다.
    kind: str = COVER
    note: str = ""
    added_at: str = ""

    @property
    def publishes(self) -> bool:
        """옛 이름. 커버 종목이면 발간한다."""
        return self.kind == COVER


@dataclass
class Profile:
    """이 사람의 커버리지. **화면 상태가 아니라 업무의 형태다.**"""

    uid: str = ""
    display_name: str = ""
    # 맡은 섹터. 자유 텍스트다 — 표준 분류로 못 적는다는 것이 D68의 결론이다
    # (방산 4종목이 KSIC 어느 자릿수에서도 한 그룹이 안 된다).
    sectors: list[str] = field(default_factory=list)
    stocks: list[Covered] = field(default_factory=list)
    # 고정한 피어 그룹의 카드 id. **그룹 자체를 복제하지 않는다** — 카드가
    # 정본이고 여기는 「내가 계속 보는 것」이라는 표시다.
    pinned_peers: list[str] = field(default_factory=list)
    updated_at: str = ""

    # ── 조회 ─────────────────────────────────────────────────────────
    def symbols(self) -> list[str]:
        return [s.symbol for s in self.stocks]

    def covers(self, symbol: str) -> bool:
        return any(s.symbol == symbol for s in self.stocks)

    def covering(self) -> list[str]:
        """**내가 리포트를 내는 종목.** 브리프·알림이 여기를 먼저 본다."""
        return [s.symbol for s in self.stocks if s.kind == COVER]

    def watching(self) -> list[str]:
        """옆에서 보는 종목. 안 봐도 사고가 안 난다."""
        return [s.symbol for s in self.stocks if s.kind == WATCH]

    # 옛 이름 (import 호환)
    def publishing(self) -> list[str]:
        return self.covering()

    @property
    def empty(self) -> bool:
        return not self.stocks and not self.sectors


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


def _read_stock(raw: dict) -> Covered:
    """저장된 종목 한 줄 → `Covered`. **모르는 필드는 버린다.**

    필드를 바꾸기 **전에** 저장된 것이 반드시 있다. 카드에서 이 자리를 두 번
    밟았고(D65) 여기서 세 번째로 밟았다 — 「발간 여부」를 「커버냐 관심이냐」로
    고쳤더니 옛 프로필의 `publishes`가 `TypeError`를 냈고 화면이 500이 됐다.

    옛 `publishes`는 뜻이 있으니 새 축으로 **옮겨 준다** — 버리면 사용자가
    표시해 둔 것이 조용히 사라진다.
    """
    known = {k: v for k, v in raw.items() if k in Covered.__dataclass_fields__}
    if "kind" not in known and "publishes" in raw:
        known["kind"] = COVER if raw.get("publishes") else WATCH
    if known.get("kind") not in KINDS:
        known["kind"] = COVER
    return Covered(**known)


class ProfileStore:
    """`{user_dir}/profile.json` 하나.

    카드와 같은 자리에 둔다 — 사람별로 갈린 그 디렉터리다. 프로필만 따로
    빼면 사용자 축이 두 군데가 되고, 그러면 한쪽을 지웠을 때 다른 쪽이 남는다.
    """

    def __init__(self, base_dir: str | Path) -> None:
        self.path = Path(base_dir) / "profile.json"

    def load(self, uid: str = "") -> Profile:
        """없으면 **빈 프로필**을 돌려준다 — 없는 것은 오류가 아니다."""
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return Profile(uid=uid)
        stocks = [_read_stock(s) for s in raw.pop("stocks", []) if isinstance(s, dict)]
        try:
            return Profile(**raw, stocks=stocks)
        except TypeError as exc:
            # 필드를 추가하기 **전에** 저장된 프로필. 카드에서 이미 두 번
            # 밟은 자리다(D65) — 여기서는 아는 것만 취한다.
            log.warning("프로필에 모르는 필드가 있어 아는 것만 읽었습니다: %s", exc)
            known = {k: v for k, v in raw.items() if k in Profile.__dataclass_fields__}
            return Profile(**known, stocks=stocks)

    def save(self, profile: Profile) -> Profile:
        profile.updated_at = _now()
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(profile), ensure_ascii=False), encoding="utf-8")
        # 원자적 교체 — 쓰다 죽으면 반쪽 JSON이 남아 프로필이 통째로 사라진다
        tmp.replace(self.path)
        return profile


def add_stock(profile: Profile, stock: Covered) -> Profile:
    """커버 종목 추가. **같은 종목은 덮어쓴다** — 두 번 들어가면 브리프가
    같은 종목을 두 번 낸다."""
    stock.added_at = stock.added_at or _now()
    if stock.kind not in KINDS:
        stock.kind = COVER
    rest = [s for s in profile.stocks if s.symbol != stock.symbol]
    if len(rest) >= MAX_STOCKS:
        raise ValueError(f"커버 종목은 {MAX_STOCKS}개까지입니다.")
    profile.stocks = [*rest, stock]
    return profile


def remove_stock(profile: Profile, symbol: str) -> Profile:
    profile.stocks = [s for s in profile.stocks if s.symbol != symbol]
    return profile


def set_sectors(profile: Profile, sectors: list[str]) -> Profile:
    """맡은 섹터. 순서를 지키고 중복만 걷는다."""
    cleaned = [s.strip() for s in sectors if s and s.strip()]
    if len(cleaned) > MAX_SECTORS:
        raise ValueError(f"섹터는 {MAX_SECTORS}개까지입니다.")
    profile.sectors = list(dict.fromkeys(cleaned))
    return profile


def pin_peer(profile: Profile, card_id: str) -> Profile:
    """피어 그룹을 고정한다 (D68).

    **카드 id만 둔다.** 그룹 내용을 복제하면 카드를 고친 뒤 프로필이 옛말을
    하고, 그게 정확히 D51에서 밟은 실수다.
    """
    if card_id and card_id not in profile.pinned_peers:
        profile.pinned_peers.append(card_id)
    return profile


def unpin_peer(profile: Profile, card_id: str) -> Profile:
    profile.pinned_peers = [c for c in profile.pinned_peers if c != card_id]
    return profile
