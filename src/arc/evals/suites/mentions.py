"""종목명 인식기 eval — **결정적이고 무료다** (D89).

무엇을 재나
-----------
`ingest/telegram_mentions.py`는 메시지에서 종목명을 찾는 분류기다. 오탐이
문제인 자리다: 「대상」은 식품회사(001680)이면서 「~을 대상으로」이고,
「나노」·「레이」·「하림」·「만도」도 전부 상장사이면서 흔한 말이다.

세 겹으로 막는데(최장 일치 · 경계 검사 · 상용어 목록) **그 강도가 조용히
느슨해지면 아무도 모른다.** 게이트가 못 보는 종류다 — 문법도 형식도 멀쩡하고
숫자도 안 나오므로 G0가 통과시킨다. 그래서 eval이 필요하다.

왜 시험이 아니라 eval인가
-------------------------
같은 측정을 `tests/test_telegram_parse.py::TestMeasuredExtraction`이 이미
하고 있고, 거기서는 **정확한 등식**으로 못 박는다(`missed == 14`,
`extras == (156, 52, 47)`). 그건 회귀를 확실히 잡지만 **코퍼스가 자라면 깨진다** —
새 리포트 제목을 추가하는 정직한 작업이 빨간 시험을 만든다.

eval은 같은 것을 **바닥**으로 잰다. 재현율이 99.5% 아래로 떨어지면 막고,
그 위에서 코퍼스가 자라는 것은 통과시킨다. 둘 다 있는 것이 맞다 — 시험은
「지금 정확히 이 값」이고 eval은 「이 아래로는 안 된다」다.

N을 밝힌다
----------
라벨은 코퍼스에 실린 것뿐이다. 상장사 이름은 DART 전량(약 2,800종)이 아니라
코퍼스의 1,100여 종이므로 **여기서 잰 정밀도는 하한**이다 — 이름을 더 넣으면
오탐이 더 난다. 이 스위트는 **트립와이어이지 정확도 추정이 아니다.**
"""

from __future__ import annotations

from arc.evals.corpora import labeled_titles, listed_names, ordinary_prose
from arc.evals.result import Result
from arc.ingest.telegram_mentions import (
    COMMON_WORD_NAMES,
    Evidence,
    NameIndex,
    extract_mentions,
)

NAME = "mentions"


def run() -> list[Result]:
    """재현율 · 덤 · 산문 오탐 셋. **하나라도 재료가 없으면 그렇게 말한다.**"""
    names = listed_names()
    titles = labeled_titles()
    if len(names) < 1000 or len(titles) < 5000:
        return [
            Result(
                suite=NAME,
                metric="corpus_present",
                value=0.0,
                note=(
                    f"라벨 코퍼스가 모자랍니다 — 상장사 {len(names)}종 · 제목 "
                    f"{len(titles)}건. corpus/ 가 체크아웃됐는지 확인하십시오."
                ),
            )
        ]

    index = NameIndex.from_names(names)

    # ── 재현율 — 이름만으로 정답 종목을 잡는가 ──────────────────────
    missed = [t for t, code in titles if code not in {m.symbol for m in extract_mentions(t, index)}]
    recall = 1.0 - len(missed) / len(titles)

    # ── 덤 — 정답 말고 더 잡은 것 ────────────────────────────────────
    # **비율이 아니라 개수다.** 제목 하나에 회사가 둘 나오는 것은 정상이라
    # (「삼성전자·SK하이닉스」) 이 값은 정밀도가 아니라 추세 지표다.
    extras = sum(len({m.symbol for m in extract_mentions(t, index)} - {code}) for t, code in titles)

    # ── 산문 오탐 — 상용어 이름이 회사로 잡히면 안 된다 ──────────────
    # 경계 규칙만 켠 상태에서 실측 19건이었다. 상용어 목록이 그것을 0으로 만든다.
    prose = ordinary_prose()
    prose_hits = sum(
        1
        for m in extract_mentions(prose, index)
        if index.by_code.get(m.symbol, "") in COMMON_WORD_NAMES
    )
    naive_prose_hits = sum(
        1
        for m in extract_mentions(prose, index, min_evidence_for_risky=Evidence.NAME)
        if index.by_code.get(m.symbol, "") in COMMON_WORD_NAMES
    )

    n = f"제목 {len(titles):,}건 · 상장사 {len(names):,}종"
    return [
        Result(
            suite=NAME,
            metric="recall",
            value=round(recall, 5),
            note=f"{n} · 놓친 {len(missed)}건",
        ),
        Result(
            suite=NAME,
            metric="extras_per_title",
            value=round(extras / len(titles), 5),
            note=f"{n} · 덤 총 {extras}건. 정밀도가 아니라 추세 지표다",
        ),
        Result(
            suite=NAME,
            metric="prose_false_positives",
            value=float(prose_hits),
            note=(
                f"산문 {len(prose):,}자에서 상용어 이름이 회사로 잡힌 횟수. "
                f"상용어 목록을 끄면 {naive_prose_hits}건"
            ),
        ),
    ]
