"""매크로 — 환율·금리. 한국은행 ECOS.

왜 필요한가
-----------
브리프의 왼쪽 칸이 **시장 매크로**다. 금융위 시세 API에는 지수밖에 없다 —
원/달러도, 국고채도, 기준금리도 없다. 증권사 모닝 미팅이 매일 첫 줄에 놓는
숫자가 정확히 이 셋인데 우리한테는 출처가 없었다.

ECOS는 한국은행이 직접 내는 원본이고, 키는 이메일만 넣으면 즉시 나오고,
이용에 제한이 없다. **환율·금리에 관해서는 이보다 위가 없다.**

**값마다 자기 날짜를 달고 다닌다**
----------------------------------
계열마다 채워지는 속도가 다르다. 지수는 어제 것이 있는데 환율이 일주일 전
것이면, 나란히 놓는 순간 사람은 둘 다 어제 것으로 읽는다. 그래서 `Point`가
`date`를 들고 다니고 브리프가 그 날짜를 그대로 찍는다.

**여기서 날짜를 추정하지 않는다.** 「7/31 값이니 8/6도 비슷할 것」은 우리가
할 말이 아니다. 못 받은 날은 못 받았다고 한다.

**기준금리는 계단이고, 일별을 쓰면 안 된다**
--------------------------------------------
환율·금리는 매일 움직이지만 기준금리는 금통위가 바꿀 때만 바뀐다. 그래서
「전일 대비」가 늘 0.00이고, 그 0.00은 정보가 아니다. 대신 **마지막으로 바뀐
때**를 들고 다닌다.

일별(`D`) 계열로 받으면 **틀린 값이 나온다.** ECOS `StatisticSearch`는 창의
앞에서부터 최대 행수만큼 잘라 주기 때문에, 매일 같은 값이 반복되는 계열은
행수 한도에 먼저 걸려 **최신이 아니라 중간이 잘려 나온다** — 실제로 2.50에서
멈춰 2026-07의 2.75 인상을 놓쳤다. 월별(`M`)은 2년치가 31행이라 잘리지
않는다. **정책금리에 일별 해상도는 애초에 필요 없다.**
"""

from __future__ import annotations

import datetime as dt
import logging
import os
from dataclasses import dataclass

import httpx

log = logging.getLogger("arc.data.kr.ecos")

BASE_URL = "https://ecos.bok.or.kr/api"


@dataclass(frozen=True)
class Series:
    """볼 계열 하나. **표·주기·항목을 코드로 박아 둔다** — 실측으로 찾았다."""

    key: str
    label: str
    table: str
    item: str
    cycle: str = "D"
    unit: str = ""
    # 소수 몇 자리로 보일 것인가. 환율은 1원 단위, 금리는 0.01%p 단위가 실무다.
    digits: int = 2
    # 계단형인가 — 기준금리처럼 정책이 바꿀 때만 움직이는 계열
    step: bool = False


# **넷이면 된다.** 모닝 미팅이 첫 줄에 놓는 것이 원/달러·국고채 3년·10년·
# 기준금리다. 더 넣으면 왼쪽 칸이 길어져 정작 볼 것을 못 본다.
MACRO: tuple[Series, ...] = (
    Series("usdkrw", "원/달러", "731Y001", "0000001", unit="원", digits=1),
    Series("kr3y", "국고채 3년", "817Y002", "010200000", unit="%", digits=3),
    Series("kr10y", "국고채 10년", "817Y002", "010210000", unit="%", digits=3),
    # **월별이다.** 일별은 행수 한도에 잘려 최신을 놓친다 — 위 주석 참조
    Series("base_rate", "기준금리", "722Y001", "0101000", cycle="M", unit="%", digits=2, step=True),
)


class EcosError(Exception):
    pass


@dataclass
class Point:
    """한 계열의 최신값 + **그 값이 언제 것인지**."""

    key: str
    label: str
    value: float
    date: str  # YYYYMMDD (또는 계열 주기에 맞는 TIME)
    unit: str = ""
    digits: int = 2
    # 직전 관측 대비. **어제 대비가 아니다** — 계열이 늦으면 직전도 늦다
    prev: float | None = None
    prev_date: str = ""
    # 계단형 계열이 마지막으로 바뀐 시점. 기준금리에만 찬다
    changed_at: str = ""

    @property
    def change(self) -> float | None:
        """직전 관측 대비 변화. 환율은 원, 금리는 %p — **퍼센트가 아니다.**"""
        return None if self.prev is None else self.value - self.prev

    @property
    def display(self) -> str:
        return f"{self.value:,.{self.digits}f}{self.unit}"

    @property
    def stale_days(self) -> int | None:
        """오늘로부터 며칠 늦은 값인가. **일별이 아니면 None** — 월별
        계열에 「며칠 늦었다」를 붙이면 늘 늦은 것처럼 보인다."""
        try:
            day = dt.date(int(self.date[:4]), int(self.date[4:6]), int(self.date[6:8]))
        except (ValueError, IndexError):
            return None
        return (dt.datetime.now(dt.UTC).date() - day).days


def _key(api_key: str | None = None) -> str:
    key = api_key or os.environ.get("ECOS_API_KEY", "")
    if not key:
        raise EcosError("ECOS_API_KEY가 없습니다 — https://ecos.bok.or.kr/api 에서 받으십시오")
    return key


def _rows(
    series: Series,
    start: str,
    end: str,
    *,
    api_key: str | None = None,
    client: httpx.Client | None = None,
    limit: int = 200,
) -> list[dict]:
    """`StatisticSearch` 원본 행. **비어 있으면 빈 목록이지 예외가 아니다.**

    ECOS는 자료가 없을 때 `RESULT.CODE = INFO-200`을 준다 — 오류가 아니라
    「해당 기간에 없다」는 뜻이고, 늦게 채워지는 계열에서는 정상이다.
    """
    url = (
        f"{BASE_URL}/StatisticSearch/{_key(api_key)}/json/kr/1/{limit}"
        f"/{series.table}/{series.cycle}/{start}/{end}/{series.item}"
    )
    own = client is None
    client = client or httpx.Client(timeout=20.0)
    try:
        res = client.get(url)
        res.raise_for_status()
        body = res.json()
    except httpx.HTTPError as exc:
        raise EcosError(f"ECOS 호출 실패 ({series.label}): {exc}") from exc
    finally:
        if own:
            client.close()

    if "RESULT" in body:
        code = body["RESULT"].get("CODE", "")
        if code == "INFO-200":  # 해당 기간에 자료 없음 — 정상이다
            return []
        raise EcosError(f"ECOS 거부 ({series.label}): {body['RESULT'].get('MESSAGE', code)}")

    rows = body.get("StatisticSearch", {}).get("row", [])
    out = []
    for r in rows:
        raw = r.get("DATA_VALUE")
        if raw in (None, "", "-"):
            continue  # 휴일·미공표. **0으로 채우지 않는다**
        try:
            out.append({"time": str(r.get("TIME", "")), "value": float(raw)})
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda x: x["time"])
    return out


def fetch_point(
    series: Series,
    *,
    today: dt.date | None = None,
    lookback: int = 60,
    api_key: str | None = None,
    client: httpx.Client | None = None,
) -> Point | None:
    """계열 하나의 최신 관측. **없으면 None이다** — 가짜 값을 만들지 않는다.

    `lookback`이 60일인 이유: ECOS 일별은 며칠 늦고 연휴가 끼면 더 늦는다.
    계단형(기준금리)은 마지막 변경일을 찾아야 해서 창이 더 필요하다.
    """
    today = today or dt.datetime.now(dt.UTC).date()
    # 계단형은 마지막 변경일을 찾아야 해서 창이 넓어야 한다. 월별이라 넓혀도
    # 행수가 얼마 안 된다 — 3년이 36행이다.
    span = lookback * 18 if series.step else lookback
    start = today - dt.timedelta(days=span)

    fmt = "%Y%m%d" if series.cycle == "D" else "%Y%m"
    rows = _rows(
        series,
        start.strftime(fmt),
        today.strftime(fmt),
        api_key=api_key,
        client=client,
    )
    if not rows:
        return None

    last = rows[-1]
    point = Point(
        key=series.key,
        label=series.label,
        value=last["value"],
        date=last["time"],
        unit=series.unit,
        digits=series.digits,
    )
    if len(rows) >= 2:
        point.prev = rows[-2]["value"]
        point.prev_date = rows[-2]["time"]

    if series.step:
        # **마지막으로 값이 바뀐 날.** 계단형에서 「전일 대비 0.00」은
        # 정보가 아니고, RA가 알고 싶은 것은 「언제 올렸나」다.
        point.prev = None
        point.prev_date = ""
        for older, newer in zip(reversed(rows[:-1]), reversed(rows[1:]), strict=True):
            if older["value"] != newer["value"]:
                point.changed_at = newer["time"]
                point.prev = older["value"]
                point.prev_date = older["time"]
                break
    return point


def fetch_macro(
    *,
    series: tuple[Series, ...] = MACRO,
    today: dt.date | None = None,
    api_key: str | None = None,
    client: httpx.Client | None = None,
) -> list[Point]:
    """매크로 한 벌. **하나가 실패해도 나머지는 낸다.**

    브리프가 매크로 때문에 통째로 안 뜨면 안 된다 — 없는 줄은 화면에서
    「못 받았습니다」로 나오면 그만이다.
    """
    own = client is None
    client = client or httpx.Client(timeout=20.0)
    out: list[Point] = []
    try:
        for s in series:
            try:
                point = fetch_point(s, today=today, api_key=api_key, client=client)
            except EcosError as exc:
                log.warning("매크로를 못 받았습니다 (%s): %s", s.label, exc)
                continue
            if point is not None:
                out.append(point)
    finally:
        if own:
            client.close()
    return out


def available() -> bool:
    return bool(os.environ.get("ECOS_API_KEY", ""))
