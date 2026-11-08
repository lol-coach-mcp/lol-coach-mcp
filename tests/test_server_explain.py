from mcp import Client

from lol_coach_mcp import analysis, explain
from test_server_form import EmptyRiot, _mixed, _setup
from test_server_profile import RIOT_ID, make_server, store  # noqa: F401


def _bl(tier, metric, mean, position="BOTTOM", n=500):
    return {"tier": tier, "position": position, "metric": metric, "mean": mean, "std": 1.0,
            "n": n, "patch": "16.22", "collected_at": "2026-11-01", "source": "opgg"}


async def _x(c, topic):
    return await c.call_tool("explain", {"topic": topic})


def test_next_tier_order():
    assert explain.next_tier("GOLD") == "PLATINUM"
    assert explain.next_tier("gold") == "PLATINUM"
    assert explain.next_tier("GRANDMASTER") == "CHALLENGER"
    assert explain.next_tier("CHALLENGER") is None
    assert explain.next_tier("UNKNOWN") is None
    assert explain.TIER_ORDER[0] == "IRON" and explain.TIER_ORDER[-1] == "CHALLENGER"


def test_resolve_topic_by_key_or_label():
    assert explain.resolve_topic("deaths") == "deaths"
    assert explain.resolve_topic("분당 CS") == "cs_per_min"
    assert explain.resolve_topic("DEATHS") == "deaths"
    assert explain.resolve_topic("tier_baselines") == "tier_baselines"
    assert explain.resolve_topic("nope") is None


def test_every_metric_has_definition_and_formula():
    for m in analysis.METRICS:
        assert explain.DEFINITIONS[m][0] and explain.DEFINITIONS[m][1]


async def test_listed_and_requires_profile(store):
    async with Client(make_server(store, EmptyRiot())) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
        assert tools["explain"].output_schema
        r = await _x(c, "deaths")
    assert r.is_error and "my_profile" in r.content[0].text


async def test_metric_by_key_and_label(store):
    stats = _mixed(20)
    async with Client(make_server(store, EmptyRiot())) as c:
        await _setup(c, store, stats)
        r = await _x(c, "deaths")
        r2 = await _x(c, "분당 CS")
    sc = r.structured_content
    assert not r.is_error and sc["metric"] == "deaths" and sc["higher_is_better"] is False
    assert sc["definition"] and sc["formula"] and sc["games"] == 20
    assert abs(sc["my_value"] - sum(s.deaths for s in stats) / 20) < 0.01
    assert sc["position"] == "BOTTOM"
    assert r2.structured_content["metric"] == "cs_per_min"
    assert "하세요" not in r.content[0].text


async def test_unknown_topic_lists_valid_topics(store):
    async with Client(make_server(store, EmptyRiot())) as c:
        await _setup(c, store, _mixed(3))
        r = await _x(c, "nope")
    assert r.is_error
    t = r.content[0].text
    assert "tier_baselines" in t and "next_tier" in t and "cs_per_min" in t and "분당 CS" in t


async def test_metric_without_games(store):
    async with Client(make_server(store, EmptyRiot())) as c:
        await c.call_tool("my_profile", {"action": "set", "riot_id": RIOT_ID})
        r = await _x(c, "kda")
    assert not r.is_error and r.structured_content["my_value"] is None


async def test_tier_baselines_missing_and_present(store):
    async with Client(make_server(store, EmptyRiot())) as c:
        await _setup(c, store, _mixed(20))
        r = await _x(c, "tier_baselines")
        sc = r.structured_content
        assert not r.is_error and sc["tier"] == "GOLD" and sc["position"] == "BOTTOM"
        assert {row["status"] for row in sc["rows"]} == {"기준선 없음"}
        assert "기준선 없음" in r.content[0].text
        store.upsert_baselines([_bl("GOLD", "deaths", 5.0)])
        r = await _x(c, "tier_baselines")
    by = {row["metric"]: row for row in r.structured_content["rows"]}
    assert by["deaths"]["mean"] == 5.0 and by["deaths"]["n"] == 500
    assert by["deaths"]["patch"] == "16.22" and by["deaths"]["source"] == "opgg"
    assert by["kda"]["status"] == "기준선 없음"


async def test_next_tier_missing_baselines(store):
    async with Client(make_server(store, EmptyRiot())) as c:
        await _setup(c, store, _mixed(20))
        r = await _x(c, "next_tier")
    sc = r.structured_content
    assert not r.is_error and sc["tier"] == "GOLD" and sc["next_tier"] == "PLATINUM"
    assert {row["status"] for row in sc["rows"]} == {"기준선 없음"}
    assert all(row["difference"] is None for row in sc["rows"])
    assert "기준선 없음" in r.content[0].text


async def test_next_tier_with_baselines_and_partial_missing(store):
    stats = _mixed(20)
    async with Client(make_server(store, EmptyRiot())) as c:
        await _setup(c, store, stats)
        store.upsert_baselines([
            _bl("GOLD", "deaths", 5.0), _bl("PLATINUM", "deaths", 4.5),
            _bl("GOLD", "kda", 2.0),  # 다음 티어 없음
        ])
        r = await _x(c, "next_tier")
    by = {row["metric"]: row for row in r.structured_content["rows"]}
    d = by["deaths"]
    assert d["tier_mean"] == 5.0 and d["next_tier_mean"] == 4.5 and d["difference"] == -0.5
    assert abs(d["my_value"] - sum(s.deaths for s in stats) / 20) < 0.01
    assert by["kda"]["status"] == "기준선 없음" and by["kda"]["difference"] is None
    assert by["kda"]["tier_mean"] == 2.0


async def test_tier_unknown_gives_note(store):
    class Unranked(EmptyRiot):
        def __init__(self):
            super().__init__(league="league_empty")

    async with Client(make_server(store, Unranked())) as c:
        await _setup(c, store, _mixed(20))
        r = await _x(c, "tier_baselines")
    sc = r.structured_content
    assert not r.is_error and sc["tier"] is None and sc["rows"] == [] and sc["notes"]
