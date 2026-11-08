import copy

import pytest

from lol_coach_mcp import review
from conftest import PUUID_ME, load_fixture


@pytest.fixture
def match():
    return load_fixture("match_normal")


@pytest.fixture
def timeline():
    return load_fixture("timeline_normal")


def _events(tl):
    for fr in tl["info"]["frames"]:
        yield from fr["events"]


def test_participant_id(timeline):
    pid = review.participant_id(timeline, PUUID_ME)
    entry = next(p for p in timeline["info"]["participants"] if p["puuid"] == PUUID_ME)
    assert pid == entry["participantId"]
    with pytest.raises(ValueError):
        review.participant_id(timeline, "nobody")


def test_lane_opponent(match):
    opp = review.lane_opponent_pid(match, PUUID_ME)
    me = next(p for p in match["info"]["participants"] if p["puuid"] == PUUID_ME)
    rival = next(p for p in match["info"]["participants"] if p["participantId"] == opp)
    assert rival["teamId"] != me["teamId"]
    assert rival["teamPosition"] == me["teamPosition"]


def test_mmss_and_phase():
    assert review.mmss(860000) == "14:20"
    assert review.phase_of(899999) == "초반"
    assert review.phase_of(900000) == "중반"
    assert review.phase_of(1499999) == "중반"
    assert review.phase_of(1500000) == "후반"


@pytest.mark.parametrize(
    "xy,zone",
    [
        ((1000, 1000), "블루 본진"),
        ((13000, 13000), "레드 본진"),
        ((7500, 7500), "미드"),
        ((1500, 8000), "탑"),
        ((8000, 13500), "탑"),
        ((13500, 1500), "바텀"),
        ((8000, 1500), "바텀"),
        ((4000, 10000), "위쪽 정글"),
        ((10000, 4500), "아래쪽 정글"),
    ],
)
def test_zone_of(xy, zone):
    assert review.zone_of(*xy) == zone


def test_deaths_on_fixture(match, timeline):
    me = next(p for p in match["info"]["participants"] if p["puuid"] == PUUID_ME)
    result = review.deaths(match, timeline, PUUID_ME)
    assert len(result) == me["deaths"]
    for d in result:
        assert d["zone"] in review.ZONES
        assert d["phase"] in review.PHASES
        assert d["time"] == review.mmss(d["ms"])


def test_deaths_empty_when_no_deaths(match, timeline):
    tl = copy.deepcopy(timeline)
    pid = review.participant_id(tl, PUUID_ME)
    for fr in tl["info"]["frames"]:
        fr["events"] = [
            e
            for e in fr["events"]
            if not (e["type"] == "CHAMPION_KILL" and e.get("victimId") == pid)
        ]
    assert review.deaths(match, tl, PUUID_ME) == []
    assert review.key_moments(match, tl, PUUID_ME) == []


def test_lane_checkpoints(match, timeline):
    pid = review.participant_id(timeline, PUUID_ME)
    res = review.lane_checkpoints(match, timeline, PUUID_ME)
    assert res["status"] == "ok"
    assert res["opponent_champion"]
    p10 = next(p for p in res["points"] if p["minute"] == 10)
    pf = timeline["info"]["frames"][10]["participantFrames"][str(pid)]
    assert p10["my_gold"] == pf["totalGold"]
    assert p10["gold_diff"] == p10["my_gold"] - p10["opp_gold"]
    assert p10["cs_diff"] == p10["my_cs"] - p10["opp_cs"]


def test_lane_checkpoints_short_game(match, timeline):
    tl = copy.deepcopy(timeline)
    tl["info"]["frames"] = tl["info"]["frames"][:12]
    minutes = [p["minute"] for p in review.lane_checkpoints(match, tl, PUUID_ME)["points"]]
    assert minutes == [5, 10]


def test_lane_checkpoints_no_opponent(match, timeline):
    m = copy.deepcopy(match)
    me = next(p for p in m["info"]["participants"] if p["puuid"] == PUUID_ME)
    for p in m["info"]["participants"]:
        if p["teamId"] != me["teamId"] and p["teamPosition"] == me["teamPosition"]:
            p["teamPosition"] = ""
    res = review.lane_checkpoints(m, timeline, PUUID_ME)
    assert res["status"] == "비교 불가"
    assert res["points"] == []
    assert res["opponent_champion"] is None


def test_objectives(match, timeline):
    res = review.objectives(match, timeline, PUUID_ME)
    assert res["my_team_epic_count"] >= 0
    if res["my_team_epic_count"]:
        assert res["participation_rate"] == res["my_participation"] / res["my_team_epic_count"]
    else:
        assert res["participation_rate"] == 0.0
    for ev in res["events"]:
        assert ev["team"] in ("우리", "상대")


def test_vision(match, timeline):
    pid = review.participant_id(timeline, PUUID_ME)
    res = review.vision(match, timeline, PUUID_ME)
    mine = sum(1 for e in _events(timeline) if e["type"] == "WARD_PLACED" and e["creatorId"] == pid)
    assert sum(res["wards_placed"].values()) == mine
    assert set(res["wards_placed"]) == set(review.PHASES)


def test_key_moments_on_fixture(match, timeline):
    res = review.key_moments(match, timeline, PUUID_ME, limit=3)
    assert len(res) <= 3
    assert [m["ms"] for m in res] == sorted(m["ms"] for m in res)
    assert res[0]["reason"].startswith("첫 데스") or "첫 데스" in res[0]["reason"]


def _synthetic():
    match = {
        "info": {
            "gameDuration": 1200,
            "participants": [
                {"participantId": 1, "puuid": "me", "teamId": 100, "teamPosition": "TOP",
                 "championName": "Ahri", "visionScore": 12},
                {"participantId": 2, "puuid": "foe", "teamId": 200, "teamPosition": "TOP",
                 "championName": "Darius", "visionScore": 5},
            ],
        }
    }

    def kill(ts, killer, assists=None):
        e = {"type": "CHAMPION_KILL", "timestamp": ts, "victimId": 1, "killerId": killer,
             "position": {"x": 1000, "y": 8000}}
        if assists:
            e["assistingParticipantIds"] = assists
        return e

    events = [
        kill(100_000, 2),
        kill(180_000, 2, [2]),  # within 120s of previous
        {"type": "ELITE_MONSTER_KILL", "timestamp": 200_000, "killerId": 2, "killerTeamId": 200,
         "monsterType": "DRAGON", "monsterSubType": "FIRE_DRAGON"},
        kill(500_000, 0),  # killed by minion/turret
    ]
    timeline = {
        "info": {
            "participants": [{"participantId": 1, "puuid": "me"}, {"participantId": 2, "puuid": "foe"}],
            "frames": [{"events": events}],
        }
    }
    return match, timeline


def test_synthetic_tags():
    match, tl = _synthetic()
    d = review.deaths(match, tl, "me")
    assert len(d) == 3
    assert d[0]["tags"] == ["솔로킬 당함"]
    assert d[0]["solo"] is True and d[0]["killer_champion"] == "Darius"
    assert d[1]["tags"] == ["연속 데스", "오브젝트 직전"]
    assert d[1]["assists"] == 1 and d[1]["solo"] is False
    assert d[2]["killer_champion"] is None and d[2]["tags"] == []
    assert d[2]["solo"] is False


def test_synthetic_key_moments_merge():
    match, tl = _synthetic()
    km = review.key_moments(match, tl, "me")
    assert [m["ms"] for m in km] == [100_000, 180_000, 200_000]
    assert km[0]["reason"] == "첫 데스"
    assert km[1]["reason"] == "오브젝트 직전, 연속 데스"
    assert km[2]["reason"] == "오브젝트 때 사망 상태"
    assert len(review.key_moments(match, tl, "me", limit=2)) == 2
