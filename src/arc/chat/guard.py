"""답변을 내보내기 전에 **문장 단위로** 되짚는다.

게이트를 다시 쓴다
------------------
채팅도 리포트와 같은 불변식 위에 있다 — LLM은 `{{num:키}}`만 쓰고 값은
프롬프트에 들어가지 않는다. 그러므로 검사도 같은 것을 쓴다: `G0Gate`의
수치 대조와 컴플라이언스 룰을 **문장마다** 돌린다. 게이트를 새로 쓰면 두
규칙이 갈라지고, 갈라지면 리포트에서 막히는 문장이 채팅에서는 나간다.

문장 단위인 이유는 `G0Gate`가 줄 번호를 주기 때문이다. 줄에서 문장을 되찾는
매핑을 만드는 것보다, 자른 문장을 그대로 넣는 편이 짧고 틀릴 곳이 없다.

버리는 것과 거부하는 것
-----------------------
* **미등록 숫자·모르는 키** → 그 **문장만** 버린다. 근거가 없는 것은 그
  문장이지 답변 전체가 아니다.
* **투자의견·목표주가·매매 판단**(D4) → **답변 전체를 거부한다.** 이건 문장
  하나의 결함이 아니라 시스템이 하지 않기로 한 일을 한 것이고, 앞뒤를 남기면
  남은 문장이 그 판단의 근거로 읽힌다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from arc.chat.retrieval import Retrieval
from arc.llm.number_registry import PLACEHOLDER_RE, NumberRegistry
from arc.verify.g0 import G0Gate

# 근거를 못 찾았을 때의 문구. **한 군데서만 정의한다** — 화면·테스트·프롬프트가
# 같은 문자열을 봐야 "모른다고 답했는가"를 기계가 판정할 수 있다.
NO_EVIDENCE = "확인할 수 있는 근거가 없습니다."

# 투자 판단 질문에 내는 고정 답. **금지어를 쓰지 않고 쓴다** — 이 문구 자체가
# 게이트에 걸리면 안 된다.
POLICY_REFUSAL = (
    "이 시스템은 투자 판단에 해당하는 답을 내지 않습니다. 확인된 공시 수치와 그 출처만 제시합니다."
)

_MARKER_RE = re.compile(r"\[(c\d+)\]")
_SENT_SPLIT = re.compile(r"(?<=[.!?。])\s+")

# 질문 쪽에서 걸러야 하는 것. `G0Gate`는 본문을 검사하는 물건이라 "사도
# 될까요" 같은 **질문 어법**은 갖고 있지 않다. 여기에만 둔다.
_ASK_FOR_OPINION = re.compile(
    r"(사도|팔아도|사야|팔아야|매수|매도|담아도|들어가도|익절|손절)\s*(될까|되나|하나|할까|괜찮|좋을|하는지|해도)"
    r"|어떤\s*종목을?\s*(사|골라)"
    r"|추천\s*(해|종목|좀)"
)


@dataclass
class Verdict:
    """검사 결과. `text`는 살아남은 문장만 남긴 **치환 전** 본문이다."""

    text: str = ""
    rejected: list[str] = field(default_factory=list)  # 버린 문장 (이유 포함)
    unsourced: list[str] = field(default_factory=list)  # 출처 표시가 없는 문장
    keys: list[str] = field(default_factory=list)  # 살아남은 본문이 쓴 수치 키
    markers: list[str] = field(default_factory=list)  # 살아남은 본문이 단 카드 표시
    refused: str = ""  # 비어 있지 않으면 답변 전체를 내지 않는다

    @property
    def ok(self) -> bool:
        return not self.refused and bool(self.text.strip())


def asks_for_opinion(question: str) -> str:
    """투자 판단을 묻는 질문인가. 그렇다면 그 이유를 돌려준다 (아니면 빈 문자열).

    **LLM을 부르기 전에** 판정한다. 부르고 나서 막으면 모델이 한 번은 그 답을
    만든 셈이고, 재시도 비용도 든다. 수치 대조는 하지 않으므로 빈 레지스트리로
    충분하다 — 여기서 보는 것은 컴플라이언스 룰뿐이다.
    """
    gate = G0Gate(NumberRegistry())
    opinion = [v for v in gate.check_compliance(question) if v.rule == "banned_opinion"]
    if opinion:
        return opinion[0].detail
    if _ASK_FOR_OPINION.search(question):
        return "투자 판단을 묻는 질문 — D4에 따라 답하지 않습니다."
    return ""


def split_sentences(text: str) -> list[list[str]]:
    """줄 → 문장들. **줄 구조를 보존한다** — 목록이 한 문단으로 뭉치면 읽기 나쁘다."""
    out: list[list[str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            out.append([])
            continue
        parts: list[str] = []
        for raw in _SENT_SPLIT.split(stripped):
            piece = raw.strip()
            if not piece:
                continue
            # 출처 표시가 마침표 **뒤에** 붙으면("…없습니다. [c1]") 쪼개기가
            # 그것을 문장 하나로 만든다. 그러면 앞 문장은 출처가 없는 것으로,
            # 표시만 남은 조각은 출처가 있는 것으로 잡힌다 — 둘 다 틀렸다.
            if parts and not _MARKER_RE.sub("", piece).strip():
                parts[-1] = f"{parts[-1]} {piece}"
                continue
            parts.append(piece)
        out.append(parts)
    return out


def check_answer(text: str, retrieval: Retrieval) -> Verdict:
    """문장마다 게이트를 돌리고, 통과한 것만 남긴 본문을 만든다."""
    verdict = Verdict()
    gate = G0Gate(retrieval.registry)
    tags = set(retrieval.tags())
    lines: list[str] = []

    for sentences in split_sentences(text):
        kept: list[str] = []
        for sentence in sentences:
            # 출처 표시는 우리가 정한 문법이지 모델이 쓴 숫자가 아니다. 떼고
            # 검사한다 — 안 그러면 `[c1]`의 1이 미등록 숫자로 잡힌다.
            probe = _MARKER_RE.sub("", sentence)
            violations = gate.check_compliance(probe)
            opinion = [v for v in violations if v.rule == "banned_opinion"]
            if opinion:
                verdict.refused = f"답변에 {opinion[0].detail}"
                return verdict
            problems = [v.detail for v in violations]
            problems += [v.detail for v in gate.check_numbers(probe)]

            markers = _MARKER_RE.findall(sentence)
            unknown = sorted({m for m in markers if m not in tags})
            if unknown:
                problems.append(f"근거에 없는 카드를 인용했습니다: {', '.join(unknown)}")
            if problems:
                verdict.rejected.append(f"{sentence} — {problems[0]}")
                continue

            keys = PLACEHOLDER_RE.findall(sentence)
            if not keys and not markers:
                # 버리지는 않는다. 연결 문장("정리하면 이렇습니다")까지 지우면
                # 답이 조각난다. 대신 **표시해서 올린다** — 화면이 이 목록을
                # 보여주면 검토자가 어디를 의심할지 안다.
                verdict.unsourced.append(sentence)
            verdict.keys.extend(keys)
            verdict.markers.extend(markers)
            kept.append(sentence)
        lines.append(" ".join(kept))

    verdict.text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    verdict.keys = list(dict.fromkeys(verdict.keys))
    verdict.markers = list(dict.fromkeys(verdict.markers))
    return verdict
