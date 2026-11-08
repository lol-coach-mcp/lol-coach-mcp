from mcp import Client

from conftest import PUUID_ME, load_fixture
from lol_coach_mcp.riot.errors import RiotNotFound, RiotServerError
from lol_coach_mcp.stats import extract_match_stats
from test_server_profile import RIOT_ID, FakeRiot, make_server, store  # noqa: F401


class TlRiot(FakeRiot):
    def __init__(self, timeline_404=False, tl_error=None, **kw):
        super().__init__(**kw)
        self.timeline_404 = timeline_404
        self.tl_error = tl_error
        self.timeline_calls = 0

    async def get_timeline(self, match_id):
        self.timeline_calls += 1
        if self.tl_error:
            raise self.tl_error
        if self.timeline_404 == "all" or (self.timeline_404 and match_id == "KR_1"):
            raise RiotNotFound()
        return await super().get_timeline(match_id)


def _seed(store, n, with_timeline=()):
    for i in range(1, n + 1):
        m = load_fixture("match_normal")
        m["metadata"]["matchId"] = f"KR_{i}"
        m["info"]["gameCreation"] = 1_700_000_000_000 + i * 3_600_000
        store.save_match(f"KR_{i}", m, extract_match_stats(m, PUUID_ME))
        if i in with_timeline:
            store.save_timeline(f"KR_{i}", load_fixture("timeline_normal"))


async def _run(store, riot, args=None, n=6, with_timeline=()):
    _seed(store, n, with_timeline)
    async with Client(make_server(store, riot)) as c:
        await c.call_tool("my_profile", {"action": "set", "riot_id": RIOT_ID})
        return await c.call_tool("find_habits", args or {})


async def test_tool_listed_with_schema(store):
    async with Client(make_server(store, TlRiot())) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
    assert tools["find_habits"].output_schema


async def test_requires_profile(store):
    async with Client(make_server(store, TlRiot())) as c:
        r = await c.call_tool("find_habits", {})
    assert r.is_error and "my_profile" in r.content[0].text


async def test_ok_with_counts_and_korean_text(store):
    riot = TlRiot(n_matches=0)
    r = await _run(store, riot, n=6)
    sc = r.structured_content
    assert not r.is_error
    assert sc["status"] == "ok" and sc["games_total"] == 6 and sc["games_with_timeline"] == 6
    assert sc["deaths_total"] > 0 and len(sc["top_death_zones"]) <= 3
    assert sum(b["deaths"] for b in sc["death_time_buckets"]) == sc["deaths_total"]
    assert sc["win_compare"]["status"] == "판단 보류"  # 전부 패배
    text = r.content[0].text
    assert "6판" in text and "솔로랭크" in text and "하세요" not in text
    assert riot.timeline_calls == 6
    assert store.get_timeline("KR_3") is not None


async def test_stored_timelines_not_refetched(store):
    riot = TlRiot(n_matches=0)
    r = await _run(store, riot, n=5, with_timeline=range(1, 6))
    assert r.structured_content["status"] == "ok" and riot.timeline_calls == 0


async def test_backfill_limited_to_ten_per_call(store):
    riot = TlRiot(n_matches=0)
    r = await _run(store, riot, n=14)
    sc = r.structured_content
    assert riot.timeline_calls == 10
    assert sc["games_total"] == 14 and sc["games_with_timeline"] == 10
    assert any("4판 남음" in n for n in sc["notes"])


async def test_all_timelines_404_defers_judgement(store):
    riot = TlRiot(n_matches=0, timeline_404="all")
    r = await _run(store, riot, n=6)
    sc = r.structured_content
    assert not r.is_error
    assert sc["status"] == "판단 보류(타임라인 없음)"
    assert sc["games_with_timeline"] == 0 and sc["games_total"] == 6
    assert sc["top_death_zones"] == [] and sc["first_back_avg"] is None


async def test_404_skipped_others_used(store):
    riot = TlRiot(n_matches=0, timeline_404=True)  # KR_1 only
    r = await _run(store, riot, n=6)
    sc = r.structured_content
    assert sc["games_with_timeline"] == 5 and sc["status"] == "ok"
    assert any("1판 제외" in n for n in sc["notes"])


async def test_few_timelines_is_insufficient_sample(store):
    r = await _run(store, TlRiot(n_matches=0), n=4)
    sc = r.structured_content
    assert sc["status"] == "판단 보류(표본 부족)" and sc["games_with_timeline"] == 4


async def test_riot_error_becomes_tool_error(store):
    r = await _run(store, TlRiot(n_matches=0, tl_error=RiotServerError(500)), n=5)
    assert r.is_error
