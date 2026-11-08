from datetime import datetime, timedelta, timezone

from mcp import Client

from test_server_form import BASE, HOUR, EmptyRiot, _mixed, _ms, _save, _setup
from test_server_profile import make_server, store  # noqa: F401

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 11, 8, 12, 0, tzinfo=KST)


async def _call(c, **args):
    return await c.call_tool("goals", args)


async def test_tool_listed(store):
    async with Client(make_server(store, EmptyRiot())) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
    assert tools["goals"].output_schema


async def test_requires_profile(store):
    async with Client(make_server(store, EmptyRiot())) as c:
        r = await _call(c, action="list")
    assert r.is_error and "my_profile" in r.content[0].text


async def test_check_and_list_without_goals_are_empty_not_error(store):
    async with Client(make_server(store, EmptyRiot(), now=lambda: NOW)) as c:
        await _setup(c, store, _mixed(3))
        for action in ("check", "list"):
            r = await _call(c, action=action)
            assert not r.is_error and r.structured_content["goals"] == []
            assert "과제 없음" in r.content[0].text


async def test_suggest_returns_first_weakness_and_practice_without_saving(store):
    async with Client(make_server(store, EmptyRiot(), now=lambda: NOW)) as c:
        await _setup(c, store, _mixed(20))
        r = await _call(c, action="suggest")
    assert not r.is_error
    sc = r.structured_content
    s = sc["suggestion"]
    assert s["games"] == 10 and s["metric"] in sc["practice"] and len(sc["practice"][s["metric"]]) >= 2
    assert s["direction"] == ("down" if s["metric"] == "deaths" else "up")
    assert store.list_goals() == []
    text = r.content[0].text
    assert "하세요" not in text and "솔로랭크" in text and s["metric"] in text


async def test_suggest_without_weakness_gives_reason(store):
    async with Client(make_server(store, EmptyRiot(), now=lambda: NOW)) as c:
        await _setup(c, store, _mixed(6))
        r = await _call(c, action="suggest")
    sc = r.structured_content
    assert not r.is_error and sc["suggestion"] is None and sc["reason"]


async def test_set_saves_goal_with_baseline_and_start(store):
    async with Client(make_server(store, EmptyRiot(), now=lambda: NOW)) as c:
        stats = _mixed(20)
        await _setup(c, store, stats)
        r = await _call(c, action="set", metric="deaths", target=3.0, games=5)
    assert not r.is_error
    g = store.list_goals()[0]
    mean = sum(s.deaths for s in stats) / 20
    assert g["metric"] == "deaths" and g["direction"] == "down" and g["target"] == 3.0
    assert g["games_to_track"] == 5 and g["position"] == "BOTTOM" and g["status"] == "진행 중"
    assert abs(g["baseline_value"] - mean) < 1e-9
    assert g["start_after_ms"] == max(s.game_creation for s in stats)
    assert g["created_at"].startswith("2026-11-08")
    assert r.structured_content["goals"][0]["id"] == g["id"]


async def test_set_without_target_uses_suggestion_else_errors(store):
    async with Client(make_server(store, EmptyRiot(), now=lambda: NOW)) as c:
        await _setup(c, store, _mixed(20))
        sug = (await _call(c, action="suggest")).structured_content["suggestion"]
        r = await _call(c, action="set", metric=sug["metric"])
        assert not r.is_error
        assert store.list_goals()[0]["target"] == sug["target"]
        r = await _call(c, action="set", metric="gold_per_min")  # 약점 아님 + 목표 없음
        assert r.is_error and "target" in r.content[0].text


async def test_set_validation(store):
    async with Client(make_server(store, EmptyRiot(), now=lambda: NOW)) as c:
        await _setup(c, store, _mixed(20))
        for args in ({"metric": "win", "target": 1.0}, {"metric": "nope", "target": 1.0},
                     {"target": 3.0}, {"metric": "deaths", "target": 3.0, "games": 0}):
            r = await _call(c, action="set", **args)
            assert r.is_error
    assert store.list_goals() == []


async def test_set_without_games_errors(store):
    async with Client(make_server(store, EmptyRiot(), now=lambda: NOW)) as c:
        await c.call_tool("my_profile", {"action": "set", "riot_id": "서석하는댄스머신밤만되면센스머신#빨고싶어"})
        r = await _call(c, action="set", metric="deaths", target=3.0)
    assert r.is_error


async def _with_goal(c, store, games=3):
    stats = _mixed(20)
    await _setup(c, store, stats)
    await _call(c, action="set", metric="deaths", target=3.0, games=games)
    return max(s.game_creation for s in stats)


async def test_check_progress_then_achieved(store):
    async with Client(make_server(store, EmptyRiot(), now=lambda: NOW)) as c:
        last = await _with_goal(c, store)
        _save(store, [_ms(100 + i, deaths=2, created=last + (i + 1) * HOUR) for i in range(2)])
        r = await _call(c, action="check")
        g = r.structured_content["goals"][0]
        assert g["progress"]["games_done"] == 2 and g["status"] == "진행 중"
        assert "2/3판" in r.content[0].text
        assert store.list_goals()[0]["status"] == "진행 중"
        _save(store, [_ms(102, deaths=2, created=last + 3 * HOUR),
                      _ms(103, deaths=9, created=last + 4 * HOUR)])
        r = await _call(c, action="check")
        g = r.structured_content["goals"][0]
        assert g["status"] == "달성" and g["progress"]["games_done"] == 3
        row = store.list_goals()[0]
        assert row["status"] == "달성" and row["closed_at"].startswith("2026-11-08")
        r = await _call(c, action="check")  # 이미 종료된 과제는 대상 아님
        assert r.structured_content["goals"] == []


async def test_check_missed_and_other_position_excluded(store):
    async with Client(make_server(store, EmptyRiot(), now=lambda: NOW)) as c:
        last = await _with_goal(c, store)
        _save(store, [_ms(100 + i, deaths=0, position="TOP", created=last + (i + 1) * HOUR)
                      for i in range(5)])
        r = await _call(c, action="check")
        assert r.structured_content["goals"][0]["progress"]["games_done"] == 0
        _save(store, [_ms(200 + i, deaths=8, created=last + (10 + i) * HOUR) for i in range(3)])
        r = await _call(c, action="check")
    assert r.structured_content["goals"][0]["status"] == "미달"
    assert store.list_goals()[0]["status"] == "미달"


async def test_check_unknown_goal_id_and_complete(store):
    async with Client(make_server(store, EmptyRiot(), now=lambda: NOW)) as c:
        await _with_goal(c, store)
        gid = store.list_goals()[0]["id"]
        assert (await _call(c, action="check", goal_id=999)).is_error
        assert (await _call(c, action="complete")).is_error
        assert (await _call(c, action="complete", goal_id=999)).is_error
        r = await _call(c, action="complete", goal_id=gid)
        assert not r.is_error
        row = store.list_goals()[0]
        assert row["status"] == "종료" and row["closed_at"]
        r = await _call(c, action="list")
    assert [g["status"] for g in r.structured_content["goals"]] == ["종료"]


async def test_check_offline_fallback_uses_stored(store):
    class Down(EmptyRiot):
        async def get_match_ids(self, *a, **k):
            import httpx
            raise httpx.ConnectTimeout("t")

    async with Client(make_server(store, Down(), now=lambda: NOW)) as c:
        last = await _with_goal(c, store)
        _save(store, [_ms(100, deaths=2, created=last + HOUR)])
        r = await _call(c, action="check")
    assert not r.is_error and r.structured_content["goals"][0]["progress"]["games_done"] == 1
