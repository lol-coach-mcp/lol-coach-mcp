"""뷰별 섹션 조립 (순수 함수, I/O 없음). 반환: [{"heading": str, "html": SafeHtml}]."""
from __future__ import annotations

from typing import Any, Sequence

from lol_coach_mcp import analysis, champions

from . import svg
from .render import SafeHtml, empty_state, kv_list, table

MA_WINDOW = 10
SEASON_CHAMPION_BARS = 8
CHAMPION_BAR_TOP = 5
GOLD_DIFF_MINUTES = tuple(range(1, 16))
LP_TABLE_LIMIT = 20
MA_METRICS = ("kda", "cs_per_min", "vision_per_min")
DASH = "-"

NO_DATA_GUIDE = "저장된 솔로랭크 경기가 없음. 경기를 진행한 뒤 sync_matches로 동기화한 다음 다시 열 수 있음."
NO_TIMELINE = "타임라인 데이터 없음"

Section = dict


def section(heading: str, html: SafeHtml) -> Section:
    return {"heading": heading, "html": html}


def moving_average(values: Sequence[float], window: int) -> list[float]:
    """창이 가득 찬 구간만 계산(앞쪽 부분 창은 제외)."""
    if window < 1 or len(values) < window:
        return []
    return [sum(values[i - window:i]) / window for i in range(window, len(values) + 1)]


def ma_series(label: str, values: Sequence[float], window: int) -> dict:
    """x는 이동평균 창이 끝나는 경기 번호(1부터)."""
    avg = moving_average(values, window)
    return {"label": label, "points": [(window + i, v) for i, v in enumerate(avg)]}


def gap_label(row: dict) -> str:
    if row["status"] != analysis.STATUS_OK:
        return row["status"]
    return "큰 차이" if row["strong_gap"] else "뚜렷한 차이" if row["clear_gap"] else "뚜렷하지 않은 차이"


def _num(v: Any, fmt: str = "{:.2f}") -> str:
    return DASH if v is None else fmt.format(v)


def _pct(v: float | None) -> str:
    return DASH if v is None else f"{v * 100:.1f}%"


def _rank_text(e: dict) -> str:
    return f"{e.get('tier', '')} {e.get('rank', '')} {e.get('leaguePoints', 0)}LP".strip()


def _guide(message: str) -> Section:
    return section("안내", empty_state(message))


# ---- overview ----

def overview_view(
    *,
    riot_id: str,
    rank_entries: Sequence[dict],
    results: Sequence[str],
    form: dict | None,
    goal_rows: Sequence[dict],
    queue_text: str = "솔로랭크",
) -> list[Section]:
    pairs: list[tuple[str, str]] = [("계정", riot_id)]
    names = {"RANKED_SOLO_5x5": "솔로랭크", "RANKED_FLEX_SR": "자유랭크"}
    for e in rank_entries:
        if e.get("queueType") in names:
            wins, losses = e.get("wins", 0), e.get("losses", 0)
            pairs.append((names[e["queueType"]], f"{_rank_text(e)} ({wins}승 {losses}패)"))
    if len(pairs) == 1:
        pairs.append(("랭크", "랭크 정보 없음(언랭크이거나 아직 조회하지 못함)"))
    out = [section("프로필", kv_list(pairs))]

    if not results:
        out.append(section("최근 20판 결과", empty_state(NO_DATA_GUIDE)))
    else:
        out.append(section(f"최근 {len(results)}판 결과", svg.result_strip(results)))

    if form is None or not form["games"]:
        out.append(section("폼 분석", empty_state(NO_DATA_GUIDE)))
    else:
        head = kv_list([
            ("기간", form["period_desc"]), ("큐", queue_text),
            ("포지션", f"{form['position']} ({form['games']}판 분석)"),
            ("비교 기준", form["ref_source"]),
        ])
        html = head + _evidence(form["rows"], form["weaknesses"], form["strengths"])
        if form.get("best_self_rows") is not None:
            html = html + kv_list([("참고 비교: " + form["best_self_ref_source"], "티어 기준선이 없어 함께 표시함")])
            html = html + _evidence(
                form["best_self_rows"], form["best_self_weaknesses"] or [],
                form["best_self_strengths"] or [],
            )
        out.append(section("폼 분석", html))

    if goal_rows:
        out.append(section("진행 중 과제", table(
            ["지표", "목표", "시작 시 평균", "진행", "현재 평균"],
            [[g["label"], g["target"], g["baseline"], g["progress"], g["current"]]
             for g in goal_rows],
        )))
    else:
        out.append(section("진행 중 과제", empty_state("진행 중인 집중 과제 없음")))
    return out


def _evidence(rows: Sequence[dict], weaknesses: Sequence[dict], strengths: Sequence[dict]) -> SafeHtml:
    """근거 표 + 약점·강점 표."""
    body = [
        [r["label"], _fmt(r, r["my_value"]), f"{r['my_sample']}판",
         _fmt(r, r["ref_value"]), _num(r["difference"], "{:+g}"),
         _num(r["margin"], "±{:g}"), gap_label(r)]
        for r in rows
    ]
    found = [["약점", w["label"], f"{w['difference']:+g}"] for w in weaknesses] + [
        ["강점", s["label"], f"{s['difference']:+g}"] for s in strengths
    ]
    return table(["지표", "내 값", "표본", "기준", "차이", "오차폭", "판정"], body) + (
        table(["구분", "지표", "차이"], found) if found
        else empty_state("약점·강점으로 판정된 지표 없음")
    )


def _fmt(row: dict, v: float | None) -> str:
    return analysis.format_metric(row["metric"], v)


# ---- season ----

def season_view(
    *,
    lp_rows: Sequence[dict],
    chrono: Sequence[Any],
    names: dict[str, str],
    period_desc: str,
) -> list[Section]:
    """lp_rows: [{at, days, tier, rank, lp}] (등록 이후, 시간순). chrono: 시간순 MatchStats."""
    out: list[Section] = []
    if lp_rows:
        chart = svg.line_chart(
            [{"label": "솔로랭크 LP(디비전 내)", "points": [(r["days"], r["lp"]) for r in lp_rows]}],
            x_label="첫 스냅샷 이후 일수", y_label="LP",
        )
        recent = list(lp_rows)[-LP_TABLE_LIMIT:]
        out.append(section("LP 추이", chart + table(
            ["시각(KST)", "티어", "LP"],
            [[r["at"], f"{r['tier']} {r['rank']}".strip(), r["lp"]] for r in recent],
        )))
    else:
        out.append(section("LP 추이", empty_state(
            "등록 이후 저장된 솔로랭크 LP 스냅샷이 없음. get_profile 호출이나 대시보드 개요를 열 때마다 스냅샷이 쌓임"
        )))

    games = list(chrono)
    series = analysis.metric_series(games)
    for metric in MA_METRICS:
        label = analysis.METRICS[metric][0]
        heading = f"{MA_WINDOW}판 이동평균: {label}"
        if len(games) < MA_WINDOW:
            out.append(section(heading, empty_state(
                f"{period_desc} 경기가 {MA_WINDOW}판 미만({len(games)}판)이라 이동평균을 계산할 수 없음"
                if games else NO_DATA_GUIDE)))
            continue
        out.append(section(heading, svg.line_chart(
            [ma_series(label, series[metric], MA_WINDOW)],
            x_label=f"경기 번호({period_desc})", y_label=label,
        )))

    counts: dict[str, int] = {}
    for s in games:
        counts[s.champion] = counts.get(s.champion, 0) + 1
    top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:SEASON_CHAMPION_BARS]
    out.append(section("챔피언별 판수", svg.bar_chart(
        [(names.get(k, k), float(n)) for k, n in top], y_label="판수",
    ) if top else empty_state(NO_DATA_GUIDE)))
    return out


# ---- champion ----

def champion_view(
    *, rows: Sequence[dict], period_desc: str, queue_text: str = "솔로랭크"
) -> list[Section]:
    if not rows:
        return [_guide(NO_DATA_GUIDE)]
    body = []
    for r in rows:
        o = r.get("opgg") or {}
        ok = o.get("status") == champions.STATUS_OK
        body.append([
            r["name"], champions.POSITION_NAMES.get(r["position"], r["position"]), r["games"],
            _pct(r["win_rate"]), _num(r["kda"]), _num(r["cs_per_min"], "{:.1f}"),
            _num(r["damage_share"], "{:.3f}"), r["status"],
            _pct(o.get("win_rate")) if ok else DASH,
            _num(o.get("win_rate_diff") * 100 if ok and o.get("win_rate_diff") is not None else None,
                 "{:+.1f}%p"),
        ])
    out = [section("챔피언 요약", kv_list([("기간", period_desc), ("큐", queue_text)]) + table(
        ["챔피언", "포지션", "판수", "승률", "KDA", "분당 CS", "딜 비중", "상태",
         "OP.GG 승률", "승률 차이"], body))]
    top = list(rows)[:CHAMPION_BAR_TOP]
    for heading, key, scale, ylabel in (
        ("상위 챔피언: 승률(%)", "win_rate", 100.0, "승률(%)"),
        ("상위 챔피언: KDA", "kda", 1.0, "KDA"),
        ("상위 챔피언: 분당 CS", "cs_per_min", 1.0, "분당 CS"),
    ):
        items = [(r["name"], r[key] * scale) for r in top if r[key] is not None]
        out.append(section(heading, svg.bar_chart(items, y_label=ylabel)))
    return out


# ---- match ----

def match_view(
    *,
    summary: Sequence[tuple[str, Any]],
    gold_points: Sequence[dict] | None,
    death_points: Sequence[dict] | None,
    minimap_uri: str | None,
    objective_events: Sequence[dict] | None,
) -> list[Section]:
    """None은 해당 데이터 없음(타임라인 없음 등)."""
    out = [section("경기 요약", kv_list(summary))]
    if gold_points:
        out.append(section("15분까지 골드 차", svg.line_chart(
            [{"label": "내 골드 − 라인 상대 골드", "points": [(p["minute"], p["gold_diff"]) for p in gold_points]}],
            x_label="분", y_label="골드 차", y_zero_line=True,
        )))
    else:
        out.append(section("15분까지 골드 차", empty_state(NO_TIMELINE)))
    if death_points is None:
        out.append(section("데스 위치", empty_state(NO_TIMELINE)))
    elif not death_points:
        out.append(section("데스 위치", empty_state("데스 없음")))
    else:
        out.append(section("데스 위치", svg.scatter_on_image(
            [{"x": d["x"], "y": d["y"], "label": d["time"]} for d in death_points], minimap_uri,
        )))
    if objective_events is None:
        out.append(section("오브젝트", empty_state(NO_TIMELINE)))
    else:
        out.append(section("오브젝트", table(
            ["시간", "오브젝트", "팀", "내 참여", "사망 상태"],
            [[e["time"], e["type"] + (f"/{e['sub_type']}" if e.get("sub_type") else ""), e["team"],
              "O" if e["participated"] else "X", "O" if e["dead_at_time"] else "X"]
             for e in objective_events],
        )))
    return out
