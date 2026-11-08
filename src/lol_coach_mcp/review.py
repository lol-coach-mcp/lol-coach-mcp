"""Pure computations for single-match review (no I/O)."""

from __future__ import annotations

PHASE_EARLY_END_MS = 900_000
PHASE_MID_END_MS = 1_500_000
PHASES = ("초반", "중반", "후반")

BASE_LIMIT = 4000
RED_BASE_MIN = 10900
MID_DIAGONAL_WIDTH = 2200
TOP_X_LIMIT = 3600
TOP_Y_LIMIT = 11300
BOT_X_LIMIT = 11300
BOT_Y_LIMIT = 3600
ZONES = (
    "블루 본진",
    "레드 본진",
    "탑",
    "미드",
    "바텀",
    "위쪽 정글",
    "아래쪽 정글",
)

CHECKPOINT_MINUTES = (5, 10, 15)
CONSECUTIVE_DEATH_MS = 120_000
OBJECTIVE_AFTER_DEATH_MS = 60_000
DEAD_BEFORE_OBJECTIVE_MS = 30_000
KEY_MOMENT_LIMIT = 5

TAG_CONSECUTIVE = "연속 데스"
TAG_BEFORE_OBJECTIVE = "오브젝트 직전"
TAG_SOLO = "솔로킬 당함"
REASON_FIRST_DEATH = "첫 데스"
REASON_DEAD_AT_OBJECTIVE = "오브젝트 때 사망 상태"


def _side(team_id: int, other_team_id) -> str:
    """Label a team id relative to mine: 우리 / 상대 / 중립 (e.g. 300 for herald)."""
    if other_team_id == team_id:
        return "우리"
    if other_team_id == (200 if team_id == 100 else 100):
        return "상대"
    return "중립"


def participant_id(timeline: dict, puuid: str) -> int:
    for p in timeline["info"]["participants"]:
        if p["puuid"] == puuid:
            return int(p["participantId"])
    raise ValueError(f"puuid not found in timeline: {puuid}")


def _participants(match: dict) -> list[dict]:
    return match["info"]["participants"]


def _me(match: dict, puuid: str) -> dict:
    for p in _participants(match):
        if p["puuid"] == puuid:
            return p
    raise ValueError(f"puuid not found in match: {puuid}")


def _champion_by_pid(match: dict) -> dict[int, str]:
    return {int(p["participantId"]): p["championName"] for p in _participants(match)}


def lane_opponent_pid(match: dict, puuid: str) -> int | None:
    me = _me(match, puuid)
    pos = me.get("teamPosition") or ""
    if not pos:
        return None
    rivals = [
        p
        for p in _participants(match)
        if p["teamId"] != me["teamId"] and p.get("teamPosition") == pos
    ]
    if len(rivals) != 1:
        return None
    return int(rivals[0]["participantId"])


def mmss(ms: int) -> str:
    total = int(ms) // 1000
    return f"{total // 60:02d}:{total % 60:02d}"


def phase_of(ms: int) -> str:
    if ms < PHASE_EARLY_END_MS:
        return PHASES[0]
    if ms < PHASE_MID_END_MS:
        return PHASES[1]
    return PHASES[2]


def zone_of(x: int, y: int) -> str:
    if x < BASE_LIMIT and y < BASE_LIMIT:
        return "블루 본진"
    if x > RED_BASE_MIN and y > RED_BASE_MIN:
        return "레드 본진"
    if abs(x - y) < MID_DIAGONAL_WIDTH:
        return "미드"
    if x < TOP_X_LIMIT or y > TOP_Y_LIMIT:
        return "탑"
    if x > BOT_X_LIMIT or y < BOT_Y_LIMIT:
        return "바텀"
    if y > x:
        return "위쪽 정글"
    return "아래쪽 정글"


def _events(timeline: dict):
    for frame in timeline["info"]["frames"]:
        yield from frame.get("events", [])


def cs_of(pf: dict) -> int:
    return int(pf.get("minionsKilled", 0)) + int(pf.get("jungleMinionsKilled", 0))


def lane_checkpoints(
    match: dict,
    timeline: dict,
    puuid: str,
    minutes: tuple[int, ...] = CHECKPOINT_MINUTES,
) -> dict:
    pid = participant_id(timeline, puuid)
    opp = lane_opponent_pid(match, puuid)
    if opp is None:
        return {"opponent_champion": None, "status": "비교 불가", "points": []}
    champs = _champion_by_pid(match)
    frames = timeline["info"]["frames"]
    points = []
    for minute in minutes:
        if minute >= len(frames):
            continue
        pfs = frames[minute]["participantFrames"]
        mine, theirs = pfs[str(pid)], pfs[str(opp)]
        my_cs, opp_cs = cs_of(mine), cs_of(theirs)
        points.append(
            {
                "minute": minute,
                "my_cs": my_cs,
                "opp_cs": opp_cs,
                "cs_diff": my_cs - opp_cs,
                "my_gold": int(mine["totalGold"]),
                "opp_gold": int(theirs["totalGold"]),
                "gold_diff": int(mine["totalGold"]) - int(theirs["totalGold"]),
                "my_level": int(mine["level"]),
                "opp_level": int(theirs["level"]),
                "xp_diff": int(mine["xp"]) - int(theirs["xp"]),
            }
        )
    return {
        "opponent_champion": champs.get(opp),
        "status": "ok",
        "points": points,
    }


def _my_team_id(match: dict, puuid: str) -> int:
    return int(_me(match, puuid)["teamId"])


def _my_death_events(timeline: dict, pid: int) -> list[dict]:
    return sorted(
        (e for e in _events(timeline) if e["type"] == "CHAMPION_KILL" and e.get("victimId") == pid),
        key=lambda e: e["timestamp"],
    )


def death_points(timeline: dict, puuid: str) -> list[dict]:
    """내 데스 위치(게임 좌표 0~14870): [{ms, time, x, y}], 시간순."""
    pid = participant_id(timeline, puuid)
    out = []
    for e in _my_death_events(timeline, pid):
        pos = e.get("position") or {}
        out.append({"ms": int(e["timestamp"]), "time": mmss(e["timestamp"]),
                    "x": int(pos.get("x", 0)), "y": int(pos.get("y", 0))})
    return out


def deaths(match: dict, timeline: dict, puuid: str) -> list[dict]:
    pid = participant_id(timeline, puuid)
    team_id = _my_team_id(match, puuid)
    champs = _champion_by_pid(match)
    enemy_epics = sorted(
        e["timestamp"]
        for e in _events(timeline)
        if e["type"] == "ELITE_MONSTER_KILL" and _side(team_id, e.get("killerTeamId")) == "상대"
    )
    kills = _my_death_events(timeline, pid)
    result: list[dict] = []
    prev_ms: int | None = None
    for e in kills:
        ms = int(e["timestamp"])
        killer_id = int(e.get("killerId", 0))
        killer = champs.get(killer_id) if killer_id else None
        assists = len(e.get("assistingParticipantIds") or [])
        solo = assists == 0 and killer is not None
        tags: list[str] = []
        if prev_ms is not None and ms - prev_ms <= CONSECUTIVE_DEATH_MS:
            tags.append(TAG_CONSECUTIVE)
        if any(ms < t <= ms + OBJECTIVE_AFTER_DEATH_MS for t in enemy_epics):
            tags.append(TAG_BEFORE_OBJECTIVE)
        if solo:
            tags.append(TAG_SOLO)
        pos = e.get("position") or {}
        result.append(
            {
                "time": mmss(ms),
                "ms": ms,
                "phase": phase_of(ms),
                "zone": zone_of(int(pos.get("x", 0)), int(pos.get("y", 0))),
                "killer_champion": killer,
                "assists": assists,
                "solo": solo,
                "tags": tags,
            }
        )
        prev_ms = ms
    return result


def objectives(match: dict, timeline: dict, puuid: str) -> dict:
    pid = participant_id(timeline, puuid)
    team_id = _my_team_id(match, puuid)
    death_times = [
        int(e["timestamp"])
        for e in _events(timeline)
        if e["type"] == "CHAMPION_KILL" and e.get("victimId") == pid
    ]

    def involved(e: dict) -> bool:
        return e.get("killerId") == pid or pid in (e.get("assistingParticipantIds") or [])

    out = []
    epic_count = 0
    participation = 0
    for e in _events(timeline):
        etype = e["type"]
        if etype == "ELITE_MONSTER_KILL":
            kind, sub = e["monsterType"], e.get("monsterSubType")
            side = _side(team_id, e.get("killerTeamId"))
        elif etype == "BUILDING_KILL":
            building = e.get("buildingType")
            if building == "INHIBITOR_BUILDING":
                kind, sub = "INHIBITOR", None
            elif building == "TOWER_BUILDING":
                kind, sub = "TOWER", e.get("towerType")
            else:
                continue
            side = _side(team_id, e.get("teamId"))  # owner of destroyed building
            side = {"우리": "상대", "상대": "우리"}.get(side, side)
        else:
            continue
        ms = int(e["timestamp"])
        took_part = involved(e)
        if etype == "ELITE_MONSTER_KILL" and side == "우리":
            epic_count += 1
            if took_part:
                participation += 1
        out.append(
            {
                "time": mmss(ms),
                "type": kind,
                "sub_type": sub,
                "team": side,
                "participated": took_part,
                "dead_at_time": any(ms - DEAD_BEFORE_OBJECTIVE_MS <= d <= ms for d in death_times),
                "_ms": ms,
                "_epic": etype == "ELITE_MONSTER_KILL",
            }
        )
    rate = participation / epic_count if epic_count else 0.0
    return {
        "events": [{k: v for k, v in o.items() if not k.startswith("_")} for o in out],
        "my_team_epic_count": epic_count,
        "my_participation": participation,
        "participation_rate": rate,
    }


def vision(match: dict, timeline: dict, puuid: str) -> dict:
    pid = participant_id(timeline, puuid)
    me = _me(match, puuid)
    duration_min = max(int(match["info"]["gameDuration"]) / 60, 1e-9)
    placed = {p: 0 for p in PHASES}
    control = 0
    killed = 0
    for e in _events(timeline):
        if e["type"] == "WARD_PLACED" and e.get("creatorId") == pid:
            placed[phase_of(int(e["timestamp"]))] += 1
            if e.get("wardType") == "CONTROL_WARD":
                control += 1
        elif e["type"] == "WARD_KILL" and e.get("killerId") == pid:
            killed += 1
    score = int(me.get("visionScore", 0))
    return {
        "vision_score": score,
        "per_min": round(score / duration_min, 2),
        "wards_placed": placed,
        "control_wards": control,
        "wards_killed": killed,
    }


def key_moments(
    match: dict, timeline: dict, puuid: str, limit: int = KEY_MOMENT_LIMIT
) -> list[dict]:
    reasons: dict[int, list[str]] = {}

    def add(ms: int, reason: str) -> None:
        bucket = reasons.setdefault(ms, [])
        if reason not in bucket:
            bucket.append(reason)

    for i, d in enumerate(deaths(match, timeline, puuid)):
        if i == 0:
            add(d["ms"], REASON_FIRST_DEATH)
        if TAG_BEFORE_OBJECTIVE in d["tags"]:
            add(d["ms"], TAG_BEFORE_OBJECTIVE)
        if TAG_CONSECUTIVE in d["tags"]:
            add(d["ms"], TAG_CONSECUTIVE)

    pid = participant_id(timeline, puuid)
    team_id = _my_team_id(match, puuid)
    death_times = [
        int(e["timestamp"])
        for e in _events(timeline)
        if e["type"] == "CHAMPION_KILL" and e.get("victimId") == pid
    ]
    for e in _events(timeline):
        if e["type"] == "ELITE_MONSTER_KILL" and _side(team_id, e.get("killerTeamId")) == "상대":
            ms = int(e["timestamp"])
            if any(ms - DEAD_BEFORE_OBJECTIVE_MS <= d <= ms for d in death_times):
                add(ms, REASON_DEAD_AT_OBJECTIVE)

    moments = [
        {"time": mmss(ms), "ms": ms, "reason": ", ".join(rs)}
        for ms, rs in sorted(reasons.items())
    ]
    return moments[:limit]
