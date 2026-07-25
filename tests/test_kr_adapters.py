"""금융위 시세·네이버 뉴스 어댑터 파싱 테스트 — 네트워크 호출 없음."""

import datetime as dt

from arc.data.kr.krx_price import KrxPriceProvider
from arc.data.kr.naver_news import NaverNewsProvider, _clean

NOW = dt.datetime(2026, 7, 25, 12, 0, tzinfo=dt.timezone.utc)


def test_parse_price_items_single_dict():
    """data.go.kr 특성: 결과 1건이면 item이 list가 아니라 dict로 온다."""
    body = {
        "items": {
            "item": {
                "basDt": "20260724",
                "srtnCd": "078890",
                "clpr": "5150",
                "mkp": "5100",
                "hipr": "5200",
                "lopr": "5050",
                "trqu": "123456",
                "mrktTotAmt": "98765432100",
                "lstgStCnt": "19178530",
            }
        },
        "totalCount": 1,
    }
    points = KrxPriceProvider.parse_price_items(body, retrieved_at=NOW)
    assert len(points) == 1
    p = points[0]
    assert p.close == 5150.0
    assert p.date == dt.date(2026, 7, 24)
    assert p.market_cap == 98_765_432_100
    assert p.provenance.source == "krx_price"


def test_parse_price_items_handles_missing_fields():
    body = {"items": {"item": [{"basDt": "20260724", "srtnCd": "078890", "clpr": "5150"}]}}
    p = KrxPriceProvider.parse_price_items(body, retrieved_at=NOW)[0]
    assert p.open is None and p.volume is None


def test_naver_clean_strips_highlight_tags():
    assert _clean("<b>가온그룹</b> 실적") == "가온그룹 실적"
    assert _clean("A &amp; B") == "A & B"


def test_parse_news():
    payload = {
        "items": [
            {
                "title": "<b>가온그룹</b>, 2분기 실적 발표",
                "description": "매출 <b>증가</b>…",
                "originallink": "https://news.example.com/1",
                "link": "https://n.news.naver.com/1",
                "pubDate": "Fri, 24 Jul 2026 09:30:00 +0900",
            }
        ]
    }
    items = NaverNewsProvider.parse_news(payload, retrieved_at=NOW)
    assert items[0].title == "가온그룹, 2분기 실적 발표"
    assert items[0].url == "https://news.example.com/1"  # originallink 우선
    assert items[0].published_at.tzinfo is not None
    assert items[0].snippet == "매출 증가…"
