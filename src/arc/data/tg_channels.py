"""추천 텔레그램 채널 — **고르게 하되, 실측한 것만 준다.**

왜 필요한가
-----------
지금 볼 채널 목록은 **내가 이미 들어가 있는 방**뿐이다. 그런데 인터뷰의 고통이
정확히 *"어떤 종목을 봐야 할지 모르겠다"*였고, 채널도 같다 — 무엇을 구독해야
하는지 모르면 빈 목록이 계속 빈 목록이다. [섹터 시드](sectors.py)와 같은
문제이고 같은 답이다: **정답이 아니라 출발점을 준다.**

무엇을 담는가
-------------
**전부 `arc telegram check`로 직접 확인했다.** 조사로 모은 이름은 지어낸 것이
섞이고, 실제로 셋(`@sangsangsmallcap`·`@kyobo_chem`·`@EugeneResearch`)은
존재하지 않았다. 붙여 보기 전에 걸러야 한다.

**죽은 채널은 안 싣는다.** 구독자 수만 보면 시체를 잡는다 — 1,577명짜리
「SK 신성장산업분석팀」이 1,208일째 정지고, 20,437명짜리도 8개월째 멈춰 있었다.
마지막 글이 30일을 넘으면 여기 안 들어온다.

**`BLOCKED`가 이 파일의 절반이다.** 왜 뺐는지를 남기지 않으면 다음에 같은
조사를 하고 같은 시체를 다시 줍는다. 특히 `@nhsemicon`은 단순 사망이 아니라
**사칭**이다.

구독자 수는 스냅숏이다
----------------------
`CHECKED_AT` 시점의 값이고 매일 변한다. 순서를 정하는 데만 쓰고 화면에
「지금 몇 명」이라고 적지 않는다 — 그건 우리가 보증할 수 없는 숫자다.
"""

from __future__ import annotations

from dataclasses import dataclass

# 아래 값들을 확인한 날. 구독자 수는 이 시점의 스냅숏이다.
CHECKED_AT = "2026-08-08"

# 마지막 글이 이보다 오래되면 죽은 것으로 본다.
STALE_DAYS = 30


@dataclass(frozen=True)
class Recommended:
    """추천 채널 하나. **username이 열쇠다** — 안 들어가 있어도 이걸로 찾는다."""

    username: str
    title: str
    kind: str  # broker · research · news · ir
    sector: str  # 어느 담당에게 쓸모 있는가. 빈 값이면 범용
    subscribers: int  # CHECKED_AT 시점 스냅숏
    note: str = ""


# **증권사 공식이 먼저다.** 소속이 드러나 있으면 틀렸을 때 책임 소재가 있고,
# 컴플라이언스를 거친 글이라 인용해도 덜 위험하다. 그다음이 규모다.
RECOMMENDED: tuple[Recommended, ...] = (
    # ── 증권사 소속이 드러난 채널 ────────────────────────────────────
    Recommended("merITz_tech", "메리츠 Tech 김선우·양승수·김동관", "broker", "반도체", 23_135),
    Recommended("skitteam", "IT는 SK — SK증권 IT팀", "broker", "반도체", 17_589),
    Recommended("cjdbj", "미래에셋 배터리/디스플레이 김철중", "broker", "2차전지·소재", 15_987),
    Recommended("KISemicon", "한투 테크팀 채민숙 외", "broker", "반도체", 14_730),
    Recommended("HanaResearchTelecom", "하나증권 통신 김홍식", "broker", "통신", 10_237),
    Recommended("kyobofnbcosmetic", "교보 음식료/화장품 권우정", "broker", "음식료·담배", 5_076),
    Recommended(
        "unokim88",
        "IBK 김운호 — IT 하드웨어",
        "broker",
        "반도체",
        3_762,
        note="반도체·디스플레이·부품",
    ),
    Recommended("retail_analyst", "대신증권 유통/의류 유정현", "broker", "유통·의류", 2_702),
    Recommended("shinhanconsumer", "신한 리서치본부 화장품/의복", "broker", "화장품", 939),
    Recommended("TechInventory", "iM증권 전기전자 고의영", "broker", "전기전자", 205),
    # ── 규모가 커서 대중의 신뢰를 받는 비공식 리서치 ─────────────────
    Recommended("bornlupin", "루팡", "research", "", 46_237),
    Recommended("Brain_And_Body_Research", "Brain and Body Research", "research", "", 35_383),
    Recommended("aetherjapanresearch", "에테르의 일본&미국 리서치", "research", "", 33_646),
    Recommended("kkkontemp", "KK Kontemporaries", "research", "반도체", 18_322),
    Recommended("hslpartners", "IT의 신 이형수", "research", "반도체", 17_553),
    Recommended("yaza_stock", "야자반 — Y.Z. stock", "research", "반도체", 13_159),
    Recommended("anakinvest", "엄브렐라리서치 Anakin의 투자노트", "research", "반도체", 9_663),
    Recommended(
        "Barbarian_Global_Tech", "BK Tech Insight — 바바리안 리서치", "research", "반도체", 9_100
    ),
    Recommended(
        "ejpark3312",
        "#Beautylog",
        "research",
        "화장품",
        7_841,
        # **소속을 단정하지 않는다.** 이니셜과 링크 정황만으로는 증권사 채널이라고
        # 말할 수 없다 — 채널 소개 어디에도 소속이 없다.
        note="소속 미확인 — 증권사 채널로 단정하지 마십시오",
    ),
    Recommended("molru", "몰?루", "research", "반도체", 3_617),
    Recommended("DrDtech", "D의 테크 투자", "research", "반도체", 2_131),
    # ── 뉴스 큐레이션 ────────────────────────────────────────────────
    Recommended(
        "technthecity",
        "강해령의 테크앤더시티 (한경)",
        "news",
        "반도체",
        7_890,
        note="옛 채널 @bandocheEXPgo는 2025-12에 멈췄습니다",
    ),
    Recommended("semiconnews", "반도체 뉴스룸 (현직자)", "news", "반도체", 88),
    # ── 기업 IR ─────────────────────────────────────────────────────
    Recommended("caregenirpr", "케어젠 IR/PR 공식", "ir", "제약·바이오", 1_099),
)


# **왜 뺐는지를 남긴다.** 안 남기면 다음에 같은 조사를 하고 같은 시체를 줍는다.
BLOCKED: dict[str, str] = {
    # 사칭 — 이건 단순 사망이 아니다
    "nhsemicon": (
        "⚠️ 사칭. NH 반도체 채널로 보이지만 실체는 구독자 2명짜리 그룹이고, "
        "유명 채널 username들을 방 이름에 나열해 검색에 걸리게 만들었습니다."
    ),
    # 존재하지 않음 — 조사 결과에 지어낸 이름이 섞인다
    "sangsangsmallcap": "존재하지 않는 username입니다.",
    "kyobo_chem": "존재하지 않는 username입니다.",
    "EugeneResearch": "존재하지 않는 username입니다.",
    # 정지
    "smallcapsk": "SK 신성장산업분석팀 — 2023-04 이후 정지.",
    "retailyh": "메리츠 유통 최윤희 — 2022-04 이후 정지.",
    "arcas_archive": "작은곰자리 아카이브 — 2024-08 이후 정지.",
    "LSsecTech": "2026-01에 채널명을 바꾸고 프로필을 지운 뒤 정지.",
    "chemtronics": "켐트로닉스 개인채널 — 2026-01 이후 정지.",
    "bandocheEXPgo": "강해령 기자의 옛 채널 — 현행은 @technthecity 입니다.",
    "consumer_sojung": "키움 조소정 — 2026-02 이후 글이 없습니다.",
    "irgoirgo": "[IR KUDOS] — 2026-07-03 이후 글이 없습니다.",
    "nudgetech": "상상인증권 정민규 — 2026-07-02 이후 글이 없습니다.",
    "YSKoreaSemi": (
        "유안타 백길현 — 출산휴가로 컴플라이언스상 운영 중단. "
        "9월 초 재개 예정이라 죽은 채널은 아닙니다."
    ),
}


def recommended_for(sectors: list[str] | None = None) -> list[Recommended]:
    """내 섹터에 맞는 것이 먼저, 그다음 증권사, 그다음 규모.

    **증권사가 규모보다 앞이다.** 소속이 드러나 있으면 틀렸을 때 책임 소재가
    있고 컴플라이언스를 거친 글이라, 46,000명짜리 익명 채널보다 205명짜리
    담당 애널리스트 채널이 RA에게 먼저다.

    섹터가 안 맞아도 **빼지 않는다** — 순서만 뒤로 간다. 내 섹터 밖 채널이
    쓸모없는 것은 아니다.
    """
    want = set(sectors or [])
    return sorted(
        RECOMMENDED,
        key=lambda c: (c.sector not in want, c.kind != "broker", -c.subscribers),
    )


def blocked_reason(username: str) -> str:
    """이 username을 왜 안 권하는가. 모르면 빈 문자열."""
    key = username.lstrip("@")
    for name, reason in BLOCKED.items():
        if name.lower() == key.lower():
            return reason
    return ""
