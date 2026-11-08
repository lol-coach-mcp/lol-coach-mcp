import json
from pathlib import Path

import pytest

from mcp import Client
from mcp.server.mcpserver.exceptions import ResourceError

from lol_coach_mcp.server import AppState, create_server
from lol_coach_mcp.config import Settings

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


FRAGMENTS = {
    "match_review": ["review_match(focus=overview)", "review_match(focus=key_moments)",
                     "goals(action=check)"],
    "weekly_review": ["analyze_form(period=week)", "compare_splits(split_by=win_loss, period=week)"],
    "season_review": ["analyze_form(period=season)", "champion_report(mode=overview)",
                      "find_habits(period=season)"],
    "session_wrap": ["analyze_form(period=session)", "review_match"],
}


async def test_match_review_with_id_and_invalid_id(store):
    async with Client(make_server(store, EmptyRiot())) as c:
        t = (await c.get_prompt("match_review", {"match_id": "KR_0000000001"})).messages[0].content.text
        assert 'review_match(match_id="KR_0000000001", focus=overview)' in t
        assert 'review_match(match_id="KR_0000000001", focus=key_moments)' in t
        bad = (await c.get_prompt("match_review", {"match_id": 'x"); drop'})).messages[0].content.text
        assert "무시함" in bad and "match_id=" not in bad.replace("match_id 생략", "")


async def test_resource_outside_lifespan_gives_korean_error(store):
    server = make_server(store, EmptyRiot())
    with pytest.raises(ResourceError, match="서버가 아직 준비되지 않음"):
        await server.read_resource("coach://baselines")


async def test_overlapping_lifespans_do_not_clobber(store):
    server = create_server(lambda: AppState(
        settings=Settings(riot_api_key="k", data_dir=Path("."), user_agent="t"),
        riot=EmptyRiot(), store=store))
    async with Client(server):
        async with Client(server) as inner:
            assert await _read(inner, "coach://baselines") == {"status": "기준선 없음"}
        # 안쪽 lifespan이 끝나도 바깥 lifespan 상태가 유지되어야 함
        r = await server.read_resource("coach://baselines")
        assert "기준선 없음" in list(r)[0].content
    with pytest.raises(ResourceError):
        await server.read_resource("coach://baselines")


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
            for frag in FRAGMENTS[name]:
                assert frag in t, (name, frag)
            pos = [t.index(k) for k in order]
            assert pos == sorted(pos), name
        assert "KR_0000000001" in await text("match_review", {"match_id": "KR_0000000001"})
        assert "KR_" not in await text("match_review")
