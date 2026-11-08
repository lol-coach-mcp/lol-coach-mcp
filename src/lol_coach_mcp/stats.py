"""Per-match statistics extraction and rule-based tagging."""

from __future__ import annotations

from dataclasses import dataclass

# Tag rules live here in one place (thresholds used by match_tags).
TAG_RULES = {
    "remake": "다시하기",
    "low_cs": "CS 부족",
    "carry": "캐리",
    "many_deaths": "많은 데스",
    "good_vision": "시야 우수",
    "cs_per_min_min": 6.0,
    "carry_damage_share_min": 0.28,
    "carry_kill_participation_min": 0.55,
    "many_deaths_min": 8,
    "vision_per_min_min": 2.0,
}

FORM_RECENT_GAMES = 5
FORM_MIN_GAMES = 3
FORM_RISING_WINS = 4
FORM_FALLING_WINS = 1


@dataclass(frozen=True)
class MatchStats:
    match_id: str
    queue_id: int
    game_creation: int
    duration_s: int
    patch: str
    is_remake: bool
    win: bool
    position: str
    champion: str
    kills: int
    deaths: int
    assists: int
    cs: int
    cs_per_min: float
    gold: int
    vision_score: int
    damage_to_champions: int
    damage_share: float
    kill_participation: float
    team_id: int
    teammates: tuple[str, ...]


def extract_match_stats(match: dict, puuid: str) -> MatchStats:
    info = match["info"]
    participants = info["participants"]
    me = next((p for p in participants if p["puuid"] == puuid), None)
    if me is None:
        raise ValueError(f"puuid not found in match: {puuid}")

    duration_s = int(info["gameDuration"])
    team_id = me["teamId"]
    team = [p for p in participants if p["teamId"] == team_id]
    team_damage = sum(p.get("totalDamageDealtToChampions", 0) for p in team)
    team_kills = sum(p.get("kills", 0) for p in team)

    cs = me.get("totalMinionsKilled", 0) + me.get("neutralMinionsKilled", 0)
    cs_per_min = round(cs / (duration_s / 60), 1) if duration_s > 0 else 0.0
    damage = me.get("totalDamageDealtToChampions", 0)
    damage_share = round(damage / team_damage, 3) if team_damage > 0 else 0.0
    kp = round((me["kills"] + me["assists"]) / team_kills, 3) if team_kills > 0 else 0.0

    return MatchStats(
        match_id=match["metadata"]["matchId"],
        queue_id=info["queueId"],
        game_creation=info["gameCreation"],
        duration_s=duration_s,
        patch=".".join(info["gameVersion"].split(".")[:2]),
        is_remake=bool(me.get("gameEndedInEarlySurrender", False)),
        win=bool(me["win"]),
        position=me.get("teamPosition") or "UNKNOWN",
        champion=me["championName"],
        kills=me["kills"],
        deaths=me["deaths"],
        assists=me["assists"],
        cs=cs,
        cs_per_min=cs_per_min,
        gold=me.get("goldEarned", 0),
        vision_score=me.get("visionScore", 0),
        damage_to_champions=damage,
        damage_share=damage_share,
        kill_participation=kp,
        team_id=team_id,
        teammates=tuple(p["puuid"] for p in team if p["puuid"] != puuid),
    )


def kda(kills: int, deaths: int, assists: int) -> float:
    if deaths == 0:
        return round(float(kills + assists), 2)
    return round((kills + assists) / deaths, 2)


def match_tags(s: MatchStats) -> list[str]:
    r = TAG_RULES
    if s.is_remake:
        return [r["remake"]]
    tags: list[str] = []
    if s.position != "UTILITY" and s.cs_per_min < r["cs_per_min_min"]:
        tags.append(r["low_cs"])
    if (
        s.win
        and s.damage_share >= r["carry_damage_share_min"]
        and s.kill_participation >= r["carry_kill_participation_min"]
    ):
        tags.append(r["carry"])
    if s.deaths >= r["many_deaths_min"]:
        tags.append(r["many_deaths"])
    if s.duration_s > 0 and s.vision_score / (s.duration_s / 60) >= r["vision_per_min_min"]:
        tags.append(r["good_vision"])
    return tags


def recent_form(stats: list[MatchStats]) -> dict:
    valid = sorted((s for s in stats if not s.is_remake), key=lambda s: s.game_creation, reverse=True)
    recent = valid[:FORM_RECENT_GAMES]
    n = len(recent)
    wins = sum(1 for s in recent if s.win)
    if n < FORM_MIN_GAMES:
        label = "판단 보류"
    elif wins >= FORM_RISING_WINS:
        label = "상승세"
    elif wins <= FORM_FALLING_WINS:
        label = "하락세"
    else:
        label = "보통"
    return {"games": n, "wins": wins, "label": label}
