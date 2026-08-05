"""인라인 SVG 차트 — 외부 의존 없음.

왜 SVG 문자열인가
-----------------
벤치마크(SMIC)는 KEY CHARTS 한 쪽을 도식에 쓴다. 표만으로는 부문 구성이
한눈에 안 들어온다.

차트 라이브러리를 쓰지 않는 이유는 두 가지다. 웹 화면은 **엄격한 인라인
정책**을 전제로 만들었고(외부 스크립트 없음), 마크다운 산출물은 파일 하나로
완결돼야 한다. 순수 SVG 문자열이면 양쪽 다 그대로 들어간다.

숫자 취급
---------
차트에 그리는 값은 전부 **이미 레지스트리를 거친 것**이다. 이 모듈은 계산도
포맷도 하지 않고 받은 것을 그린다 — 여기서 숫자를 만들면 게이트 밖에서
수치가 생긴다.

라벨은 SVG `<text>`라 G0의 스캔 대상이 아니다. 그래서 **차트에는 값 라벨을
쓰지 않고** 비율만 형태로 보여준다. 정확한 수치는 바로 위 표에 있다.
"""

from __future__ import annotations

import html
from dataclasses import dataclass

# 부문 색 — 테마와 무관하게 읽히도록 채도를 낮춘 팔레트
_PALETTE = ("#5b9cff", "#c084fc", "#4ade80", "#fbbf24", "#f87171", "#94a3b8")


@dataclass(frozen=True)
class Slice:
    """차트 조각 하나. **값이 아니라 비율**을 받는다."""

    label: str
    share: float  # 0~100


def _esc(s: str) -> str:
    return html.escape(str(s), quote=True)


def palette(i: int) -> str:
    """조각 색 — 범례를 화면에서 다시 그릴 때 같은 색을 써야 한다."""
    return _PALETTE[i % len(_PALETTE)]


def segment_bar(slices: list[Slice], *, width: int = 640, height: int = 46) -> str:
    """부문 구성 가로 막대. 비중이 큰 순서로 이어 그린다.

    조각이 하나뿐이면 그리지 않는다 — 100% 막대는 정보가 없다.
    """
    items = [s for s in slices if s.share > 0]
    if len(items) < 2:
        return ""
    total = sum(s.share for s in items)
    if total <= 0:
        return ""

    bar_h, gap, label_y = 22, 2, 40
    parts = [
        (
            f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" '
            f'role="img" aria-label="부문별 매출 구성" xmlns="http://www.w3.org/2000/svg">'
        )
    ]
    x = 0.0
    for i, s in enumerate(items):
        w = max(0.0, s.share / total * width)
        color = _PALETTE[i % len(_PALETTE)]
        parts.append(
            f'<rect x="{x:.1f}" y="0" width="{max(0.0, w - gap):.1f}" height="{bar_h}" '
            f'rx="3" fill="{color}"><title>{_esc(s.label)}</title></rect>'
        )
        # 조각이 좁으면 라벨이 겹친다 — 넓은 것만 적는다
        if w > 64:
            parts.append(
                f'<text x="{x + 4:.1f}" y="{label_y}" font-size="11" '
                f'fill="{color}">{_esc(s.label)}</text>'
            )
        x += w
    parts.append("</svg>")
    return "".join(parts)


def trend_bars(
    labels: list[str], series: list[tuple[str, list[float]]], *, width: int = 640, height: int = 150
) -> str:
    """연도별 묶음 막대. `series`는 (이름, 연도별 값) 목록.

    값의 **크기 비교**만 보여준다. 축 눈금과 값 라벨을 넣지 않는 이유는
    표에 정확한 수치가 이미 있고, 차트가 수치의 두 번째 출처가 되면
    둘이 갈라질 수 있기 때문이다.
    """
    series = [(n, v) for n, v in series if any(x for x in v)]
    if not labels or not series:
        return ""
    peak = max((abs(x) for _, values in series for x in values), default=0.0)
    if peak <= 0:
        return ""

    pad_b, top = 24, 8
    plot_h = height - pad_b - top
    group_w = width / len(labels)
    bar_w = min(28.0, (group_w - 14) / len(series))

    parts = [
        (
            f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" '
            f'role="img" aria-label="연도별 추이" xmlns="http://www.w3.org/2000/svg">'
        )
    ]
    for gi, label in enumerate(labels):
        base_x = gi * group_w + (group_w - bar_w * len(series)) / 2
        for si, (name, values) in enumerate(series):
            value = values[gi] if gi < len(values) else 0.0
            h = abs(value) / peak * plot_h
            x = base_x + si * bar_w
            parts.append(
                f'<rect x="{x:.1f}" y="{top + plot_h - h:.1f}" width="{max(1.0, bar_w - 3):.1f}" '
                f'height="{h:.1f}" rx="2" fill="{_PALETTE[si % len(_PALETTE)]}">'
                f"<title>{_esc(name)} · {_esc(label)}</title></rect>"
            )
        parts.append(
            f'<text x="{gi * group_w + group_w / 2:.1f}" y="{height - 8}" font-size="11" '
            f'text-anchor="middle" fill="currentColor" opacity="0.65">{_esc(label)}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def legend(names: list[str]) -> str:
    """색-이름 대응. 차트에 라벨을 못 넣는 조각을 위해."""
    if not names:
        return ""
    chips = "".join(
        f'<span style="display:inline-flex;align-items:center;gap:5px;margin-right:12px">'
        f'<span style="width:9px;height:9px;border-radius:2px;background:{_PALETTE[i % len(_PALETTE)]}"></span>'
        f"{_esc(n)}</span>"
        for i, n in enumerate(names)
    )
    return f'<div style="font-size:12px;opacity:.75;margin-top:6px">{chips}</div>'
