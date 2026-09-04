"""eval이 딛고 설 라벨 — **저장소에 커밋된 것만 쓴다** (D89).

왜 여기 있나
------------
`tests/test_telegram_parse.py`가 같은 코퍼스를 이미 읽는다. 그런데 그건
**시험 파일 안의 사적 헬퍼**라 eval 러너가 못 쓴다. 브리프가 지적한 그 자리다:
*"3겹 방어를 측정으로 만들었는데 라벨셋이 안 남아 회귀를 못 막는다."*

라벨은 남아 있었지만 **꺼내 쓸 수 없는 자리에** 있었다. 여기로 올린다.

왜 API 키가 필요 없나
---------------------
라벨이 코퍼스 자체에 있다. 리포트 제목에는 종목코드가 붙어 있어서, 코드를
지우고 **이름만으로 맞히게** 하면 정답이 이미 손에 있다. 모델도 네트워크도
안 부른다 — 그래서 CI에서 푸시마다 돌 수 있다.
"""

from __future__ import annotations

import csv
import glob
import re
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

# 라벨이 붙은 리포트 제목이 있는 파일. 없으면 조용히 건너뛴다 — 코퍼스는
# 저장소에 있지만 얇은 체크아웃에서 빠질 수 있다.
_TITLE_FILES = (
    "market/stock_reports_3names",
    "market/award_winner_reports",
    "consensus/labeled_clean",
)

_CODE = re.compile(r"\d{6}")
_CODE_IN_TITLE = re.compile(r"\(?\b\d{6}\b\)?")


@lru_cache(maxsize=1)
def listed_names() -> dict[str, str]:
    """`corpus/**/*.csv`의 상장사 이름 → 종목코드.

    **DART corpCode 전량이 아니다.** 코퍼스에 실린 것뿐이라 실제 오탐은 더
    난다 — 이 이름 목록으로 잰 정밀도는 **하한**이지 추정치가 아니다.
    """
    names: dict[str, str] = {}
    for path in glob.glob(str(ROOT / "corpus" / "**" / "*.csv"), recursive=True):
        try:
            with open(path, encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                cols = reader.fieldnames or []
                name_col = next((c for c in cols if c in ("company", "dart_name")), None)
                code_col = next((c for c in cols if c in ("code", "symbol")), None)
                if not name_col or not code_col:
                    continue
                for row in reader:
                    name = (row.get(name_col) or "").strip()
                    code = (row.get(code_col) or "").strip()
                    if name and _CODE.fullmatch(code):
                        names.setdefault(name, code)
        except (OSError, csv.Error, UnicodeDecodeError):
            continue
    return names


@lru_cache(maxsize=1)
def labeled_titles() -> tuple[tuple[str, str], ...]:
    """(제목, 정답 종목코드). **제목에서 코드 표기를 지운다.**

    안 지우면 분류기가 코드를 읽어 맞히므로 이름 매칭을 시험하지 못한다.
    """
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for name in _TITLE_FILES:
        path = ROOT / "corpus" / f"{name}.csv"
        if not path.exists():
            continue
        try:
            with open(path, encoding="utf-8-sig", newline="") as f:
                for row in csv.DictReader(f):
                    title = (row.get("title") or "").strip()
                    code = (row.get("code") or "").strip()
                    if not title or not _CODE.fullmatch(code):
                        continue
                    clean = _CODE_IN_TITLE.sub(" ", title)
                    key = (clean, code)
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append(key)
        except (OSError, csv.Error, UnicodeDecodeError):
            continue
    return tuple(out)


@lru_cache(maxsize=1)
def ordinary_prose() -> str:
    """회사 얘기가 아닌 글. **상용어 이름이 여기서 잡히면 오탐이다.**

    저장소의 마크다운을 쓴다 — 계속 자라므로 총 매치 수는 고정할 수 없고,
    고정하는 것은 「상용어 이름의 매치가 0」이라는 사실이다.
    """
    parts: list[str] = []
    for path in sorted(glob.glob(str(ROOT / "docs" / "**" / "*.md"), recursive=True)):
        try:
            parts.append(Path(path).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
    readme = ROOT / "README.md"
    if readme.exists():
        parts.append(readme.read_text(encoding="utf-8"))
    return "\n".join(parts)
