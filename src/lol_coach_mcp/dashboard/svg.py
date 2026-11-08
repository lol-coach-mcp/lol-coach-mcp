"""순수 SVG 차트 생성기 (I/O 없음, 결정적 출력). 스타일은 페이지 CSS의 클래스 사용."""
from __future__ import annotations

from html import escape
from typing import Any, Sequence

from .render import SafeHtml, empty_state

MAP_SIZE_UNITS = 14870
PAD_L, PAD_R, PAD_B = 48, 12, 44
YLABEL_ROW = 20
LEGEND_ROW = 20
LEGEND_SWATCH = 12
CHAR_W_WIDE = 14  # 14px 글꼴 기준 비ASCII(한글 등) 글자 폭
CHAR_W_ASCII = 8
BAR_PAD_B = 60  # 막대 값 라벨(음수) 아래에 범주 라벨 공간 확보
MARKER_MAX_POINTS = 40
SERIES_CLASSES = ("s0", "s1", "s2", "s3")
STRIP_CLASS = {"승": "result-win", "패": "result-loss", "다시하기": "result-remake"}


def _f(v: float) -> str:
    return f"{float(v):.1f}"


def _e(v: Any) -> str:
    return escape(str(v))


def _text_w(text: Any) -> int:
    """글자 폭 보수적 추정(px)."""
    return sum(CHAR_W_WIDE if ord(c) > 127 else CHAR_W_ASCII for c in str(text))


def _aria(label: Any) -> str:
    return f' aria-label="{_e(label)}"' if label else ""


def map_to_pixel(x: float, y: float, size: float) -> tuple[float, float]:
    """게임 좌표(0~14870, y 위쪽) → 이미지 픽셀(y 아래쪽)."""
    return (x / MAP_SIZE_UNITS * size, size - y / MAP_SIZE_UNITS * size)


def _span(lo: float, hi: float) -> tuple[float, float]:
    return (lo - 1.0, hi + 1.0) if hi == lo else (lo, hi)


def _legend(series: Sequence[dict], width: int) -> tuple[list[str], int]:
    """범례 항목을 폭을 넘으면 다음 줄로 배치. (조각, 사용 높이) 반환."""
    out: list[str] = []
    x, row = PAD_L, 0
    limit = width - PAD_R
    for i, s in enumerate(series):
        label = str(s.get("label", ""))
        item_w = LEGEND_SWATCH + 6 + _text_w(label) + 16
        if x > PAD_L and x + item_w > limit:
            x, row = PAD_L, row + 1
        y = row * LEGEND_ROW
        cls = SERIES_CLASSES[i % len(SERIES_CLASSES)]
        out.append(
            f'<rect class="legend-{cls}" x="{x}" y="{y}" width="{LEGEND_SWATCH}" '
            f'height="{LEGEND_SWATCH}" rx="2"/>'
            f'<text x="{x + LEGEND_SWATCH + 6}" y="{y + LEGEND_SWATCH - 1}">{_e(label)}</text>'
        )
        x += item_w
    return out, (row + 1) * LEGEND_ROW


def line_chart(
    series: Sequence[dict],
    width: int = 560,
    height: int = 300,
    x_label: str = "",
    y_label: str = "",
    y_zero_line: bool = False,
) -> SafeHtml:
    width, height = int(width), int(height)
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
    legend, legend_h = _legend(series, width)
    shift = YLABEL_ROW if y_label else 0
    top = shift + legend_h + 14  # 위쪽 눈금 글자 여유
    pw = width - PAD_L - PAD_R
    ph = max(height - top - PAD_B, 60)
    height = top + ph + PAD_B

    def tx(x: float) -> str:
        return _f(PAD_L + (x - x0) / (x1 - x0) * pw)

    def ty(y: float) -> str:
        return _f(top + ph - (y - y0) / (y1 - y0) * ph)

    out = [f'<svg viewBox="0 0 {width} {height}" role="img"{_aria(y_label)}>']
    if y_label:
        out.append(f'<text x="4" y="14" text-anchor="start">{_e(y_label)}</text>')
    out.append(f'<g transform="translate(0,{shift})">' + "".join(legend) + "</g>")
    out.append(
        f'<line class="axis" x1="{PAD_L}" y1="{top}" x2="{PAD_L}" y2="{top + ph}"/>'
        f'<line class="axis" x1="{PAD_L}" y1="{top + ph}" x2="{PAD_L + pw}" y2="{top + ph}"/>'
    )
    out.append(
        f'<text x="{PAD_L - 4}" y="{top + 10}" text-anchor="end">{_e(_f(y1))}</text>'
        f'<text x="{PAD_L - 4}" y="{top + ph}" text-anchor="end">{_e(_f(y0))}</text>'
        f'<text x="{PAD_L}" y="{top + ph + 16}" text-anchor="start">{_e(_f(x0))}</text>'
        f'<text x="{PAD_L + pw}" y="{top + ph + 16}" text-anchor="end">{_e(_f(x1))}</text>'
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
    for i, s in enumerate(series):
        cls = SERIES_CLASSES[i % len(SERIES_CLASSES)]
        spts = sorted(s["points"])
        path = " ".join(f"{tx(x)},{ty(y)}" for x, y in spts)
        out.append(f'<polyline class="{cls}" points="{path}"/>')
        if len(spts) <= MARKER_MAX_POINTS:
            out.extend(
                f'<circle class="marker-{cls}" cx="{tx(x)}" cy="{ty(y)}" r="3"/>'
                for x, y in spts
            )
    out.append("</svg>")
    return SafeHtml("".join(out))


def bar_chart(
    items: Sequence[tuple[Any, float]],
    width: int = 560,
    height: int = 300,
    x_label: str = "",
    y_label: str = "",
) -> SafeHtml:
    width, height = int(width), int(height)
    if not items:
        return empty_state("그래프로 그릴 데이터 없음")
    n = len(items)
    lo = min(0.0, min(v for _, v in items))
    hi = max(0.0, max(v for _, v in items))
    if hi == lo:
        hi = lo + 1.0
    top = (YLABEL_ROW if y_label else 0) + 18
    pw = width - PAD_L - PAD_R
    ph = max(height - top - BAR_PAD_B, 60)
    height = top + ph + BAR_PAD_B
    slot = pw / n
    bw = slot * 0.7
    zero_y = top + ph * hi / (hi - lo)
    out = [f'<svg viewBox="0 0 {width} {height}" role="img"{_aria(y_label)}>']
    if y_label:
        out.append(f'<text x="4" y="14" text-anchor="start">{_e(y_label)}</text>')
    out.append(
        f'<line class="axis" x1="{PAD_L}" y1="{_f(zero_y)}" x2="{PAD_L + pw}" y2="{_f(zero_y)}"/>'
    )
    for i, (label, value) in enumerate(items):
        h = abs(value) / (hi - lo) * ph
        x = PAD_L + i * slot + (slot - bw) / 2
        y = zero_y - h if value >= 0 else zero_y
        vy = y - 3 if value >= 0 else y + h + 13
        out.append(
            f'<rect class="bar" x="{_f(x)}" y="{_f(y)}" '
            f'width="{_f(bw)}" height="{_f(h)}"><title>{_e(label)}: {_e(_f(value))}</title></rect>'
        )
        out.append(
            f'<text x="{_f(x + bw / 2)}" y="{_f(vy)}" text-anchor="middle">{_e(_f(value))}</text>'
        )
        out.append(
            f'<text x="{_f(x + bw / 2)}" y="{top + ph + 32}" text-anchor="middle">{_e(label)}</text>'
        )
    if x_label:
        out.append(
            f'<text x="{_f(PAD_L + pw / 2)}" y="{height - 4}" text-anchor="middle">{_e(x_label)}</text>'
        )
    out.append("</svg>")
    return SafeHtml("".join(out))


def scatter_on_image(
    points: Sequence[dict], image_data_uri: str | None, size: int = 480
) -> SafeHtml:
    size = int(size)
    out = [f'<svg viewBox="0 0 {size} {size}" role="img" aria-label="지도">']
    if image_data_uri:
        out.append(
            f'<image href="{_e(image_data_uri)}" x="0" y="0" width="{size}" height="{size}"/>'
        )
    else:
        out.append(f'<rect class="map-bg" x="0" y="0" width="{size}" height="{size}"/>')
    for p in points:
        px, py = map_to_pixel(p["x"], p["y"], size)
        label = str(p.get("label", ""))
        w = _text_w(label)
        if px + 8 + w <= size:
            lx, anchor = px + 8, "start"
        elif px - 8 - w >= 0:
            lx, anchor = px - 8, "end"
        else:
            lx, anchor = max(0, size - w), "start"
        out.append(
            f'<circle class="dot" cx="{_f(px)}" cy="{_f(py)}" r="5"><title>{_e(label)}</title></circle>'
            f'<text x="{_f(lx)}" y="{_f(py + 4)}" text-anchor="{anchor}">{_e(label)}</text>'
        )
    out.append("</svg>")
    return SafeHtml("".join(out))


def result_strip(results: Sequence[str], cell: int = 22) -> SafeHtml:
    cell = int(cell)
    if not results:
        return empty_state("최근 경기 없음")
    width = len(results) * (cell + 2)
    out = [
        f'<svg viewBox="0 0 {width} {cell}" role="img" aria-label="경기 결과" '
        f'style="max-width:{width}px">'
    ]
    for i, r in enumerate(results):
        cls = STRIP_CLASS.get(r, "result-remake")
        out.append(
            f'<rect class="{cls}" x="{i * (cell + 2)}" y="0" width="{cell}" height="{cell}" rx="3">'
            f"<title>{_e(r)}</title></rect>"
        )
    out.append("</svg>")
    return SafeHtml("".join(out))
