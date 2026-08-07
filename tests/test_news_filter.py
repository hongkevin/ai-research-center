"""기사 골라내기.

「삼성물산」으로 검색하면 100건이 온다. 그대로 넣으면 프롬프트의 절반이 같은
수주 기사고, 지난 실적 시즌 기사가 「최근 이슈」에 섞이고, 남의 목표주가가
따라 들어온다([D4](../docs/decisions.md#d4)).

버리는 쪽이 기본이다. 이 파일은 **무엇을 버리는지**를 고정한다.
"""

from __future__ import annotations

import datetime as dt

from arc.data.base import NewsItem, Provenance
from arc.data.kr.news_filter import event_tokens, is_noise, plain_name, press_name, select

NOW = dt.datetime(2026, 8, 6, tzinfo=dt.UTC)
PROV = Provenance(source="naver_news", retrieved_at=NOW)


def _item(title: str, *, url: str, days_ago: int = 1, snippet: str = "") -> NewsItem:
    return NewsItem(
        title=title,
        snippet=snippet or title,
        url=url,
        published_at=NOW - dt.timedelta(days=days_ago),
        provenance=PROV,
    )


class TestPress:
    def test_knows_the_majors(self):
        assert press_name("https://www.yna.co.kr/view/AKR1") == "연합뉴스"
        assert press_name("https://www.hankyung.com/article/1") == "한국경제"
        assert press_name("https://biz.chosun.com/x/") == "조선비즈"

    def test_subdomains_roll_up(self):
        assert press_name("https://land.mk.co.kr/news/1") == "매일경제"

    def test_unknown_press_is_shown_not_dropped(self):
        """**모른다고 버리지 않는다.** 실측으로 뒤집힌 자리 — 제목에 회사명이
        있는 기사가 전부 목록 밖 매체였다. 매체는 관련성을 대신하지 못한다."""
        assert press_name("https://some-local-paper.co.kr/a/1") == "some-local-paper.co.kr"


class TestNoise:
    def test_price_blurbs_are_noise(self):
        assert is_noise("삼성물산, 장중 52주 신고가")
        assert is_noise("[특징주] 삼성물산 급등")

    def test_other_houses_targets_are_noise(self):
        """남의 목표주가를 실으면 D4가 금지한 것을 우회해서 싣는 셈이다."""
        assert is_noise("삼성물산 목표주가 상향")
        assert is_noise("A증권, 삼성물산 투자의견 매수 유지")

    def test_price_close_blurbs_are_noise(self):
        assert is_noise("파마리서치 주가, 8월 3일 372,000원 9.90% 상승 마감")
        assert is_noise("[코스닥 외국인] 파마리서치·마키나락스 담고 대한광통신 팔아")

    def test_market_commentary_is_noise(self):
        """시황은 지수를 주어로 쓴다. 회사 사건 제목에는 지수 이름이 안 나온다."""
        assert is_noise("SK하이닉스 또 -8%↓…코스피 6400선 무너졌다")
        assert is_noise("SK하이닉스, 10프로 넘게 하락")

    def test_truncated_bracket_tags_are_caught(self):
        """**제목이 잘려 온다.** 네이버 검색 API가 「…[주식 초고…」처럼
        대괄호 안에서 끊어 주므로 `초고수`가 완성되지 않는다 — 실측으로
        수급 기사가 통과했다."""
        assert is_noise("이틀 연속 SK하이닉스 던졌다…삼성전기는 매수 [주식 초고...")
        assert is_noise("오늘의 매매 [증시 브리")

    def test_real_news_still_survives_the_wider_net(self):
        """넓히면서 정상 기사를 잡으면 안 된다."""
        for title in (
            "삼성전기 베트남, AI 기판 생산라인 2.4배 확대 나섰다",
            "삼성물산, 카타르 플랜트 수주",
            "삼성전기·LG이노텍, 자율주행 넘어 휴머노이드 눈 잡는다",
        ):
            assert not is_noise(title), title

    def test_multi_company_roundups_are_noise(self):
        """여러 회사를 늘어놓는 묶음 기사는 우리 회사 얘기가 아니라 목록이다."""
        assert is_noise("[제약 브리핑] 동국제약·차바이오·부광약품·파마리서치")
        assert is_noise("[의료산업 이모저모] 파마리서치, 산부인과 창상피복재")

    def test_real_events_survive(self):
        assert not is_noise("삼성물산, 카타르 플랜트 수주")
        assert not is_noise("삼성물산 바이오 4공장 증설 착수")
        assert not is_noise("삼성물산, 2년 만에 소방시설공사 시공능력평가 1위 탈환")


class TestEventTokens:
    def test_strips_the_company_and_boilerplate(self):
        """남는 것은 대개 고유명사다 — 그게 사건의 이름이다."""
        got = event_tokens("삼성물산 홈닉, 청소 세차 펫케어 등 생활서비스 4종 출시", "삼성물산")
        assert "홈닉" in got
        assert "삼성물산" not in got and "출시" not in got

    def test_same_event_shares_a_proper_noun(self):
        """한국어 제목은 어미·어순이 매체마다 다르다. 겹치는 것은 고유명사다."""
        a = event_tokens("삼성물산 홈닉, 세차 반려동물 케어까지", "삼성물산")
        b = event_tokens("삼성물산 건설부문, 홈플랫폼 홈닉 생활편의 서비스 4종 출시", "삼성물산")
        assert a & b == {"홈닉"}


class TestSelect:
    def test_drops_articles_older_than_the_window(self):
        items = [
            _item("삼성물산 수주 발표", url="https://www.yna.co.kr/1", days_ago=10),
            _item("삼성물산 옛날 기사", url="https://www.yna.co.kr/2", days_ago=200),
        ]
        got = select(items, now=NOW, months=3, company="삼성물산")
        assert [x.title for x in got] == ["삼성물산 수주 발표"]

    def test_window_never_exceeds_six_months(self):
        """`months=24`를 줘도 6개월에서 끊는다 — 「최근」이 아니게 된다."""
        items = [_item("삼성물산 아주 옛날", url="https://www.yna.co.kr/1", days_ago=300)]
        assert select(items, now=NOW, months=24, company="삼성물산") == []

    def test_collapses_the_same_story_across_outlets(self):
        """같은 수주를 열 매체가 받아쓴다. 하나만 남긴다."""
        items = [
            _item("삼성물산, 카타르서 플랜트 수주", url="https://www.yna.co.kr/1"),
            _item("[단독] 삼성물산 카타르 플랜트 수주", url="https://www.hankyung.com/2"),
            _item("삼성물산 카타르 플랜트 수주 성공", url="https://www.mk.co.kr/3"),
        ]
        assert len(select(items, now=NOW, company="삼성물산")) == 1

    def test_collapses_even_when_wording_differs(self):
        """실측으로 놓쳤던 자리 — 단어 겹침 비율로는 0.4라 안 묶였다."""
        items = [
            _item("삼성물산 홈닉, 세차 반려동물 케어까지", url="https://www.yna.co.kr/1"),
            _item(
                "삼성물산 건설부문, 홈플랫폼 홈닉 생활편의 서비스 4종 출시",
                url="https://www.mk.co.kr/2",
            ),
            _item(
                "아파트 앱으로 세차 청소까지 삼성물산 홈닉 서비스 확대",
                url="https://www.hankyung.com/3",
            ),
        ]
        assert len(select(items, now=NOW, company="삼성물산")) == 1

    def test_the_same_keyword_months_apart_is_a_new_event(self):
        """7일 창을 넘으면 다른 사건이다 — 같은 제품의 후속 발표일 수 있다."""
        items = [
            _item("삼성물산 홈닉 신규 기능", url="https://www.yna.co.kr/1", days_ago=1),
            _item("삼성물산 홈닉 신규 기능", url="https://www.mk.co.kr/2", days_ago=40),
        ]
        assert len(select(items, now=NOW, company="삼성물산")) == 2

    def test_keeps_genuinely_different_stories(self):
        items = [
            _item("삼성물산, 카타르서 플랜트 수주", url="https://www.yna.co.kr/1"),
            _item("삼성물산 바이오 공장 증설 착수", url="https://www.mk.co.kr/2"),
        ]
        assert len(select(items, now=NOW, company="삼성물산")) == 2

    def test_requires_the_company_in_the_title(self):
        """**본문에만 있으면 버린다.** 실측: 「코스피 4%대 급락」·「폭염
        건설현장 점검」이 요약에 회사명이 있다는 이유로 통과했다. 그런 기사는
        회사 얘기가 아니라 나열이다."""
        items = [
            _item("코스피 4%대 하락", url="https://www.yna.co.kr/1", snippet="삼성물산 등 하락"),
            _item("삼성물산 수주", url="https://www.yna.co.kr/2"),
        ]
        got = select(items, now=NOW, company="삼성물산(주)")
        assert [x.title for x in got] == ["삼성물산 수주"]

    def test_major_outlet_represents_a_duplicate_cluster(self):
        """같은 사건을 열 곳이 쓰면 **연합뉴스 것을 남긴다.**

        이게 「모든 언론사가 중요하지는 않다」에 대한 답이다 — 신호를 버리지
        않으면서 대표만 고른다."""
        items = [
            _item("삼성물산 홈닉 생활서비스 4종 출시", url="https://some-blog.co.kr/1"),
            _item("삼성물산 홈닉, 생활서비스 4종 출시", url="https://www.yna.co.kr/2"),
        ]
        got = select(items, now=NOW, company="삼성물산")
        assert len(got) == 1
        assert press_name(got[0].url) == "연합뉴스"

    def test_plain_name_drops_the_legal_form(self):
        """검색어에도 쓴다 — 실측: `(주)파마리서치`로 검색하면 거른 뒤 3건,
        `파마리서치`는 10건이었다. 기사는 법인 표기를 안 쓴다."""
        assert plain_name("(주)파마리서치") == "파마리서치"
        assert plain_name("삼성물산(주)") == "삼성물산"
        assert plain_name("주식회사 노바렉스") == "노바렉스"

    def test_legal_form_in_the_company_name_is_ignored(self):
        """DART는 `삼성물산(주)`를 주지만 기사에는 그렇게 안 쓴다."""
        items = [_item("삼성물산 수주", url="https://www.yna.co.kr/1")]
        assert len(select(items, now=NOW, company="삼성물산(주)")) == 1

    def test_returns_newest_first(self):
        items = [
            _item("삼성물산 오래된 소식", url="https://www.yna.co.kr/1", days_ago=30),
            _item("삼성물산 어제 소식", url="https://www.mk.co.kr/2", days_ago=1),
        ]
        got = select(items, now=NOW, company="삼성물산")
        assert got[0].title == "삼성물산 어제 소식"

    def test_respects_the_limit(self):
        # 제목이 서로 충분히 달라야 한다 — 안 그러면 중복 제거가 먼저 먹는다.
        # (처음 쓴 테스트가 `사건0`·`사건1`이라 한 건으로 접혔다. 필터가 옳았다.)
        topics = [
            "카타르 플랜트 수주",
            "바이오 공장 증설 착수",
            "주주총회 정관 변경 의결",
            "리조트 부문 매각 검토",
            "패션 브랜드 신규 출점",
            "건설 자회사 합병 추진",
            "해외 법인 설립 인가",
        ]
        items = [
            _item(f"삼성물산 {topic}", url=f"https://www.yna.co.kr/{i}", days_ago=i + 1)
            for i, topic in enumerate(topics)
        ]
        assert len(select(items, now=NOW, limit=5, company="삼성물산")) == 5

    def test_items_without_a_date_are_dropped(self):
        """날짜가 없으면 「최근」인지 판정할 수 없다."""
        item = NewsItem(
            title="삼성물산 수주",
            snippet="x",
            url="https://www.yna.co.kr/1",
            published_at=None,
            provenance=PROV,
        )
        assert select([item], now=NOW, company="삼성물산") == []
