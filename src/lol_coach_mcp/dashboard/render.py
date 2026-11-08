"""HTML 페이지 조립 (순수 함수, I/O 없음)."""
from __future__ import annotations

from html import escape
from typing import Any, Iterable, Sequence

DISCLAIMER = (
    "lol-coach-mcp is not endorsed by Riot Games and does not reflect the views or "
    "opinions of Riot Games or anyone officially involved in producing or managing "
    "Riot Games properties. Riot Games and all associated properties are trademarks "
    "or registered trademarks of Riot Games, Inc."
)

CSS = """
:root{--bg:#f6f7f9;--fg:#1c2330;--muted:#5d6676;--card:#fff;--border:#d9dde4;
--accent:#2a6fdb;--win:#2e9d5b;--loss:#d6453d;--remake:#8b93a1;--grid:#e4e7ec;--map:#cfd5de}
@media (prefers-color-scheme: dark){:root{--bg:#12161d;--fg:#e6e9ef;--muted:#9aa3b2;
--card:#1b212b;--border:#2e3644;--accent:#6aa3ff;--win:#4cc784;--loss:#ff6b62;
--remake:#7b8494;--grid:#2a313d;--map:#262d39}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font-family:-apple-system,"Segoe UI","Malgun Gothic","Apple SD Gothic Neo",sans-serif;line-height:1.5}
main{max-width:960px;margin:0 auto;padding:16px}
h1{font-size:1.5rem;margin:0 0 4px}
h2{font-size:1.15rem;margin:0 0 12px}
section{background:var(--card);border:1px solid var(--border);border-radius:10px;
padding:16px;margin:16px 0;overflow-x:auto}
.meta,.notes,footer{color:var(--muted);font-size:.85rem}
table{border-collapse:collapse;width:100%;font-size:.92rem}
th,td{text-align:left;padding:6px 10px;border-bottom:1px solid var(--border)}
th{color:var(--muted);font-weight:600}
dl.kv{display:grid;grid-template-columns:max-content 1fr;gap:4px 16px;margin:0}
dl.kv dt{color:var(--muted)}dl.kv dd{margin:0}
.empty-state{color:var(--muted);padding:12px;border:1px dashed var(--border);border-radius:8px}
svg{max-width:100%;height:auto;display:block}
svg text{fill:var(--fg);font-size:11px}
svg .axis,svg .grid-line{stroke:var(--grid);stroke-width:1}
svg .zero-line{stroke:var(--muted);stroke-dasharray:4 3;stroke-width:1}
svg .map-bg{fill:var(--map)}
svg .dot{fill:var(--loss);stroke:var(--bg);stroke-width:1}
svg .bar{fill:var(--accent)}
svg .result-win{fill:var(--win)}svg .result-loss{fill:var(--loss)}
svg .result-remake{fill:var(--remake)}
svg polyline.s0{stroke:var(--accent)}svg polyline.s1{stroke:var(--win)}
svg polyline.s2{stroke:var(--loss)}svg polyline.s3{stroke:var(--muted)}
svg polyline{fill:none;stroke-width:2}
footer{margin:24px 0 8px}
@media (max-width:600px){main{padding:8px}section{padding:12px}table{font-size:.85rem}}
"""


def empty_state(message: str) -> str:
    return f'<p class="empty-state">{escape(str(message))}</p>'


def table(headers: Sequence[Any], rows: Iterable[Sequence[Any]]) -> str:
    rows = list(rows)
    if not rows:
        return empty_state("표시할 데이터 없음")
    head = "".join(f"<th>{escape(str(h))}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{escape(str(c))}</td>" for c in row) + "</tr>"
        for row in rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def kv_list(pairs: Iterable[tuple[Any, Any]]) -> str:
    pairs = list(pairs)
    if not pairs:
        return empty_state("표시할 데이터 없음")
    items = "".join(
        f"<dt>{escape(str(k))}</dt><dd>{escape(str(v))}</dd>" for k, v in pairs
    )
    return f'<dl class="kv">{items}</dl>'


def render_page(
    title: str,
    sections: Sequence[dict],
    generated_at_kst: str,
    notes: Sequence[str] = (),
) -> str:
    """sections의 html은 호출자가 이미 이스케이프한(table/svg 등) 조각이어야 함."""
    secs = "".join(
        f"<section><h2>{escape(str(s['heading']))}</h2>{s['html']}</section>"
        for s in sections
    )
    note_html = ""
    if notes:
        note_html = (
            '<ul class="notes">'
            + "".join(f"<li>{escape(str(n))}</li>" for n in notes)
            + "</ul>"
        )
    return (
        "<!DOCTYPE html>\n"
        '<html lang="ko"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="color-scheme" content="light dark">'
        f"<title>{escape(str(title))}</title><style>{CSS}</style></head><body><main>"
        f"<h1>{escape(str(title))}</h1>"
        f'<p class="meta">생성 시각: {escape(str(generated_at_kst))}</p>'
        f"{secs}{note_html}"
        f"<footer>{escape(DISCLAIMER)}</footer>"
        "</main></body></html>\n"
    )
