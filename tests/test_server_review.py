import copy

from mcp import Client

from conftest import PUUID_ME, load_fixture
from lol_coach_mcp.riot.errors import RiotNotFound
from lol_coach_mcp.stats import extract_match_stats
from test_server_profile import RIOT_ID, FakeRiot, make_server, store  # noqa: F401

FIELDS = ("summary", "lane", "deaths", "objectives", "vision", "key_moments")
FOCUS_FIELD = {
    "lane": "lane",
    "deaths": "deaths",
    "objectives": "objectives",
    "vision": "vision",
    "key_moments": "key_moments",
}


class ReviewRiot(FakeRiot):
    def __init__(self, timeline_404=False, remake_ids=(), **kw):
        super().__init__(**kw)
        self.timeline_404 = timeline_404
        self.remake_ids = set(remake_ids)
        self.timeline_calls = 0
        self.match_calls = 0

    async def get_match(self, match_id):
        self.match_calls += 1
        if match_id in self.remake_ids:
            m = load_fixture("match_remake")
            m["metadata"]["matchId"] = match_id
            m["info"]["queueId"] = 420
            return m
        return await super().get_match(match_id)

    async def get_timeline(self, match_id):
        self.timeline_calls += 1
        if self.timeline_404:
            raise RiotNotFound()
        return await super().get_timeline(match_id)


async def _register(c):
    await c.call_tool("my_profile", {"action": "set", "riot_id": RIOT_ID})


def _filled(sc):
    return {f for f in FIELDS if sc.get(f) is not None}


async def test_tool_listed_with_schema(store):
    async with Client(make_server(store)) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
    assert tools["review_match"].output_schema


async def test_each_focus_fills_only_its_field(store):
    async with Client(make_server(store, ReviewRiot())) as c:
        await _register(c)
        for focus, field in FOCUS_FIELD.items():
            r = await c.call_tool("review_match", {"match_id": "KR_1", "focus": focus})
            assert not r.is_error, focus
            sc = r.structured_content
            assert sc["status"] == "ok" and sc["focus"] == focus
            assert _filled(sc) == {field}, focus
    assert sc["key_moments"] is not None


async def test_overview_contents(store):
    async with Client(make_server(store, ReviewRiot())) as c:
        await _register(c)
        r = await c.call_tool("review_match", {"match_id": "KR_1"})
    sc = r.structured_content
    assert sc["focus"] == "overview" and sc["status"] == "ok"
    assert _filled(sc) == {"summary", "lane", "key_moments"}
    assert set(sc["summary"]["death_counts_by_phase"]) == {"초반", "중반", "후반"}
    assert sc["summary"]["objective_participation_rate"] is not None
    assert [p["minute"] for p in sc["lane"]["points"]] == [15]
    assert len(sc["key_moments"]) <= 3
    assert sc["result"] in ("승", "패") and sc["duration"].count(":") == 1
    assert sc["queue_id"] == 420


async def test_deaths_match_review_module(store):
    from lol_coach_mcp import review

    m = load_fixture("match_normal")
    tl = load_fixture("timeline_normal")
    expected = review.deaths(m, tl, PUUID_ME)
    async with Client(make_server(store, ReviewRiot())) as c:
        await _register(c)
        r = await c.call_tool("review_match", {"match_id": "KR_1", "focus": "deaths"})
    assert [d["time"] for d in r.structured_content["deaths"]] == [d["time"] for d in expected]


async def test_default_picks_latest_stored_game(store):
    riot = ReviewRiot()
    async with Client(make_server(store, riot)) as c:
        await _register(c)
        for i in (1, 2):
            m = load_fixture("match_normal")
            m["metadata"]["matchId"] = f"KR_{i}"
            m["info"]["gameCreation"] = 1_700_000_000_000 + i * 1000
            store.save_match(f"KR_{i}", m, extract_match_stats(m, PUUID_ME))
        r = await c.call_tool("review_match", {})
    assert r.structured_content["match_id"] == "KR_2"
    assert riot.match_calls == 0


async def test_default_skips_latest_remake(store):
    async with Client(make_server(store, ReviewRiot())) as c:
        await _register(c)
        m = load_fixture("match_normal")
        m["metadata"]["matchId"] = "KR_1"
        m["info"]["gameCreation"] = 1_700_000_000_000
        store.save_match("KR_1", m, extract_match_stats(m, PUUID_ME))
        rm = load_fixture("match_remake")
        rm["metadata"]["matchId"] = "KR_2"
        rm["info"]["queueId"] = 420
        rm["info"]["gameCreation"] = 1_700_000_999_000
        store.save_match("KR_2", rm, extract_match_stats(rm, PUUID_ME))
        r = await c.call_tool("review_match", {})
    assert r.structured_content["match_id"] == "KR_1"


async def test_default_syncs_when_nothing_stored(store):
    riot = ReviewRiot()
    async with Client(make_server(store, riot)) as c:
        await _register(c)
        r = await c.call_tool("review_match", {})
    assert not r.is_error
    assert r.structured_content["match_id"] == "KR_3"
    assert riot.match_calls == 3


async def test_default_errors_when_no_games(store):
    async with Client(make_server(store, ReviewRiot(n_matches=0))) as c:
        await _register(c)
        r = await c.call_tool("review_match", {})
    assert r.is_error
    assert "복기할" in r.content[0].text


async def test_fetches_and_saves_missing_match_and_timeline(store):
    riot = ReviewRiot()
    assert not store.has_match("KR_9") and store.get_timeline("KR_9") is None
    async with Client(make_server(store, riot)) as c:
        await _register(c)
        r = await c.call_tool("review_match", {"match_id": "KR_9", "focus": "deaths"})
        assert not r.is_error
        assert store.has_match("KR_9")
        assert store.get_timeline("KR_9") is not None
        assert (riot.match_calls, riot.timeline_calls) == (1, 1)
        await c.call_tool("review_match", {"match_id": "KR_9", "focus": "lane"})
    assert (riot.match_calls, riot.timeline_calls) == (1, 1)


async def test_stored_timeline_is_not_refetched(store):
    riot = ReviewRiot()
    m = load_fixture("match_normal")
    m["metadata"]["matchId"] = "KR_1"
    store.save_match("KR_1", m, extract_match_stats(m, PUUID_ME))
    store.save_timeline("KR_1", load_fixture("timeline_normal"))
    async with Client(make_server(store, riot)) as c:
        await _register(c)
        r = await c.call_tool("review_match", {"match_id": "KR_1"})
    assert r.structured_content["status"] == "ok"
    assert riot.timeline_calls == 0


async def test_timeline_404_defers_judgement(store):
    riot = ReviewRiot(timeline_404=True)
    async with Client(make_server(store, riot)) as c:
        await _register(c)
        r = await c.call_tool("review_match", {"match_id": "KR_1", "focus": "deaths"})
        ov = await c.call_tool("review_match", {"match_id": "KR_1"})
    assert not r.is_error
    sc = r.structured_content
    assert sc["status"] == "판단 보류"
    assert "타임라인 데이터 없음" in sc["notes"]
    assert _filled(sc) == set()
    assert store.get_timeline("KR_1") is None
    osc = ov.structured_content
    assert osc["status"] == "판단 보류"
    assert _filled(osc) == {"summary"}
    assert osc["summary"]["deaths"] >= 0 and osc["summary"]["death_counts_by_phase"] is None


async def test_remake_is_skipped(store):
    riot = ReviewRiot(remake_ids={"KR_5"})
    async with Client(make_server(store, riot)) as c:
        await _register(c)
        r = await c.call_tool("review_match", {"match_id": "KR_5", "focus": "deaths"})
    assert not r.is_error
    sc = r.structured_content
    assert sc["status"] == "다시하기" and sc["result"] == "다시하기"
    assert _filled(sc) == set()
    assert riot.timeline_calls == 0


async def test_not_registered_guides(store):
    async with Client(make_server(store, ReviewRiot())) as c:
        r = await c.call_tool("review_match", {})
    assert r.is_error
    assert "my_profile" in r.content[0].text


async def test_riot_error_becomes_tool_error(store):
    class Boom(ReviewRiot):
        async def get_match(self, match_id):
            raise RiotNotFound()

    async with Client(make_server(store, Boom())) as c:
        await _register(c)
        r = await c.call_tool("review_match", {"match_id": "KR_1"})
    assert r.is_error


async def test_foreign_match_errors(store):
    class Foreign(ReviewRiot):
        async def get_match(self, match_id):
            m = copy.deepcopy(self._base)
            for p in m["info"]["participants"]:
                p["puuid"] = "someone-else-" + str(p["participantId"])
            return m

    async with Client(make_server(store, Foreign())) as c:
        await _register(c)
        r = await c.call_tool("review_match", {"match_id": "KR_1"})
    assert r.is_error
