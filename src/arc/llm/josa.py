"""한국어 조사 교정 — 플레이스홀더 치환 시점에 쓴다.

왜 필요한가
-----------
LLM은 `{{num:operating_margin_2025a}}으로`처럼 조사를 붙여 쓰는데, **치환될
값의 끝소리를 모른다.** 값을 안 보여주는 게 이 시스템의 전제이므로 알 수도
없다. 그 결과가 실측에서 이렇게 나왔다:

    40.0%으로  →  40.0%로     (퍼센트 = 받침 없음)
    53.2%을    →  53.2%를
    44.2%과    →  44.2%와
    70.1%이    →  70.1%가

반대로 `5,363억원으로`는 맞다(원 = ㄴ받침). 즉 **단위가 조사를 결정하고,
단위는 치환 시점에 확정된다.** 그래서 이건 판단이 아니라 계산이다.

주의: `이`는 주격조사일 수도, 서술격조사 `이다`의 어간일 수도 있다.
"40.0%이다"는 옳고 "40.0%이 웃돈다"는 틀리다. 뒤따르는 글자로 가른다.
"""

from __future__ import annotations

# (받침 있을 때, 받침 없을 때)
_PAIRS: tuple[tuple[str, str], ...] = (
    ("으로서", "로서"),
    ("으로써", "로써"),
    ("으로", "로"),
    ("이나", "나"),
    ("은", "는"),
    ("을", "를"),
    ("과", "와"),
    ("이", "가"),
)

# `이`가 서술격조사(이다/이라/이며…)일 때 뒤에 오는 글자. 이 경우 손대지 않는다.
#
# 서술격조사는 받침이 없어도 원형("40%이다", "25.9%이며")이 표준이고 축약형도
# 쓰인다. 어느 쪽도 틀리지 않으므로 **교정 대상이 아니다.** 반면 주격조사
# `이`("40%이 웃돈다")는 명백히 틀리므로 고친다.
_COPULA_FOLLOWERS = frozenset("다라며고네요지만")

# 숫자·기호를 한국어로 읽었을 때의 끝소리 받침 여부.
# 값은 (받침 있음?, ㄹ받침?) — ㄹ받침은 '으로/로'에서만 다르게 취급된다.
_READING_JONG: dict[str, tuple[bool, bool]] = {
    "%": (False, False),  # 퍼센트
    "＄": (False, False),
    "$": (False, False),  # 달러
    "0": (True, False),  # 영
    "1": (True, True),  # 일
    "2": (False, False),  # 이
    "3": (True, False),  # 삼
    "4": (False, False),  # 사
    "5": (False, False),  # 오
    "6": (True, False),  # 육
    "7": (True, True),  # 칠
    "8": (True, True),  # 팔
    "9": (False, False),  # 구
    "p": (False, False),  # pp = 피피
    "P": (False, False),
    "x": (False, False),  # 배수 표기 = 엑스… 실제로는 '배'로 읽히지만 받침 없음
    "X": (False, False),
}


def _final_sound(value: str) -> tuple[bool, bool] | None:
    """값의 끝소리 → (받침 있음?, ㄹ받침?). 판단할 수 없으면 None."""
    for ch in reversed(value):
        if ch.isspace():
            continue
        if "가" <= ch <= "힣":
            jong = (ord(ch) - 0xAC00) % 28
            return (jong != 0, jong == 8)  # 8 = ㄹ
        if ch in _READING_JONG:
            return _READING_JONG[ch]
        # 괄호·따옴표 등은 건너뛰고 그 앞 글자를 본다
        if ch in "()[]{}\"'`,.":
            continue
        return None
    return None


def attach(word: str, with_final: str, without_final: str) -> str:
    """`word` + 받침에 맞는 조사. 판단할 수 없으면 받침 있는 쪽을 쓴다.

    **결정적 코드가 회사·부문 이름을 문장에 넣을 때 쓴다.** 치환 시점 교정
    (`render_text`)은 플레이스홀더 뒤의 조사만 고치므로, 이름 뒤에 붙는 조사는
    여기서 골라야 한다. 실측: 렌즈가 "화장품은 늘고 **기타은** 줄었다"를 냈다.
    """
    sound = _final_sound(word)
    has_final = True if sound is None else sound[0]
    return f"{word}{with_final if has_final else without_final}"


def fix_after(value: str, following: str) -> str:
    """`value` 바로 뒤에 오는 조사를 교정해 돌려준다 (조사가 아니면 빈 문자열).

    돌려주는 것은 **교체할 조사**이고, 호출자가 `following`의 앞부분을
    그만큼 잘라낸 뒤 붙인다.
    """
    sound = _final_sound(value)
    if sound is None or not following:
        return ""
    has_jong, is_rieul = sound

    for with_jong, without_jong in _PAIRS:
        for candidate in (with_jong, without_jong):
            if not following.startswith(candidate):
                continue
            rest = following[len(candidate) :]
            # 서술격조사 '이다/이라/이며'는 주격조사가 아니다 — 손대지 않는다
            if candidate == "이" and rest[:1] in _COPULA_FOLLOWERS:
                return ""
            if with_jong.endswith("로"):
                # ㄹ받침은 '으로'가 아니라 '로'를 쓴다 ("서울로", "1,000원으로")
                correct = without_jong if (not has_jong or is_rieul) else with_jong
            else:
                correct = with_jong if has_jong else without_jong
            return correct if correct != candidate else ""
    return ""


def replace_particle(value: str, following: str) -> tuple[str, int]:
    """`(교정된 조사, 소비한 글자 수)`. 교정할 게 없으면 `("", 0)`.

    호출자는 `following[consumed:]`부터 이어 붙이면 된다.
    """
    correct = fix_after(value, following)
    if not correct:
        return "", 0
    for with_jong, without_jong in _PAIRS:
        for candidate in (with_jong, without_jong):
            if following.startswith(candidate):
                return correct, len(candidate)
    return "", 0
