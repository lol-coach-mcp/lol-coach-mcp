"""순수 SVG 차트 생성기 (I/O 없음, 결정적 출력). 스타일은 페이지 CSS의 클래스 사용."""
from __future__ import annotations

from html import escape
from typing import Any, Sequence

from .render import empty_state

MAP_SIZE_UNITS = 14870
PAD_L, PAD_R, PAD_T, PAD_B = 48, 12, 12, 40
SERIES_CLASSES = ("s0", "s1", "s2", "s3")
STRIP_CLASS = {"승": "result-win", "패": "result-loss", "다시하기": "result-remake"}


def _f(v: float) -> str:
    return f"{float(v):.1f}"


def _e(v: Any) -> str:
    return escape(str(v))


def map_to_pixel(x: float, y: float, size: float) -> tuple[float, float]:
    """게임 좌표(0~14870, y 위쪽) → 이미지 픽셀(y 아래쪽)."""
    return (x / MAP_SIZE_UNITS * size, size - y / MAP_SIZE_UNITS * size)


def _span(lo: float, hi: float) -> tuple[float, float]:
    return (lo - 1.0, hi + 1.0) if hi == lo else (lo, hi)


def line_chart(
    series: Sequence[dict],
    width: int = 640,
    height: int = 280,
    x_label: str = "",
    y_label: str = "",
    y_zero_line: bool = False,
) -> str:
    series = [s for s in series if s.get("points")]
    if not series:
        return empty_state("그래프로 그릴 데이터 없음")
    pts = [p for s in series for p in s["points"]]
    x0, x1 = _span(min(p[0] for p in pts), max(p[0] for p in pts))
    y0, y1 = _span(min(p[1] for p in pts), max(p[1] for p in pts))
    if y_zero_line:
        y0, y1 = min(y0, 0), max(y1, 0)
        if y0 == y1:
            y0, y1 = y0 - 1.0, y1 + 1.0
    pw, ph = width - PAD_L - PAD_R, height - PAD_T - PAD_B

    def tx(x: float) -> str:
        return _f(PAD_L + (x - x0) / (x1 - x0) * pw)

    def ty(y: float) -> str:
        return _f(PAD_T + ph - (y - y0) / (y1 - y0) * ph)

    out = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{_e(y_label)}">']
    out.append(
        f'<line class="axis" x1="{PAD_L}" y1="{PAD_T}" x2="{PAD_L}" y2="{PAD_T + ph}"/>'
        f'<line class="axis" x1="{PAD_L}" y1="{PAD_T + ph}" x2="{PAD_L + pw}" y2="{PAD_T + ph}"/>'
    )
    out.append(
        f'<text x="{PAD_L - 4}" y="{PAD_T + 8}" text-anchor="end">{_e(_f(y1))}</text>'
        f'<text x="{PAD_L - 4}" y="{PAD_T + ph}" text-anchor="end">{_e(_f(y0))}</text>'
        f'<text x="{PAD_L}" y="{PAD_T + ph + 14}" text-anchor="start">{_e(_f(x0))}</text>'
        f'<text x="{PAD_L + pw}" y="{PAD_T + ph + 14}" text-anchor="end">{_e(_f(x1))}</text>'
    )
    if y_zero_line:
        zy = ty(0)
        out.append(
            f'<line class="zero-line" x1="{PAD_L}" y1="{zy}" x2="{PAD_L + pw}" y2="{zy}"/>'
        )
    if x_label:
        out.append(
            f'<text x="{_f(PAD_L + pw / 2)}" y="{height - 4}" text-anchor="middle">{_e(x_label)}</text>'
        )
    if y_label:
        out.append(f'<text x="4" y="{PAD_T + 8}" text-anchor="start">{_e(y_label)}</text>')
    for i, s in enumerate(series):
        cls = SERIES_CLASSES[i % len(SERIES_CLASSES)]
        path = " ".join(f"{tx(x)},{ty(y)}" for x, y in sorted(s["points"]))
        out.append(f'<polyline class="{cls}" points="{path}"/>')
        out.append(
            f'<text x="{PAD_L + 8 + i * 120}" y="{PAD_T + 24}">'
            f'{_e(s.get("label", ""))}</text>'
        )
    out.append("</svg>")
    return "".join(out)


def bar_chart(
    items: Sequence[tuple[Any, float]],
    width: int = 640,
    height: int = 280,
    x_label: str = "",
    y_label: str = "",
) -> str:
    if not items:
        return empty_state("그래프로 그릴 데이터 없음")
    n = len(items)
    top = max(max(v for _, v in items), 0) or 1.0
    pw, ph = width - PAD_L - PAD_R, height - PAD_T - PAD_B
    slot = pw / n
    bw = slot * 0.7
    out = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{_e(y_label)}">']
    out.append(
        f'<line class="axis" x1="{PAD_L}" y1="{PAD_T + ph}" x2="{PAD_L + pw}" y2="{PAD_T + ph}"/>'
    )
    for i, (label, value) in enumerate(items):
        h = max(value, 0) / top * ph
        x = PAD_L + i * slot + (slot - bw) / 2
        out.append(
            f'<rect class="bar" x="{_f(x)}" y="{_f(PAD_T + ph - h)}" '
            f'width="{_f(bw)}" height="{_f(h)}"><title>{_e(label)}: {_e(_f(value))}</title></rect>'
        )
        out.append(
            f'<text x="{_f(x + bw / 2)}" y="{_f(PAD_T + ph - h - 3)}" text-anchor="middle">{_e(_f(value))}</text>'
        )
        out.append(
            f'<text x="{_f(x + bw / 2)}" y="{PAD_T + ph + 14}" text-anchor="middle">{_e(label)}</text>'
        )
    if x_label:
        out.append(
            f'<text x="{_f(PAD_L + pw / 2)}" y="{height - 4}" text-anchor="middle">{_e(x_label)}</text>'
        )
    if y_label:
        out.append(f'<text x="4" y="{PAD_T}" text-anchor="start">{_e(y_label)}</text>')
    out.append("</svg>")
    return "".join(out)


def scatter_on_image(
    points: Sequence[dict], image_data_uri: str | None, size: int = 480
) -> str:
    out = [f'<svg viewBox="0 0 {size} {size}" role="img" aria-label="map">']
    if image_data_uri:
        out.append(
            f'<image href="{_e(image_data_uri)}" x="0" y="0" width="{size}" height="{size}"/>'
        )
    else:
        out.append(f'<rect class="map-bg" x="0" y="0" width="{size}" height="{size}"/>')
    for p in points:
        px, py = map_to_pixel(p["x"], p["y"], size)
        label = p.get("label", "")
        out.append(
            f'<circle class="dot" cx="{_f(px)}" cy="{_f(py)}" r="5"><title>{_e(label)}</title></circle>'
            f'<text x="{_f(px + 7)}" y="{_f(py + 4)}">{_e(label)}</text>'
        )
    out.append("</svg>")
    return "".join(out)


def result_strip(results: Sequence[str], cell: int = 22) -> str:
    if not results:
        return empty_state("최근 경기 없음")
    width = len(results) * (cell + 2)
    out = [
        f'<svg viewBox="0 0 {width} {cell}" role="img" aria-label="results" '
        f'style="max-width:{width}px">'
    ]
    for i, r in enumerate(results):
        cls = STRIP_CLASS.get(r, "result-remake")
        out.append(
            f'<rect class="{cls}" x="{i * (cell + 2)}" y="0" width="{cell}" height="{cell}" rx="3">'
            f"<title>{_e(r)}</title></rect>"
        )
    out.append("</svg>")
    return "".join(out)
