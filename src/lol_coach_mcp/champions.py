"""내 챔피언 집계 (순수 함수). OP.GG·Data Dragon 데이터는 인자로 받는다."""
from __future__ import annotations

from lol_coach_mcp import analysis
from lol_coach_mcp.stats import MatchStats

MIN_GAMES = 5  # 챔피언별 판단 보류 기준
CANDIDATE_MIN_GAMES = 10  # 주력 후보 근거 행 최소 판수
OPGG_TOP_N = 5  # 요청당 OP.GG 조회 챔피언 수(판수 상위)
COUNTER_TOP_N = 3
POOL_NARROW_TOP3_SHARE = 0.8
POOL_WIDE_MIN_CHAMPIONS = 10
POOL_WIDE_TOP3_SHARE = 0.5
SIMILAR_MAX = 5
SIMILAR_MAX_TIER = 2
SIMILAR_EXCLUDE_GAMES = 10
PROGRESS_CHUNK = 5

STATUS_OK = "ok"
STATUS_HOLD = "판단 보류"
STATUS_NO_OPGG = "OP.GG 데이터 없음"
STATUS_NO_DDRAGON = "Data Dragon 데이터 없음"
STATUS_PARTIAL = "부분 구간"

LABEL_NARROW = "좁음"
LABEL_WIDE = "넓음"
LABEL_MID = "보통"

POSITION_NAMES = {
    "TOP": "탑", "JUNGLE": "정글", "MIDDLE": "미드", "BOTTOM": "원딜", "UTILITY": "서폿",
    "UNKNOWN": "알 수 없음",
}


def _norm(s: str) -> str:
    return "".join(s.split()).lower()


def resolve_champion(
    query: str, ddragon: dict[str, dict] | None, played_keys: list[str]
) -> str | None:
    """한국어 이름 또는 영문 키 → Data Dragon 챔피언 키. 못 찾으면 None."""
    q = _norm(query)
    played = {_norm(k): k for k in played_keys}  # Match-V5 표기(FiddleSticks)가 Data Dragon(Fiddlesticks)보다 우선
    for key, info in (ddragon or {}).items():
        if _norm(key) == q or _norm(info.get("name", "")) == q:
            return played.get(_norm(key), key)
    for key in played_keys:  # Data Dragon 없이도 영문 키는 내 경기 기록으로 해석
        if _norm(key) == q:
            return key
    return None


def display_name(key: str, ddragon: dict[str, dict] | None) -> str:
    return ((ddragon or {}).get(key) or {}).get("name") or key


def group_by_champion(stats: list[MatchStats]) -> dict[str, list[MatchStats]]:
    out: dict[str, list[MatchStats]] = {}
    for s in stats:
        out.setdefault(s.champion, []).append(s)
    return out


def main_position(games: list[MatchStats]) -> str:
    return analysis.main_position(games)


def aggregate(games: list[MatchStats]) -> dict:
    """한 챔피언(또는 구간)의 판수·승률·KDA(합계 기준)·분당 CS·딜 비중."""
    n = len(games)
    wins = sum(1 for s in games if s.win)
    k = sum(s.kills for s in games)
    a = sum(s.assists for s in games)
    d = sum(s.deaths for s in games)
    series = analysis.metric_series(games)
    mean = {m: (sum(v) / len(v) if v else None) for m, v in series.items()}
    return {
        "games": n,
        "wins": wins,
        "win_rate": round(wins / n, 3) if n else None,
        "kda": round((k + a) / max(d, 1), 2) if n else None,
        "cs_per_min": analysis.round_metric("cs_per_min", mean["cs_per_min"]),
        "damage_share": analysis.round_metric("damage_share", mean["damage_share"]),
        "deaths": analysis.round_metric("deaths", mean["deaths"]),
    }


def champion_row(key: str, games: list[MatchStats], ddragon: dict[str, dict] | None) -> dict:
    row = {"champion": key, "name": display_name(key, ddragon),
           "position": main_position(games), **aggregate(games)}
    row["status"] = STATUS_OK if len(games) >= MIN_GAMES else STATUS_HOLD
    row["opgg"] = None
    return row


def overview_rows(stats: list[MatchStats], ddragon: dict[str, dict] | None) -> list[dict]:
    groups = group_by_champion(stats)
    rows = [champion_row(k, g, ddragon) for k, g in groups.items()]
    return sorted(rows, key=lambda r: (-r["games"], r["champion"]))


def opgg_block(row: dict, analysis_data: dict | None) -> dict:
    """내 행과 OP.GG champion_analysis(`data`)를 비교한 블록. 없으면 '데이터 없음'."""
    avg = ((analysis_data or {}).get("summary") or {}).get("average_stats")
    if not avg or avg.get("win_rate") is None:
        return {"status": STATUS_NO_OPGG}
    wr, kda = avg.get("win_rate"), avg.get("kda")
    block = {
        "status": STATUS_OK,
        "win_rate": wr,
        "kda": kda,
        "play": avg.get("play"),
        "tier": avg.get("tier"),
        "win_rate_diff": None,
        "kda_diff": None,
    }
    if row["status"] == STATUS_OK:
        block["win_rate_diff"] = round(row["win_rate"] - wr, 3)
        if kda is not None:
            block["kda_diff"] = round(row["kda"] - kda, 2)
    return block


def main_candidates(rows: list[dict]) -> list[dict]:
    """주력 후보 근거 행: 판수 ≥ 10 이고 (내 승률 ≥ OP.GG 평균 승률 또는 내 KDA ≥ OP.GG KDA)."""
    out = []
    for r in rows:
        o = r.get("opgg")
        if r["games"] < CANDIDATE_MIN_GAMES or not o or o.get("status") != STATUS_OK:
            continue
        reasons = []
        if o["win_rate_diff"] is not None and o["win_rate_diff"] >= 0:
            reasons.append("내 승률이 OP.GG 평균 이상")
        if o["kda_diff"] is not None and o["kda_diff"] >= 0:
            reasons.append("내 KDA가 OP.GG 평균 이상")
        if reasons:
            out.append({
                "champion": r["champion"], "name": r["name"], "games": r["games"],
                "win_rate": r["win_rate"], "kda": r["kda"],
                "win_rate_diff": o["win_rate_diff"], "kda_diff": o["kda_diff"],
                "evidence": reasons,
            })
    return out


def counters(analysis_data: dict | None) -> dict | None:
    """OP.GG 카운터 상위 3: weak(내 챔피언이 상대하기 어려운), strong(상대하기 쉬운)."""
    if not analysis_data:
        return None

    def pick(rows, rate_key):
        return [{"champion": c.get("champion_name"), "play": c.get("play"), rate_key: c.get("win_rate")}
                for c in (rows or [])[:COUNTER_TOP_N]]

    return {
        "hard_against": pick(analysis_data.get("weak_counters"), "opponent_win_rate"),
        "easy_against": pick(analysis_data.get("strong_counters"), "my_champion_win_rate"),
    }


def pool_summary(stats: list[MatchStats]) -> dict:
    groups = group_by_champion(stats)
    total = len(stats)
    counts = sorted((len(g) for g in groups.values()), reverse=True)
    top3 = sum(counts[:3])
    share = round(top3 / total, 3) if total else None
    if total < MIN_GAMES:
        label = STATUS_HOLD
    elif share >= POOL_NARROW_TOP3_SHARE:
        label = LABEL_NARROW
    elif len(groups) >= POOL_WIDE_MIN_CHAMPIONS and share < POOL_WIDE_TOP3_SHARE:
        label = LABEL_WIDE
    else:
        label = LABEL_MID
    by_pos: dict[str, set[str]] = {}
    for s in stats:
        by_pos.setdefault(s.position, set()).add(s.champion)
    positions = [
        {"position": p, "position_name": POSITION_NAMES.get(p, p), "champions": len(c)}
        for p, c in sorted(by_pos.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    ]
    return {"games": total, "champion_count": len(groups), "top3_games": top3,
            "top3_share": share, "label": label, "by_position": positions}


def focus_rows(games: list[MatchStats]) -> list[dict]:
    """승리 판 vs 패배 판 지표 근거 행(analysis.compare_metric 재사용)."""
    wins = [s for s in games if s.win]
    losses = [s for s in games if not s.win]
    w, l = analysis.metric_series(wins), analysis.metric_series(losses)
    pos = main_position(games)
    rows = []
    for m in analysis.METRICS:
        if m == "win":
            continue
        rows.append(dict(analysis.compare_metric(m, w[m], l[m], "승리 판 vs 패배 판", pos)))
    return rows


def similar_candidates(
    my_stats: list[MatchStats],
    ddragon: dict[str, dict],
    lane_meta: list[dict],
) -> dict:
    """내 최다 챔피언과 Data Dragon tags가 겹치고 OP.GG 메타 tier ≤ 2인 챔피언."""
    groups = group_by_champion(my_stats)
    top_key = max(groups, key=lambda k: (len(groups[k]), k))
    top_tags = set((ddragon.get(top_key) or {}).get("tags", []))
    played10 = {k for k, g in groups.items() if len(g) >= SIMILAR_EXCLUDE_GAMES}
    by_name = {_norm(v["name"]): (k, v) for k, v in ddragon.items()}
    found = []
    for m in lane_meta:
        hit = by_name.get(_norm(m.get("champion", "")))
        if hit is None:
            continue
        key, info = hit
        tier = m.get("tier")
        shared = sorted(top_tags & set(info.get("tags", [])))
        if key == top_key or key in played10 or not shared:
            continue
        if tier is None or tier > SIMILAR_MAX_TIER:
            continue
        found.append({
            "champion": key, "name": info["name"], "shared_tags": shared,
            "tier": tier, "rank": m.get("rank"), "win_rate": m.get("win_rate"),
            "pick_rate": m.get("pick_rate"), "kda": m.get("kda"), "play": m.get("play"),
            "my_games": len(groups.get(key, [])),
        })
    found.sort(key=lambda c: (-len(c["shared_tags"]), c["tier"], c["rank"] or 9999, c["champion"]))
    return {"base_champion": top_key, "base_name": display_name(top_key, ddragon),
            "base_tags": sorted(top_tags),
            "candidates": found[:SIMILAR_MAX]}


def progress_chunks(games: list[MatchStats]) -> list[dict]:
    """시간순 5판 단위 묶음. 마지막 묶음이 5판 미만이면 '부분 구간'."""
    ordered = sorted(games, key=lambda s: s.game_creation)
    out = []
    for i in range(0, len(ordered), PROGRESS_CHUNK):
        chunk = ordered[i:i + PROGRESS_CHUNK]
        out.append({
            "chunk": i // PROGRESS_CHUNK + 1,
            "first_match_id": chunk[0].match_id,
            "game_creation": chunk[0].game_creation,
            **aggregate(chunk),
            "status": STATUS_OK if len(chunk) >= PROGRESS_CHUNK else STATUS_PARTIAL,
        })
    return out

