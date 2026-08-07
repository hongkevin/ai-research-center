"""기사 골라내기 — **호출 한 번으로 쓸 만한 것만 남긴다.**

왜 필요한가
-----------
「삼성물산」으로 검색하면 하루에도 수십 건이 뜬다. 그대로 LLM에 넣으면 셋이
망가진다:

* **오래된 것** — 지난 실적 시즌 기사가 「최근 이슈」에 섞인다.
* **중복** — 같은 수주 발표를 열 매체가 받아쓴다. 프롬프트의 절반이 같은 말이다.
* **무관한 것** — 증권사 리포트 요약, 주가 등락 단신, 동명 회사.

토큰만 낭비하는 게 아니라 **문단 품질이 떨어진다.** 같은 사건이 열 번 나오면
LLM은 그게 제일 중요한 줄 안다.

API 호출 방어
-------------
네이버 검색 API는 일 25,000회이고 한 번에 최대 100건을 준다. 그래서
**호출은 종목당 한 번**이면 충분하다 — 100건을 최신순으로 받아 여기서 거른다.
페이지를 넘기지 않는다. 그리고 같은 종목을 하루에 여러 번 생성해도 캐시가
받아내므로 실제 호출은 종목·날짜당 1회다.

매체 화이트리스트를 **하드 필터로 쓰지 않는다** (실측으로 뒤집힘)
------------------------------------------------------------
처음에는 통신사·경제지만 남기려 했다. 삼성물산 100건으로 재보니 정반대였다:

* 제목에 회사명이 있는 11건이 **전부 목록 밖 매체**였다. 화이트리스트와
  AND로 걸면 **0건**이 된다.
* 정작 목록 안 매체가 준 것은 「코스피 급락」·「폭염 건설현장 점검」처럼
  회사와 무관한 시황·산업 기사였다.

**매체는 관련성을 대신하지 못한다.** 관련성은 「제목에 회사명이 있는가」가
가른다. 매체 목록은 대신 **중복 묶음의 대표를 고르는 데** 쓴다 — 같은 사건을
열 곳이 쓰면 연합뉴스 것을 남긴다. 그게 「모든 언론사가 중요하지는 않다」에
대한 답이면서 신호를 안 버리는 방법이다.

정렬은 왜 `sim`인가
-------------------
`sort=date`로 100건을 받으면 **대형주는 오늘 하루치**만 온다 — 삼성물산은
하루 100건이 넘는다. 3개월 창을 쓴다면서 실제로는 오늘만 보고 있었다.
실측: `date`는 제목매치 11/100, `sim`은 **80/100**. 날짜 필터는 그대로
상한으로 두고, 받아오는 순서는 정확도로 한다.
"""

from __future__ import annotations

import datetime as dt
import re
from urllib.parse import urlparse

from arc.data.base import NewsItem

# ── 매체 ─────────────────────────────────────────────────────────────
# 통신사·경제지·주요 일간지. **거르는 데 쓰지 않고**, 같은 사건을 여럿이
# 썼을 때 어느 것을 대표로 남길지 고르는 데 쓴다. 도메인으로 가른다 —
# 검색 API가 매체명을 안 준다.
_MAJOR = {
    # 통신사
    "yna.co.kr": "연합뉴스",
    "yonhapnews.co.kr": "연합뉴스",
    "newsis.com": "뉴시스",
    "news1.kr": "뉴스1",
    # 경제지
    "hankyung.com": "한국경제",
    "mk.co.kr": "매일경제",
    "sedaily.com": "서울경제",
    "mt.co.kr": "머니투데이",
    "edaily.co.kr": "이데일리",
    "fnnews.com": "파이낸셜뉴스",
    "asiae.co.kr": "아시아경제",
    "heraldcorp.com": "헤럴드경제",
    "biz.chosun.com": "조선비즈",
    "chosun.com": "조선일보",
    "joongang.co.kr": "중앙일보",
    "donga.com": "동아일보",
    "hani.co.kr": "한겨레",
    "khan.co.kr": "경향신문",
    "etnews.com": "전자신문",
    "thebell.co.kr": "더벨",
    "dt.co.kr": "디지털타임스",
    "ajunews.com": "아주경제",
    "newdaily.co.kr": "뉴데일리",
    "inews24.com": "아이뉴스24",
    "zdnet.co.kr": "지디넷코리아",
    "pharmnews.com": "팜뉴스",
    "hankookilbo.com": "한국일보",
    "segye.com": "세계일보",
    "kmib.co.kr": "국민일보",
    "munhwa.com": "문화일보",
    "seoul.co.kr": "서울신문",
    "imaeil.com": "매일신문",
    "kbs.co.kr": "KBS",
    "imnews.imbc.com": "MBC",
    "news.sbs.co.kr": "SBS",
    "ytn.co.kr": "YTN",
    "wowtv.co.kr": "한국경제TV",
    "mbn.co.kr": "MBN",
}

# ── 제목만 봐도 버릴 것 ──────────────────────────────────────────────
# 주가 단신과 증권사 리포트 요약은 「최근 이슈」가 아니다. 앞엣것은 우리가 쓸
# 정보가 없고, 뒤엣것은 **남의 목표주가**라 [D4](../../../docs/decisions.md#d4)에
# 정면으로 걸린다.
_NOISE = re.compile(
    r"(상한가|하한가|급등|급락|강세|약세|신고가|신저가|장중|시황|"
    r"상승 ?마감|하락 ?마감|보합|거래량|외국인|기관 ?순매수|순매도|"
    r"주가 ?(상승|하락|급등|급락|강세|약세|[↑↓])|'?주가'? ?[↑↓]|"
    # 「SK하이닉스 또 -8%↓」·「10프로 넘게 하락」 — 숫자에 붙은 등락 기호와
    # 구어체 등락 표현. 회사 사건이 아니라 그날의 주가다.
    r"\d\s?%?\s?[↓↑]|[↓↑]\s?\d|(프로|퍼센트|%)\s?넘게|"
    # 시황은 지수를 주어로 쓴다. 회사 사건 제목에는 지수 이름이 안 나온다.
    r"코스피|코스닥|나스닥|다우|S&P|증시|"
    # 개인투자자 대상 기사 — 「물타기」·「손절」·「수익률」
    r"물타기|손절|수익률|개미 ?투자|주주 ?게시판|"
    r"목표주가|목표가|투자의견|매수 ?추천|비중확대|커버리지 개시|추천 ?종목|유망주|"
    r"클릭 ?e ?종목|톱픽|Top ?Pick|리포트|보고서 ?발간|"
    r"부고|인사|동정|포토|사진|영상|\[표\]|\[표시\]|오늘의 운세)"
)

# 여러 회사를 한 줄에 늘어놓는 묶음 기사. 우리 회사 얘기가 아니라 목록이다.
# 「[제약 브리핑] 동국제약·차바이오F&C·부광약품·파마리서치」
# 「[코스닥 기관] 선택은 성장주… 알테오젠 에코프로 파마리서치」처럼 대괄호
# 머리말이 시장·수급이면 본문은 종목 나열이다.
_ROUNDUP = re.compile(
    r"(브리핑|이모저모|한눈에|종합\]|[가-힣]+ ?오늘\]|"
    # 대괄호 안 **어디든** 코스닥·코스피가 있으면 시장 기사다.
    # 실측: 「[주간 코스닥 기관]」이 `\[코스`로 시작하지 않아 빠져나갔다.
    r"\[[^\]]*코스(닥|피)[^\]]*\]|\[증시[^\]]*\]|\[특징주\]|"
    # **제목이 잘려 온다.** 네이버 검색 API가 「…[주식 초고…」처럼 대괄호
    # 안에서 끊어 주므로 `초고수`가 완성되지 않는다 — 실측으로 수급 기사가
    # 통과했다. 잘린 조각(`초고`)과 열린 대괄호 태그를 함께 본다.
    r"초고|고수의 ?선택|\[V ?차트\]|\[Who ?Is|\[인사\]|\[프로필\]|"
    r"\[주식[^\]]*$|\[증시[^\]]*$|던졌|쓸어담|담았다)"
)

# 증권사 리포트 요약. 두 가지 표기가 있다:
#   「IBK證 "파마리서치, …"」            — 매체가 증권사를 주어로 쓴 것
#   「노바렉스, … 성장 사이클 진입-NH」   — 제목 끝에 리포트 출처를 단 것
# **남의 목표주가를 우리 노트에 싣는 셈**이라 D4에 정면으로 걸린다.
_BROKER = re.compile(
    r"([가-힣A-Za-z]{2,}證|[가-힣A-Za-z]{2,}증권[,\s\"]|"
    r"[-–—]\s?[가-힣A-Za-z]{2,}(투자)?증권\s*$|[-–—]\s?리서치|"
    r"[-–—]\s?(NH|KB|DS|SK|IBK|한투|하나|신한|미래|삼성|키움|대신|유안타|메리츠)\s*$)"
)
_LISTY = re.compile(r"[가-힣A-Za-z]+·[가-힣A-Za-z]+·[가-힣A-Za-z]+")

# 사건을 가르는 데 쓸모없는 말. 어느 보도자료에나 있다.
_GENERIC = {
    "출시",
    "서비스",
    "확대",
    "공개",
    "개최",
    "진출",
    "발표",
    "참가",
    "도입",
    "선보여",
    "신규",
    "강화",
    "추진",
    "계획",
    "예정",
    "위해",
    "통해",
    "관련",
    "대상",
    "기업",
    "시장",
    "사업",
    "공급",
    "제품",
    "고객",
    "국내",
    "최초",
    "업계",
    "전략",
    "성장",
    "협업",
    "제휴",
    "경쟁",
    "검토",
    "공략",
    "첫선",
    "론칭",
    "확장",
    "고도화",
    "소개",
    "홍보",
    "진입",
    "분야",
    "제품군",
    "선정",
    "체결",
    "진행",
    "구축",
    "운영",
}

# 같은 사건으로 묶는 시간 창. 보도자료는 하루이틀 안에 몰린다.
_CLUSTER_DAYS = 7

# 제목 비교 전에 지우는 것 — 매체가 다르면 여기까지가 다르다
_DECORATION = re.compile(r"[\[\]\(\)【】〈〉<>“”\"'‘’·…,.!?~\-–—/|:;]+")
_STOPWORDS = {"기자", "종합", "속보", "단독", "1보", "2보", "3보", "영상", "포토"}

# 두 제목이 이만큼 겹치면 같은 사건으로 본다. 실측으로 조정한 값 —
# 0.5는 다른 사건을 묶었고, 0.8은 같은 수주 기사를 못 묶었다.
_SIMILARITY = 0.65


def _major(url: str) -> str | None:
    """주요 매체면 이름, 아니면 None."""
    host = urlparse(url).netloc.lower().removeprefix("www.")
    parts = host.split(".")
    for i in range(len(parts) - 1):
        name = _MAJOR.get(".".join(parts[i:]))
        if name is not None:
            return name
    return None


def press_name(url: str) -> str:
    """URL → 표시할 매체 이름. 모르는 곳은 도메인을 그대로 쓴다.

    **모른다고 버리지 않는다.** 「삼성물산, 소방시설공사 시공능력평가 1위
    탈환」이 목록 밖 매체라는 이유로 버려졌던 자리다.
    """
    return _major(url) or urlparse(url).netloc.lower().removeprefix("www.")


def plain_name(company: str) -> str:
    """DART 법인명 → **기사에 쓰이는 이름**. `(주)파마리서치` → `파마리서치`.

    검색어에도 이걸 쓴다. 법인 표기를 붙인 채로 검색하면 결과가 줄어든다 —
    실측: `(주)파마리서치`로 검색하니 거른 뒤 3건, `파마리서치`는 10건이었다.
    기사는 법인 표기를 쓰지 않는다.
    """
    # **긴 것부터.** `\(?주\)?`를 앞에 두면 「주식회사 노바렉스」의 맨 앞 「주」가
    # 먼저 걸려 「식회사 노바렉스」가 된다.
    return re.sub(r"주식회사|㈜|\(주\)|\(株\)", "", company).strip()


def event_tokens(title: str, company: str = "") -> set[str]:
    """제목 → **사건을 가리키는 말**만. 회사명과 상투어는 뺀다.

    회사명은 모든 제목에 있어서 사건을 못 가른다. 「출시」·「확대」 같은
    보도자료 상투어도 마찬가지다. 남는 것은 대개 고유명사다 — `홈닉`,
    `자이너`, `에잇세컨즈`. 그게 사건의 이름이다.
    """
    plain = _DECORATION.sub(" ", title.replace(company, " ") if company else title)
    return {w for w in plain.split() if len(w) >= 2 and w not in _GENERIC and not w.isdigit()}


def is_noise(title: str) -> bool:
    """제목만으로 버릴 기사인가 — 주가·수급 단신, **증권사 리포트**, 나열 기사."""
    if _NOISE.search(title) or _BROKER.search(title):
        return True
    return bool(_ROUNDUP.search(title)) or bool(_LISTY.search(title))


def select(
    items: list[NewsItem],
    *,
    now: dt.datetime,
    months: int = 3,
    limit: int = 10,
    company: str | None = None,
) -> list[NewsItem]:
    """쓸 기사만. **버리는 쪽이 기본이다.**

    1. **날짜** — 기본 3개월, 6개월을 넘기지 않는다.
    2. **제목에 회사명** — 관련성의 실질적 기준. 본문에만 있으면 보통
       「삼성물산·GS건설·현대건설이…」식 나열이거나 시황 기사다.
    3. **소음** — 주가·수급 단신, 남의 목표주가·추천, 여러 회사 나열 기사.
    4. **중복** — 사건 토큰이 **하나라도** 겹치고 7일 안이면 같은 사건.
       대표는 주요 매체 것으로 고른다.

    4번의 「하나라도」가 실측의 결론이다. 한국어 제목은 어미와 어순이 매체마다
    달라 단어 겹침 비율이 안 통한다 — 「홈닉 세차·반려동물 케어까지」와
    「건설부문, 홈플랫폼 '홈닉' 생활편의 서비스 4종 출시」는 겹침이 0.4다.
    반면 상투어를 걷어내고 나면 **같은 사건은 같은 고유명사를 쓴다.**
    실측(삼성물산 80건): 2개 기준 16건에 홈닉 기사가 4건 남았고, 1개 기준
    11건에 1건으로 접혔다.
    """
    cutoff = now - dt.timedelta(days=30 * min(months, 6))
    plain = plain_name(company or "")

    fresh: list[NewsItem] = []
    for item in items:
        if item.published_at is None or item.published_at < cutoff:
            continue
        if plain and plain not in item.title:
            continue
        if is_noise(item.title):
            continue
        fresh.append(item)

    # 주요 매체를 먼저 보게 해서 그쪽이 묶음의 대표가 되게 한다.
    fresh.sort(
        key=lambda x: (
            _major(x.url) is None,
            -(x.published_at or dt.datetime.min.replace(tzinfo=dt.UTC)).timestamp(),
        )
    )
    picked: list[NewsItem] = []
    seen: list[tuple[set[str], dt.datetime]] = []
    for item in fresh:
        tokens = event_tokens(item.title, plain)
        when = item.published_at
        if any(tokens & prev and abs((when - at).days) <= _CLUSTER_DAYS for prev, at in seen):
            continue
        seen.append((tokens, when))
        picked.append(item)
        if len(picked) >= limit:
            break

    picked.sort(
        key=lambda x: x.published_at or dt.datetime.min.replace(tzinfo=dt.UTC), reverse=True
    )
    return picked
