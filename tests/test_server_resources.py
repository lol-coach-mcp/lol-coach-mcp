import json

from mcp import Client

from test_server_form import EmptyRiot, _mixed, _setup
from test_server_profile import make_server, store  # noqa: F401


def _bl(tier, position, metric, patch="16.22", collected="2026-11-01", source="opgg", n=500):
    return {"tier": tier, "position": position, "metric": metric, "mean": 1.0, "std": 1.0,
            "n": n, "patch": patch, "collected_at": collected, "source": source}


async def _read(c, uri):
    r = await c.read_resource(uri)
    assert len(r.contents) == 1
    return json.loads(r.contents[0].text)


async def test_lists_resources_and_prompts(store):
    async with Client(make_server(store, EmptyRiot())) as c:
        uris = {str(r.uri) for r in (await c.list_resources()).resources}
        names = {p.name for p in (await c.list_prompts()).prompts}
    assert uris == {"coach://context", "coach://baselines"}
    assert names == {"match_review", "weekly_review", "season_review", "session_wrap"}


async def test_context_resource_without_profile(store):
    async with Client(make_server(store, EmptyRiot())) as c:
        data = await _read(c, "coach://context")
    assert data["registered"] is False and "my_profile" in data["guidance"]


async def test_context_resource_matches_tool(store):
    async with Client(make_server(store, EmptyRiot())) as c:
        await _setup(c, store, _mixed(5))
        data = await _read(c, "coach://context")
        tool = (await c.call_tool("get_coach_context", {})).structured_content
    assert data["registered"] is True
    assert {k: v for k, v in data.items() if k != "registered"} == tool


async def test_baselines_resource_empty(store):
    async with Client(make_server(store, EmptyRiot())) as c:
        assert await _read(c, "coach://baselines") == {"status": "기준선 없음"}


async def test_baselines_resource_summary(store):
    store.upsert_baselines([
        _bl("GOLD", "BOTTOM", "deaths"), _bl("GOLD", "BOTTOM", "kda"),
        _bl("PLATINUM", "BOTTOM", "deaths", patch="16.21", collected="2026-10-20"),
        _bl("GOLD", "MIDDLE", "deaths", source="ps"),
    ])
    async with Client(make_server(store, EmptyRiot())) as c:
        data = await _read(c, "coach://baselines")
    by = {(g["tier"], g["position"]): g for g in data["groups"]}
    assert by[("GOLD", "BOTTOM")]["metrics"] == 2
    assert by[("PLATINUM", "BOTTOM")]["metrics"] == 1
    assert data["total_rows"] == 4
    assert data["sources"] == ["opgg", "ps"]
    assert sorted(data["patches"]) == ["16.21", "16.22"]
    assert data["collected_at"] == {"min": "2026-10-20", "max": "2026-11-01"}


async def test_prompts_name_tools_in_order(store):
    async with Client(make_server(store, EmptyRiot())) as c:
        async def text(name, args=None):
            r = await c.get_prompt(name, args)
            assert r.messages and r.messages[0].role == "user"
            return r.messages[0].content.text

        specs = {
            "match_review": ["review_match", "key_moments", "goals"],
            "weekly_review": ["analyze_form", "week", "compare_splits", "win_loss", "goals"],
            "season_review": ["analyze_form", "season", "champion_report", "find_habits"],
            "session_wrap": ["analyze_form", "session", "review_match"],
        }
        for name, order in specs.items():
            t = await text(name)
            assert "get_coach_context" in t and "말투" in t and "상세" in t
            pos = [t.index(k) for k in order]
            assert pos == sorted(pos), name
        assert "KR_0000000001" in await text("match_review", {"match_id": "KR_0000000001"})
        assert "KR_" not in await text("match_review")
