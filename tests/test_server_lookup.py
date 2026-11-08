import copy

import pytest
from mcp import Client

from conftest import PUUID_ME, load_fixture
from lol_coach_mcp.stats import extract_match_stats
from test_server_profile import RIOT_ID, FakeRiot, make_server, store  # noqa: F401


class LookupRiot(FakeRiot):
    def __init__(self, league="league_ranked", **kw):
        super().__init__(**kw)
        self.league = load_fixture(league)
        self.get_match_calls = 0

    async def get_league_entries(self, puuid):
        return copy.deepcopy(self.league)

    async def get_summoner(self, puuid):
        return {"summonerLevel": 321}

    async def get_top_mastery(self, puuid, count=5):
        return [{"championId": 22, "championLevel": 7, "championPoints": 123456}][:count]

    async def get_match(self, match_id):
        self.get_match_calls += 1
        return await super().get_match(match_id)


async def _register(c):
    await c.call_tool("my_profile", {"action": "set", "riot_id": RIOT_ID})


async def test_profile_ranked(store):
    async with Client(make_server(store, LookupRiot())) as c:
        await _register(c)
        r = await c.call_tool("get_profile", {})
    assert not r.is_error
    sc = r.structured_content
    solo = next(x for x in sc["ranks"] if x["queue"] == "솔로랭크")
    assert solo["tier"] == "GOLD" and solo["division"] == "II"
    assert solo["win_rate"] == round(539 / (539 + 521) * 100, 1)
    assert sc["unranked"] is False
    assert sc["level"] == 321
    assert sc["top_mastery"][0]["champion_id"] == 22
    assert sc["queue_used"] == 420
    assert store.latest_rank_snapshot() is not None


async def test_profile_unranked(store):
    async with Client(make_server(store, LookupRiot("league_empty"))) as c:
        await _register(c)
        r = await c.call_tool("get_profile", {})
    assert r.structured_content["unranked"] is True
    assert "언랭" in r.content[0].text


async def test_profile_ignores_premade_queue(store):
    async with Client(make_server(store, LookupRiot())) as c:
        await _register(c)
        r = await c.call_tool("get_profile", {})
    assert {x["queue"] for x in r.structured_content["ranks"]} == {"솔로랭크", "자유랭크"}
    assert len(r.structured_content["ranks"]) == 2


async def test_history_marks_remake(store):
    async with Client(make_server(store, LookupRiot())) as c:
        await _register(c)
        for i, name in enumerate(["match_normal", "match_remake"]):
            m = load_fixture(name)
            m["metadata"]["matchId"] = f"KR_{i + 1}"
            m["info"]["queueId"] = 420
            store.save_match(f"KR_{i + 1}", m, extract_match_stats(m, PUUID_ME))
        r = await c.call_tool("get_match_history", {"count": 2})
    assert not r.is_error
    rows = r.structured_content["matches"]
    assert len(rows) == 2
    remake = [x for x in rows if x["result"] == "다시하기"]
    assert len(remake) == 1 and remake[0]["tags"] == ["다시하기"]
    assert r.structured_content["queue_used"] == 420
    assert rows[0]["played_at"].endswith("+09:00")


async def test_history_syncs_when_empty(store):
    riot = LookupRiot()
    async with Client(make_server(store, riot)) as c:
        await _register(c)
        r = await c.call_tool("get_match_history", {"count": 5})
    assert not r.is_error
    assert riot.get_match_calls == 3
    assert len(r.structured_content["matches"]) == 3


async def test_detail_damage_rank(store):
    riot = LookupRiot()
    async with Client(make_server(store, riot)) as c:
        await _register(c)
        r = await c.call_tool("get_match_detail", {"match_id": "KR_2"})
    assert not r.is_error
    sc = r.structured_content
    parts = load_fixture("match_normal")["info"]["participants"]
    dmg = sorted((p["totalDamageDealtToChampions"] for p in parts), reverse=True)
    me = next(p for p in parts if p["puuid"] == PUUID_ME)
    assert sc["my_damage_rank_in_game"] == dmg.index(me["totalDamageDealtToChampions"]) + 1
    team = sorted(
        (p["totalDamageDealtToChampions"] for p in parts if p["teamId"] == me["teamId"]),
        reverse=True,
    )
    assert sc["my_damage_rank_in_team"] == team.index(me["totalDamageDealtToChampions"]) + 1
    assert sum(p["is_me"] for t in sc["teams"] for p in t["players"]) == 1
    assert store.has_match("KR_2")


async def test_lookup_requires_profile(store):
    async with Client(make_server(store, LookupRiot())) as c:
        for name, args in [
            ("get_match_history", {}),
            ("get_profile", {}),
            ("get_match_detail", {"match_id": "KR_1"}),
        ]:
            r = await c.call_tool(name, args)
            assert r.is_error and "my_profile" in r.content[0].text
