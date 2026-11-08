"""매치업 집계 (순수 함수). 저장된 경기·타임라인은 호출 측이 로더로 넘긴다."""
from __future__ import annotations

from collections.abc import Callable

from lol_coach_mcp import champions, review
from lol_coach_mcp.stats import MatchStats

MATCHUP_MIN_GAMES = 3  # 상대별 판단 보류 기준
CANDIDATE_MIN_GAMES = 5  # 챔피언 선택 모드의 후보 최소 판수(주 포지션)
OPGG_CANDIDATE_LIMIT = 5  # 챔피언 선택 모드에서 OP.GG를 조회할 후보 수
MAX_ENEMIES = 5
GOLD_DIFF_MINUTE = 15

STATUS_UNKNOWN = "알 수 없는 챔피언"
STATUS_NOT_QUERIED = "OP.GG 조회 제외"
ADVANTAGE_EVEN = "비슷함"
ADVANTAGE_MINE = "내 챔피언 우세"
ADVANTAGE_THEIRS = "상대 우세"
PLAY_STYLE_LABELS = {"even": "비슷함", "aggressive": "공격적", "defensive": "수비적"}
RELATION_EASY = "상대하기 쉬움"
RELATION_HARD = "상대하기 어려움"
RELATION_NOT_LISTED = "카운터 목록에 없음"


def record_status(games: int) -> str:
    return champions.STATUS_OK if games >= MATCHUP_MIN_GAMES else champions.STATUS_HOLD


def lane_opponent(match: dict | None, puuid: str) -> str | None:
    """같은 포지션(teamPosition)의 상대 팀 챔피언 이름. 유일하지 않거나 정보가 없으면 None."""
    ps = ((match or {}).get("info") or {}).get("participants") or []
    me = next((p for p in ps if p.get("puuid") == puuid), None)
    if me is None or not me.get("teamPosition"):
        return None
    rivals = [p for p in ps if p.get("teamId") != me.get("teamId")
              and p.get("teamPosition") == me["teamPosition"]]
    return rivals[0].get("championName") if len(rivals) == 1 else None


def lane_entries(
    stats: list[MatchStats], load_match: Callable[[str], dict | None], puuid: str
) -> list[dict]:
    """판마다 {stats, opponent(상대 챔피언 키|None), match}."""
    out = []
    for s in stats:
        match = load_match(s.match_id)
        out.append({"stats": s, "opponent": lane_opponent(match, puuid), "match": match})
    return out


def gold_diff(entry: dict, timeline: dict | None, puuid: str) -> int | None:
    """15분 골드차(나 - 라인 상대). 타임라인이 없거나 15분 전에 끝났으면 None."""
    if not timeline or not entry["match"]:
        return None
    try:
        res = review.lane_checkpoints(entry["match"], timeline, puuid, (GOLD_DIFF_MINUTE,))
    except (ValueError, KeyError, TypeError, IndexError):
        return None
    pts = res.get("points") or []
    return pts[0]["gold_diff"] if pts else None


def record(
    entries: list[dict], load_timeline: Callable[[str], dict | None], puuid: str
) -> dict:
    """내 기록: 판수·승수·승률·15분 골드차 평균(타임라인 있는 판만)과 그 판수."""
    n = len(entries)
    wins = sum(1 for e in entries if e["stats"].win)
    diffs = []
    for e in entries:
        d = gold_diff(e, load_timeline(e["stats"].match_id), puuid)
        if d is not None:
            diffs.append(d)
    return {
        "games": n,
        "wins": wins,
        "win_rate": round(wins / n, 3) if n else None,
        "status": record_status(n),
        "timeline_games": len(diffs),
        "avg_gold_diff_15": round(sum(diffs) / len(diffs), 1) if diffs else None,
    }


def advantage_label(value: str | None, mine: str | None, theirs: str | None) -> str | None:
    """OP.GG 우세 값(EVEN 또는 챔피언 이름) → 한국어 라벨. 알 수 없으면 None(표시 생략)."""
    if not value:
        return None
    if value.upper() == "EVEN":
        return ADVANTAGE_EVEN
    if mine and value == mine:
        return ADVANTAGE_MINE
    if theirs and value == theirs:
        return ADVANTAGE_THEIRS
    return None


def dedupe_enemies(resolved: list[tuple[str, str | None]]) -> list[tuple[str, str | None]]:
    """해석된 키(못 찾은 입력은 정규화한 이름) 기준으로 중복 제거, 첫 입력 순서 유지."""
    seen, out = set(), []
    for q, k in resolved:
        ident = k if k is not None else "?" + champions._norm(q)
        if ident not in seen:
            seen.add(ident)
            out.append((q, k))
    return out


def matchup_block(data: dict | None) -> dict:
    """OP.GG lane_matchup 요약. average_stats는 매치업이 아니라 내 챔피언 전체 통계."""
    if not data:
        return {"status": champions.STATUS_NO_OPGG}
    avg = data.get("average_stats") or {}
    return {
        "status": champions.STATUS_OK,
        "matchup": data.get("matchup"),
        "my_champion_overall": {
            "play": avg.get("play"), "win_rate": avg.get("win_rate"),
            "kda": None if avg.get("kda") is None else round(avg["kda"], 2), "tier": avg.get("tier"),
        },
        "lane_advantage": advantage_label(
            data.get("lane_advantage_champion"), data.get("my_champion"), data.get("opponent_champion")),
        "solo_kill_advantage": advantage_label(
            data.get("lane_solo_kill_advantage_champion"), data.get("my_champion"),
            data.get("opponent_champion")),
        "recommended_play_style": PLAY_STYLE_LABELS.get(
            str(data.get("recommended_play_style") or "").lower()),
    }


def counter_info(analysis_data: dict | None, enemy_id: int | None, enemy_name: str | None) -> dict | None:
    """champion_analysis의 strong/weak_counters에서 상대를 찾음. 분석 자체가 없으면 None."""
    if not analysis_data:
        return None
    for relation, rows, mine_is_rate in (
        (RELATION_EASY, analysis_data.get("strong_counters"), True),
        (RELATION_HARD, analysis_data.get("weak_counters"), False),
    ):
        for c in rows or []:
            if (enemy_id is not None and c.get("champion_id") == enemy_id) or (
                enemy_name and c.get("champion_name") == enemy_name
            ):
                wr = c.get("win_rate")
                other = None if wr is None else round(1 - wr, 3)
                return {
                    "relation": relation, "play": c.get("play"),
                    # strong_counters.win_rate = 내 챔피언 승률, weak_counters.win_rate = 상대 승률
                    "my_win_rate": wr if mine_is_rate else other,
                    "opponent_win_rate": other if mine_is_rate else wr,
                }
    return {"relation": RELATION_NOT_LISTED, "play": None,
            "my_win_rate": None, "opponent_win_rate": None}


def pick_candidates(picked: list[MatchStats], main_pos: str) -> list[dict]:
    """주 포지션에서 CANDIDATE_MIN_GAMES판 이상 한 챔피언(판수 내림차순)."""
    groups = champions.group_by_champion([s for s in picked if s.position == main_pos])
    out = [{"champion": k, "games": len(g), "wins": sum(1 for s in g if s.win)}
           for k, g in groups.items() if len(g) >= CANDIDATE_MIN_GAMES]
    return sorted(out, key=lambda c: (-c["games"], c["champion"]))


def sort_pick_rows(rows: list[dict]) -> list[dict]:
    """내 판수 내림차순, 같으면 승률 내림차순."""
    return sorted(rows, key=lambda r: (
        -r["games"], -(r["win_rate"] if r["win_rate"] is not None else -1.0),
        r["candidate"], r["enemy"]))
