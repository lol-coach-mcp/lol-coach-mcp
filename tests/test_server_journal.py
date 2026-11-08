from datetime import datetime, timedelta, timezone

import httpx
from mcp import Client

from conftest import PUUID_ME, load_fixture
from lol_coach_mcp import review
from lol_coach_mcp.riot.errors import RiotNotFound
from lol_coach_mcp.stats import extract_match_stats
from test_server_profile import RIOT_ID, FakeRiot, make_server, store  # noqa: F401

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 11, 8, 12, 0, tzinfo=KST)


class JRiot(FakeRiot):
    def __init__(self, fail_match=False, timeline_404=False, **kw):
        super().__init__(n_matches=0, **kw)
        self.fail_match = fail_match
        self.timeline_404 = timeline_404

    async def get_match(self, match_id):
        if self.fail_match:
            raise RiotNotFound()
        return await super().get_match(match_id)

    async def get_timeline(self, match_id):
        if self.timeline_404:
            raise RiotNotFound()
        return await super().get_timeline(match_id)


def _put(store, i, *, remake=False, timeline=True, queue=420):
    m = load_fixture("match_remake" if remake else "match_normal")
    mid = f"KR_{i}"
    m["metadata"]["matchId"] = mid
    m["info"]["queueId"] = queue
    m["info"]["gameCreation"] = 1_700_000_000_000 + i * 1000
    store.save_match(mid, m, extract_match_stats(m, PUUID_ME))
    if timeline:
        store.save_timeline(mid, load_fixture("timeline_normal"))
    return mid


async def _reg(c):
    await c.call_tool("my_profile", {"action": "set", "riot_id": RIOT_ID})


async def _j(c, **args):
    return await c.call_tool("journal", args)


async def test_listed_and_requires_profile(store):
    async with Client(make_server(store, JRiot())) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
        assert tools["journal"].output_schema
        r = await _j(c, action="list")
    assert r.is_error and "my_profile" in r.content[0].text


async def test_add_links_latest_solo_non_remake(store):
    async with Client(make_server(store, JRiot(), now=lambda: NOW)) as c:
        await _reg(c)
        _put(store, 1)
        _put(store, 2)
        _put(store, 3, remake=True)
        _put(store, 4, queue=440)
        r = await _j(c, action="add", text="갱 많이 당함")
    assert not r.is_error
    row = store.list_journal(5)[0]
    assert row["match_id"] == "KR_2" and row["text"] == "갱 많이 당함"
    assert row["created_at"].startswith("2026-11-08")
    assert r.structured_content["entries"][0]["id"] == row["id"]


async def test_add_without_any_match_saves_null_with_note(store):
    async with Client(make_server(store, JRiot(), now=lambda: NOW)) as c:
        await _reg(c)
        r = await _j(c, action="add", text="메모")
    assert not r.is_error
    assert store.list_journal(5)[0]["match_id"] is None
    assert "연결된 경기 없음" in r.content[0].text


async def test_add_unknown_match_fetches_or_falls_back_to_null(store):
    async with Client(make_server(store, JRiot(), now=lambda: NOW)) as c:
        await _reg(c)
        _put(store, 1)
        r = await _j(c, action="add", text="a", match_id="KR_77")
        assert not r.is_error and store.list_journal(1)[0]["match_id"] == "KR_77"
        assert store.get_match("KR_77") is not None
    async with Client(make_server(store, JRiot(fail_match=True), now=lambda: NOW)) as c:
        r = await _j(c, action="add", text="b", match_id="KR_88")
    assert not r.is_error
    assert store.list_journal(1)[0]["match_id"] is None
    assert "연결된 경기 없음" in r.content[0].text


async def test_add_network_failure_saves_null(store):
    class Down(JRiot):
        async def get_match(self, match_id):
            raise httpx.ConnectTimeout("t")

    async with Client(make_server(store, Down(), now=lambda: NOW)) as c:
        await _reg(c)
        r = await _j(c, action="add", text="c", match_id="KR_9")
    assert not r.is_error and store.list_journal(1)[0]["match_id"] is None


async def test_add_requires_text(store):
    async with Client(make_server(store, JRiot(), now=lambda: NOW)) as c:
        await _reg(c)
        for args in ({}, {"text": "   "}):
            assert (await _j(c, action="add", **args)).is_error
    assert store.list_journal(5) == []


async def test_list_recent_with_limit(store):
    async with Client(make_server(store, JRiot(), now=lambda: NOW)) as c:
        await _reg(c)
        r = await _j(c, action="list")
        assert not r.is_error and r.structured_content["entries"] == []
        for t in ("a", "b", "c"):
            await _j(c, action="add", text=t)
        r = await _j(c, action="list", limit=2)
    assert [e["text"] for e in r.structured_content["entries"]] == ["c", "b"]


async def test_check_jungle_and_farming_evidence(store):
    m, tl = load_fixture("match_normal"), load_fixture("timeline_normal")
    async with Client(make_server(store, JRiot(), now=lambda: NOW)) as c:
        await _reg(c)
        _put(store, 1)
        await _j(c, action="add", text="초반에 갱 당했고 CS도 모자람")
        r = await _j(c, action="check")
    assert not r.is_error
    ev = r.structured_content["evidence"]
    assert ev["match_id"] == "KR_1" and ev["topics"] == ["jungle", "farming"]
    j = ev["bundles"]["jungle"]
    assert j["early_deaths"] == 1 and j["deaths"][0]["killer_champion"] == "Ezreal"
    f = ev["bundles"]["farming"]
    assert f["cs_at_10"] == review.lane_checkpoints(m, tl, PUUID_ME, (10,))["points"][0]["my_cs"]
    text = r.content[0].text
    assert "하세요" not in text and "11:44" in text


async def test_check_other_keywords(store):
    async with Client(make_server(store, JRiot(), now=lambda: NOW)) as c:
        await _reg(c)
        _put(store, 1)
        await _j(c, action="add", text="시야 부족, 라인전 밀림, 한타 졌고 딜 약함")
        r = await _j(c, action="check")
    b = r.structured_content["evidence"]["bundles"]
    assert set(b) == {"vision", "lane", "teamfight", "damage"}
    assert b["vision"]["wards_killed"] == 11 and b["vision"]["wards_placed"] == 5
    assert b["vision"]["vision_score"] == 29
    assert [p["minute"] for p in b["lane"]["points"]] == [5, 10, 15]
    assert b["teamfight"]["late_deaths"] == 3
    assert "damage_share" in b["damage"]


async def test_check_default_bundle_without_keyword(store):
    async with Client(make_server(store, JRiot(), now=lambda: NOW)) as c:
        await _reg(c)
        _put(store, 1)
        await _j(c, action="add", text="그냥 컨디션이 별로")
        r = await _j(c, action="check")
    b = r.structured_content["evidence"]["bundles"]
    assert set(b) == {"default"}
    assert set(b["default"]) >= {
        "kda", "cs_per_min", "vision_per_min", "damage_share", "kill_participation"}


async def test_check_by_entry_id_and_missing(store):
    async with Client(make_server(store, JRiot(), now=lambda: NOW)) as c:
        await _reg(c)
        r = await _j(c, action="check")
        assert not r.is_error and r.structured_content["evidence"] is None
        _put(store, 1)
        _put(store, 2)
        await _j(c, action="add", text="딜", match_id="KR_1")
        await _j(c, action="add", text="시야")
        first = store.list_journal(5)[1]["id"]
        r = await _j(c, action="check", entry_id=first)
        assert r.structured_content["evidence"]["match_id"] == "KR_1"
        assert (await _j(c, action="check", entry_id=999)).is_error


async def test_check_entry_without_match(store):
    async with Client(make_server(store, JRiot(), now=lambda: NOW)) as c:
        await _reg(c)
        await _j(c, action="add", text="갱")
        r = await _j(c, action="check")
    assert not r.is_error and r.structured_content["evidence"] is None
    assert "연결된 경기 없음" in r.content[0].text


async def test_check_without_timeline_degrades(store):
    async with Client(make_server(store, JRiot(timeline_404=True), now=lambda: NOW)) as c:
        await _reg(c)
        _put(store, 1, timeline=False)
        await _j(c, action="add", text="갱, 딜")
        r = await _j(c, action="check")
    assert not r.is_error
    b = r.structured_content["evidence"]["bundles"]
    assert b["jungle"]["early_deaths"] is None and b["damage"]["damage_share"] is not None
    assert "타임라인 데이터 없음" in r.content[0].text


async def test_check_fetches_and_stores_missing_timeline(store):
    async with Client(make_server(store, JRiot(), now=lambda: NOW)) as c:
        await _reg(c)
        _put(store, 1, timeline=False)
        await _j(c, action="add", text="갱")
        r = await _j(c, action="check")
    assert r.structured_content["evidence"]["bundles"]["jungle"]["early_deaths"] == 1
    assert store.get_timeline("KR_1") is not None
