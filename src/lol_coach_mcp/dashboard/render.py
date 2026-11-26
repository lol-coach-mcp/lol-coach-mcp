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

KOREAN_SERIF = '"Gowun Batang","NanumMyeongjo","Noto Serif KR","AppleMyungjo","Batang",Georgia,serif'

# 리그 오브 레전드 클라이언트풍 테마(남색 바탕·금색 테두리·헥스 장식). 외부 폰트·이미지 없이 CSS만 사용.
CSS = """
:root{--bg:#010A13;--bg2:#0A1428;--card:#091428;--card2:#0A1E32;--fg:#F0E6D2;--muted:#A09B8C;
--gold:#C8AA6E;--gold2:#C89B3C;--gold3:#785A28;--gold4:#463714;--border:#463714;
--accent:#C8AA6E;--win:#0AC8B9;--win2:#0397AB;--loss:#E84057;--remake:#5B5A56;
--grid:#1E2D3D;--map:#0A1E32;
--serif:"Cinzel","Beaufort for LOL","Marcellus","Cormorant SC","Trajan Pro",--KSERIF;
--title:"Beaufort for LOL",--KSERIF;
}
*{box-sizing:border-box}
html{background:var(--bg)}
body{margin:0;color:var(--fg);line-height:1.55;min-height:100vh;
font-family:-apple-system,"Segoe UI","Malgun Gothic","Apple SD Gothic Neo","Noto Sans KR",
"Noto Sans CJK KR",sans-serif;
background:radial-gradient(ellipse 80% 50% at 50% 0,rgba(10,50,60,.55),transparent 70%),
radial-gradient(ellipse 60% 40% at 50% 100%,rgba(70,55,20,.25),transparent 70%),
linear-gradient(180deg,#0A1428 0,#010A13 420px)}
main{max-width:1040px;margin:0 auto;padding:28px 20px 16px}
.hero{display:flex;align-items:center;gap:20px;padding:22px 26px;margin:0 0 8px;position:relative;
background:linear-gradient(180deg,rgba(10,20,40,.92),rgba(1,10,19,.92));border:1px solid var(--gold3);
box-shadow:inset 0 0 0 3px var(--bg),inset 0 0 0 4px rgba(200,170,110,.18),0 0 40px rgba(0,0,0,.5)}
.hero::after{content:"";position:absolute;left:24px;right:24px;bottom:-1px;height:2px;
background:linear-gradient(90deg,transparent,var(--gold) 20%,#F0E6D2 50%,var(--gold) 80%,transparent)}
.crest{flex:0 0 auto;width:64px;height:72px}
.crest svg{width:64px;height:72px}
.eyebrow{margin:0;color:var(--gold);font:600 .72rem/1.2 var(--serif);letter-spacing:.32em;text-transform:uppercase}
h1{font:700 1.65rem/1.3 var(--title);font-variant-numeric:lining-nums;margin:4px 0 2px;letter-spacing:.04em;color:var(--fg);
text-shadow:0 1px 0 #000,0 0 18px rgba(200,170,110,.25)}
h2{display:flex;align-items:center;gap:10px;font:700 1.08rem/1.3 var(--serif);margin:0 0 14px;
padding-bottom:10px;color:var(--gold);letter-spacing:.08em;text-transform:uppercase;position:relative}
h2::before{content:"";flex:0 0 auto;width:12px;height:14px;background:var(--gold2);
clip-path:polygon(50% 0,100% 25%,100% 75%,50% 100%,0 75%,0 25%);box-shadow:0 0 6px var(--gold)}
h2::after{content:"";position:absolute;left:0;right:0;bottom:0;height:1px;
background:linear-gradient(90deg,var(--gold3),rgba(120,90,40,.15) 70%,transparent)}
section{position:relative;background:linear-gradient(180deg,var(--card2),var(--card) 140px);
border:1px solid var(--border);padding:20px 22px;margin:22px 0;overflow-x:auto;
box-shadow:inset 0 0 0 3px rgba(1,10,19,.6),inset 0 0 0 4px rgba(200,170,110,.08),0 6px 24px rgba(0,0,0,.45)}
section::before{content:"";position:absolute;inset:-1px;pointer-events:none;
background:linear-gradient(var(--gold),var(--gold)) 0 0/16px 2px,
linear-gradient(var(--gold),var(--gold)) 0 0/2px 16px,
linear-gradient(var(--gold),var(--gold)) 100% 0/16px 2px,
linear-gradient(var(--gold),var(--gold)) 100% 0/2px 16px,
linear-gradient(var(--gold),var(--gold)) 0 100%/16px 2px,
linear-gradient(var(--gold),var(--gold)) 0 100%/2px 16px,
linear-gradient(var(--gold),var(--gold)) 100% 100%/16px 2px,
linear-gradient(var(--gold),var(--gold)) 100% 100%/2px 16px;background-repeat:no-repeat}
section::after{content:"";position:absolute;top:-4px;left:50%;width:8px;height:8px;margin-left:-4px;
background:var(--gold);transform:rotate(45deg);box-shadow:0 0 0 2px var(--bg)}
.meta{color:var(--muted);font-size:.85rem;margin:2px 0 0}
.notes,footer{color:var(--muted);font-size:.85rem}
ul.notes{list-style:none;margin:22px 0 0;padding:12px 18px;border:1px solid var(--gold4);
border-left:3px solid var(--gold2);background:rgba(10,20,40,.7)}
ul.notes li{position:relative;padding-left:18px}
ul.notes li::before{content:"";position:absolute;left:0;top:.5em;width:8px;height:9px;background:var(--gold3);
clip-path:polygon(50% 0,100% 25%,100% 75%,50% 100%,0 75%,0 25%)}
table{border-collapse:collapse;width:100%;font-size:.92rem;margin:12px 0 4px;
font-variant-numeric:tabular-nums}
thead th{background:linear-gradient(180deg,rgba(70,55,20,.45),rgba(70,55,20,.15));
border-top:1px solid var(--gold3);border-bottom:1px solid var(--gold3)}
th,td{text-align:left;padding:7px 12px;border-bottom:1px solid rgba(120,90,40,.28)}
th{color:var(--gold);font-weight:600;font-size:.82rem;letter-spacing:.06em;white-space:nowrap}
tbody tr:nth-child(even) td{background:rgba(10,30,50,.45)}
tbody tr:hover td{background:rgba(200,170,110,.08)}
dl.kv{display:grid;grid-template-columns:max-content 1fr;gap:6px 18px;margin:0 0 4px}
dl.kv dt{color:var(--muted);position:relative;padding-left:16px}
dl.kv dt::before{content:"";position:absolute;left:0;top:.55em;width:7px;height:8px;background:var(--gold3);
clip-path:polygon(50% 0,100% 25%,100% 75%,50% 100%,0 75%,0 25%)}
dl.kv dd{margin:0;color:var(--fg)}
.badge-row{display:flex;align-items:center;gap:22px;flex-wrap:wrap}
.badge-row dl.kv{flex:1 1 320px}
.hexbadge{flex:0 0 auto;width:92px;text-align:center}
.hexbadge svg{width:92px;height:104px;margin:0 auto}
.hexbadge .hb-cap{display:block;margin-top:4px;color:var(--muted);font-size:.75rem;letter-spacing:.12em}
svg .hb-outer{fill:none;stroke:var(--gold);stroke-width:2}
svg .hb-inner{fill:var(--bg2);stroke:var(--gold3);stroke-width:1.5}
svg .hb-gem{fill:var(--gold2)}
svg text.hb-text{fill:var(--fg);font-family:var(--serif);font-weight:700;stroke:none;letter-spacing:.04em}
.hb-win svg .hb-outer{stroke:var(--win)}.hb-win svg .hb-gem{fill:var(--win)}
.hb-loss svg .hb-outer{stroke:var(--loss)}.hb-loss svg .hb-gem{fill:var(--loss)}
.empty-state{color:var(--muted);padding:14px 16px;border:1px dashed var(--gold3);background:rgba(1,10,19,.5);margin:8px 0}
svg{max-width:100%;height:auto;display:block}
svg text{fill:var(--fg);font-size:14px;paint-order:stroke;stroke:var(--card);stroke-width:3px;stroke-linejoin:round}
svg .axis{stroke:var(--gold3);stroke-width:1}svg .grid-line{stroke:var(--grid);stroke-width:1}
svg .zero-line{stroke:var(--muted);stroke-dasharray:4 3;stroke-width:1}
svg[aria-label="지도"]{max-width:640px;margin:4px auto;border:1px solid var(--gold3);
box-shadow:0 0 0 3px var(--bg),0 0 0 4px rgba(200,170,110,.3)}
svg .map-bg{fill:var(--map);stroke:var(--gold3);stroke-width:2}
svg .dot{fill:var(--loss);stroke:#F0E6D2;stroke-width:1.5}
svg .bar{fill:var(--gold2);fill:url(#lc-gold);stroke:var(--gold);stroke-width:1}
svg.defs{position:absolute;width:0;height:0;overflow:hidden}
svg.hexbg{position:fixed;inset:0;width:100%;height:100%;max-width:none;z-index:-1;pointer-events:none}
svg.hexbg path{fill:none;stroke:#C8AA6E;stroke-opacity:.05}
svg .result-win{fill:var(--win2);stroke:var(--win);stroke-width:1.5}
svg .result-loss{fill:#9E2A3C;stroke:var(--loss);stroke-width:1.5}
svg .result-remake{fill:var(--remake);stroke:var(--muted);stroke-width:1.5}
svg[aria-label="경기 결과"] rect{clip-path:polygon(50% 0,100% 25%,100% 75%,50% 100%,0 75%,0 25%)}
svg polyline.s0{stroke:var(--gold)}svg polyline.s1{stroke:var(--win)}
svg polyline.s2{stroke:var(--loss)}svg polyline.s3{stroke:var(--muted)}
svg polyline{fill:none;stroke-width:2.5;stroke-linejoin:round}
svg .marker-s0,svg .legend-s0{fill:var(--gold)}svg .marker-s1,svg .legend-s1{fill:var(--win)}
svg .marker-s2,svg .legend-s2{fill:var(--loss)}svg .marker-s3,svg .legend-s3{fill:var(--muted)}
svg circle[class^="marker-"]{stroke:var(--card);stroke-width:1.5}
footer{margin:30px 0 8px;padding-top:16px;position:relative;font-size:.78rem;opacity:.85}
footer::before{content:"";position:absolute;top:0;left:0;right:0;height:1px;
background:linear-gradient(90deg,transparent,var(--gold3),transparent)}
@media (max-width:600px){main{padding:10px}section{padding:14px}table{font-size:.85rem}
.hero{padding:14px}.crest{display:none}}
""".replace("--KSERIF", KOREAN_SERIF)

DEFS_SVG = (
    '<svg class="defs" aria-hidden="true" focusable="false"><defs>'
    '<linearGradient id="lc-gold" x1="0" y1="0" x2="0" y2="1">'
    '<stop offset="0" stop-color="#C8AA6E"/><stop offset="1" stop-color="#785A28"/>'
    "</linearGradient></defs></svg>"
    # 배경 헥스 무늬(외부 이미지 없이 인라인 SVG 패턴)
    '<svg class="hexbg" aria-hidden="true" focusable="false"><defs>'
    '<pattern id="lc-hex" width="56" height="97" patternUnits="userSpaceOnUse">'
    '<path d="M28 0l28 16.2v32.4L28 64.8 0 48.6V16.2zM28 64.8V97"/></pattern></defs>'
    '<rect width="100%" height="100%" fill="url(#lc-hex)"/></svg>'
)

CREST_SVG = (
    '<svg viewBox="0 0 64 72" aria-hidden="true" focusable="false">'
    '<polygon points="32,2 62,19 62,53 32,70 2,53 2,19" fill="#010A13" stroke="#C8AA6E" stroke-width="2"/>'
    '<polygon points="32,9 56,23 56,49 32,63 8,49 8,23" fill="none" stroke="#785A28" stroke-width="1.5"/>'
    '<polygon points="32,18 46,36 32,54 18,36" fill="#0A1E32" stroke="#C89B3C" stroke-width="1.5"/>'
    '<polygon points="32,27 39,36 32,45 25,36" fill="#0AC8B9"/>'
    '<line x1="32" y1="2" x2="32" y2="12" stroke="#C8AA6E" stroke-width="1.5"/>'
    '<line x1="32" y1="60" x2="32" y2="70" stroke="#C8AA6E" stroke-width="1.5"/>'
    "</svg>"
)


class SafeHtml(str):
    """이미 이스케이프된 HTML 조각 표식. 일반 str은 render_page에서 이스케이프됨."""

    __slots__ = ()

    def __add__(self, other: str) -> str:  # type: ignore[override]
        joined = str.__add__(self, other)
        return SafeHtml(joined) if isinstance(other, SafeHtml) else str(joined)


def empty_state(message: str) -> SafeHtml:
    return SafeHtml(f'<p class="empty-state">{escape(str(message))}</p>')


def hex_badge(text: Any, caption: Any = "", tone: str = "") -> SafeHtml:
    """장식용 육각형 배지(티어·결과 표시). tone: ""(금색)/"win"/"loss"."""
    label = str(text)
    cls = f"hexbadge hb-{tone}" if tone else "hexbadge"
    cap = f'<span class="hb-cap">{escape(str(caption))}</span>' if caption else ""
    head, _, tail = label.partition(" ")
    if tail:  # "PLATINUM IV" → 두 줄(티어 / 디비전)
        lines = [(head, 13, 46), (tail, 22, 70)]
    elif any(ord(c) > 127 for c in label) and len(label) <= 2:
        lines = [(label, 34, 64)]
    else:
        lines = [(label, 24 if len(label) <= 3 else 13, 60 if len(label) <= 3 else 57)]
    texts = "".join(
        f'<text class="hb-text" x="46" y="{y}" text-anchor="middle" style="font-size:{fs}px"'
        + (f' textLength="62" lengthAdjust="spacingAndGlyphs"' if len(t) > 5 else "")
        + f">{escape(t)}</text>"
        for t, fs, y in lines
    )
    return SafeHtml(
        f'<div class="{cls}" aria-hidden="true">'
        '<svg viewBox="0 0 92 104" focusable="false">'
        '<polygon class="hb-outer" points="46,2 90,27 90,77 46,102 2,77 2,27"/>'
        '<polygon class="hb-inner" points="46,10 83,31 83,73 46,94 9,73 9,31"/>'
        '<polygon class="hb-gem" points="46,4 50,10 46,16 42,10"/>'
        '<polygon class="hb-gem" points="46,88 50,94 46,100 42,94"/>'
        f"{texts}</svg>{cap}</div>"
    )


def badge_row(badge: SafeHtml, body: SafeHtml) -> SafeHtml:
    return SafeHtml(f'<div class="badge-row">{badge}{body}</div>')


def table(headers: Sequence[Any], rows: Iterable[Sequence[Any]]) -> SafeHtml:
    rows = list(rows)
    if not rows:
        return empty_state("표시할 데이터 없음")
    head = "".join(f"<th>{escape(str(h))}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{escape(str(c))}</td>" for c in row) + "</tr>"
        for row in rows
    )
    return SafeHtml(f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>")


def kv_list(pairs: Iterable[tuple[Any, Any]]) -> SafeHtml:
    pairs = list(pairs)
    if not pairs:
        return empty_state("표시할 데이터 없음")
    items = "".join(
        f"<dt>{escape(str(k))}</dt><dd>{escape(str(v))}</dd>" for k, v in pairs
    )
    return SafeHtml(f'<dl class="kv">{items}</dl>')


def render_page(
    title: str,
    sections: Sequence[dict],
    generated_at_kst: str,
    notes: Sequence[str] = (),
) -> str:
    """sections의 html이 SafeHtml이면 그대로, 일반 str이면 이스케이프."""
    secs = "".join(
        f"<section><h2>{escape(str(s['heading']))}</h2>"
        f"{s['html'] if isinstance(s['html'], SafeHtml) else escape(str(s['html']))}"
        "</section>"
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
        '<meta name="color-scheme" content="dark">'
        f"<title>{escape(str(title))}</title><style>{CSS}</style></head><body>{DEFS_SVG}<main>"
        f'<header class="hero"><div class="crest">{CREST_SVG}</div><div>'
        '<p class="eyebrow">LoL Coach · Dashboard</p>'
        f"<h1>{escape(str(title))}</h1>"
        f'<p class="meta">생성 시각: {escape(str(generated_at_kst))}</p></div></header>'
        f"{secs}{note_html}"
        f"<footer>{escape(DISCLAIMER)}</footer>"
        "</main></body></html>\n"
    )
