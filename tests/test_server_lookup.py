import copy

from datetime import datetime, timedelta, timezone

import httpx

import pytest
from mcp import Client

from conftest import PUUID_ME, load_fixture, make_synthetic_match, make_synthetic_timeline
from lol_coach_mcp.riot.errors import RiotServerError
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


async def test_snapshot_excludes_premade(store):
    async with Client(make_server(store, LookupRiot())) as c:
        await _register(c)
        await c.call_tool("get_profile", {})
    _, entries = store.latest_rank_snapshot()
    assert {e["queueType"] for e in entries} == {"RANKED_SOLO_5x5", "RANKED_FLEX_SR"}


async def test_profile_level_missing_omits_level(store):
    class NoLevel(LookupRiot):
        async def get_summoner(self, puuid):
            return {}

    async with Client(make_server(store, NoLevel())) as c:
        await _register(c)
        r = await c.call_tool("get_profile", {})
    assert not r.is_error
    assert "None" not in r.content[0].text


async def test_profile_riot_error_and_network_error(store):
    from lol_coach_mcp.riot.errors import RiotAuthError

    class Auth(LookupRiot):
        async def get_league_entries(self, puuid):
            raise RiotAuthError()

    class Net(LookupRiot):
        async def get_summoner(self, puuid):
            raise httpx.ConnectTimeout("t")

    async with Client(make_server(store, Auth())) as c:
        await _register(c)
        r = await c.call_tool("get_profile", {})
    assert r.is_error and "API 키" in r.content[0].text
    async with Client(make_server(store, Net())) as c:
        r = await c.call_tool("get_profile", {})
    assert r.is_error and "네트워크" in r.content[0].text


async def test_detail_not_participant(store):
    class Other(LookupRiot):
        async def get_match(self, match_id):
            m = await super().get_match(match_id)
            for p in m["info"]["participants"]:
                if p["puuid"] == PUUID_ME:
                    p["puuid"] = "puuid-zzz"
            return m

    async with Client(make_server(store, Other())) as c:
        await _register(c)
        r = await c.call_tool("get_match_detail", {"match_id": "KR_1"})
    assert r.is_error and "참가하지 않았음" in r.content[0].text
    assert not store.has_match("KR_1")


async def test_detail_riot_ids_never_use_summoner_name(store):
    class NoRiotId(LookupRiot):
        async def get_match(self, match_id):
            m = await super().get_match(match_id)
            for p in m["info"]["participants"]:
                if p["puuid"] != PUUID_ME:
                    p.pop("riotIdGameName", None)
                    p["summonerName"] = "옛이름"
            return m

    async with Client(make_server(store, NoRiotId())) as c:
        await _register(c)
        r = await c.call_tool("get_match_detail", {"match_id": "KR_1"})
    ids = [p["riot_id"] for t in r.structured_content["teams"] for p in t["players"]]
    assert "옛이름" not in ids and "알 수 없음" in ids


async def test_history_count_clamp(store):
    class Light(LookupRiot):
        async def get_match(self, match_id):
            return make_synthetic_match(match_id)

        async def get_timeline(self, match_id):
            return make_synthetic_timeline()

    riot = Light(n_matches=60)
    async with Client(make_server(store, riot)) as c:
        await _register(c)
        r100 = await c.call_tool("get_match_history", {"count": 100})
        r0 = await c.call_tool("get_match_history", {"count": 0})
    assert len(r0.structured_content["matches"]) == 1
    assert len(r100.structured_content["matches"]) == 50


async def test_history_queue_none_syncs_without_queue_filter_and_skips_when_enough(store):
    riot = LookupRiot()
    seen = []
    orig = riot.get_match_ids

    async def spy(puuid, **kw):
        seen.append(kw.get("queue", "missing"))
        return await orig(puuid, **kw)

    riot.get_match_ids = spy
    async with Client(make_server(store, riot)) as c:
        await _register(c)
        r = await c.call_tool("get_match_history", {"count": 3, "queue": None})
        assert r.structured_content["queue_used"] is None
        assert seen == [None]
        calls = riot.get_match_calls
        await c.call_tool("get_match_history", {"count": 3, "queue": None})
    assert seen == [None, None] and riot.get_match_calls == calls


async def test_damage_rank_tie_by_participant_order(store):
    class Tie(LookupRiot):
        async def get_match(self, match_id):
            m = await super().get_match(match_id)
            for p in m["info"]["participants"]:
                p["totalDamageDealtToChampions"] = 1000
            return m

    async with Client(make_server(store, Tie())) as c:
        await _register(c)
        r = await c.call_tool("get_match_detail", {"match_id": "KR_1"})
    parts = load_fixture("match_normal")["info"]["participants"]
    order = [p["puuid"] for p in parts]
    team = [p["puuid"] for p in parts if p["teamId"] == 200]
    sc = r.structured_content
    assert sc["my_damage_rank_in_game"] == order.index(PUUID_ME) + 1
    assert sc["my_damage_rank_in_team"] == team.index(PUUID_ME) + 1


async def test_history_picks_up_new_head_ids_and_reports_sync_time(store):
    riot = LookupRiot(n_matches=3)
    async with Client(make_server(store, riot)) as c:
        await _register(c)
        r1 = await c.call_tool("get_match_history", {"count": 3})
        riot.ids = ["KR_4"] + riot.ids
        r2 = await c.call_tool("get_match_history", {"count": 3})
    assert r1.structured_content["last_sync_at"]
    assert r2.structured_content["last_sync_at"]
    assert [m["match_id"] for m in r2.structured_content["matches"]][0] == "KR_4"
    assert "동기화" in r2.content[0].text


class _Down(LookupRiot):
    def __init__(self, exc, **kw):
        super().__init__(**kw)
        self.exc = exc
        self.down = False

    async def get_match_ids(self, puuid, **kw):
        if self.down:
            raise self.exc
        return await super().get_match_ids(puuid, **kw)


@pytest.mark.parametrize(
    "exc",
    [RiotServerError(), httpx.ConnectTimeout("timeout")],
    ids=["riot", "network"],
)
async def test_history_falls_back_to_stored_data_when_sync_fails(store, exc):
    riot = _Down(exc)
    async with Client(make_server(store, riot)) as c:
        await _register(c)
        await c.call_tool("get_match_history", {"count": 3})
        riot.down = True
        r = await c.call_tool("get_match_history", {"count": 3})
    assert not r.is_error
    assert len(r.structured_content["matches"]) == 3
    assert "동기화 실패, 저장된 데이터 기준" in r.structured_content["notes"]
    assert "동기화 실패, 저장된 데이터 기준" in r.content[0].text


async def test_history_sync_failure_without_stored_data_is_error(store):
    riot = _Down(httpx.ConnectTimeout("timeout"))
    riot.down = True
    async with Client(make_server(store, riot)) as c:
        await _register(c)
        r = await c.call_tool("get_match_history", {"count": 3})
    assert r.is_error


async def test_user_facing_times_are_kst_and_queue_labeled(store):
    async with Client(make_server(store, LookupRiot())) as c:
        await _register(c)
        h = await c.call_tool("get_match_history", {"count": 3})
        ctx = await c.call_tool("get_coach_context", {})
    iso = h.structured_content["last_sync_at"]
    assert "T" in iso and "KST" not in iso
    kst = datetime.fromisoformat(iso).astimezone(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M KST")
    assert kst in h.content[0].text and kst in ctx.content[0].text
    assert iso not in h.content[0].text and iso not in ctx.content[0].text
    assert "솔로랭크" in h.content[0].text
    assert all(" KST " in ln for ln in h.content[0].text.splitlines()[2:])


def test_queue_label():
    from lol_coach_mcp.server import queue_label

    assert queue_label(420) == "솔로랭크"
    assert queue_label(440) == "자유랭크"
    assert queue_label(450) == "큐 450"


async def test_detail_text_time_is_kst(store):
    import re

    async with Client(make_server(store, LookupRiot())) as c:
        await _register(c)
        r = await c.call_tool("get_match_detail", {"match_id": "KR_2"})
    assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2} KST", r.content[0].text)
