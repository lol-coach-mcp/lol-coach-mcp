from datetime import datetime, timedelta, timezone

from mcp import Client

from test_server_form import EmptyRiot, _mixed, _ms, _setup
from test_server_profile import RIOT_ID, make_server, store  # noqa: F401

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 11, 8, 12, 0, tzinfo=KST)


def _finding(metric="deaths", created="2026-11-08T10:00:00+09:00", major="16", kind="약점"):
    return {
        "created_at": created, "kind": kind, "metric": metric, "my_value": 5.0,
        "ref_value": 3.0, "ref_source": "내 승리 판 평균", "games": 20,
        "period_desc": "최근 20판", "season_major": major, "position": "BOTTOM",
    }


def _goal(**kw):
    base = {
        "created_at": "2026-11-08T10:00:00+09:00", "metric": "deaths", "direction": "down",
        "target": 3.5, "baseline_value": 5.1, "start_after_ms": 0, "games_to_track": 10,
        "position": "BOTTOM",
    }
    return {**base, **kw}


async def test_analyze_form_saves_findings_once_per_kst_day(store):
    clock = {"t": NOW}
    async with Client(make_server(store, EmptyRiot(), now=lambda: clock["t"])) as c:
        await _setup(c, store, _mixed(20))
        r = await c.call_tool("analyze_form", {"compare_to": "best_self", "count": 20})
        sc = r.structured_content
        saved = store.list_findings(50)
        assert len(saved) == len(sc["weaknesses"]) + len(sc["strengths"]) > 0
        weak = [f for f in saved if f["kind"] == "약점"]
        assert {f["metric"] for f in weak} == {w["metric"] for w in sc["weaknesses"]}
        f = next(f for f in saved if f["metric"] == "deaths")
        assert f["kind"] == "약점" and f["position"] == "BOTTOM" and f["games"] == 20
        assert f["period_desc"] == sc["period_desc"] and f["season_major"] == "16"
        assert f["ref_source"] == "내 승리 판 평균" and f["created_at"].startswith("2026-11-08")
        await c.call_tool("analyze_form", {"compare_to": "best_self", "count": 20})
        assert len(store.list_findings(50)) == len(saved)
        clock["t"] = NOW + timedelta(days=1)
        await c.call_tool("analyze_form", {"compare_to": "best_self", "count": 20})
        assert len(store.list_findings(50)) == 2 * len(saved)


async def test_analyze_form_without_findings_saves_nothing(store):
    async with Client(make_server(store, EmptyRiot(), now=lambda: NOW)) as c:
        await _setup(c, store, _mixed(6))
        await c.call_tool("analyze_form", {"compare_to": "best_self"})
    assert store.list_findings(10) == []


async def test_context_empty_memory(store):
    async with Client(make_server(store, EmptyRiot(), now=lambda: NOW)) as c:
        await _setup(c, store, _mixed(3))
        r = await c.call_tool("get_coach_context", {})
    sc = r.structured_content
    assert sc["goals"] == [] and sc["findings"] == [] and sc["journal"] == []
    assert "집중 과제 없음" in r.content[0].text


async def test_context_goals_findings_journal(store):
    async with Client(make_server(store, EmptyRiot(), now=lambda: NOW)) as c:
        await _setup(c, store, [_ms(i, patch="16.22") for i in range(3)])
        g1 = store.add_goal(_goal())
        g2 = store.add_goal(_goal(metric="kda", direction="up", target=3.0))
        store.update_goal(g2, status="달성")
        for i, m in enumerate(["deaths", "kda", "cs_per_min", "vision_per_min", "gold_per_min",
                               "damage_share"]):
            store.add_findings([_finding(m, created=f"2026-11-0{i + 1}T10:00:00+09:00")])
        for i in range(5):
            store.add_journal(f"2026-11-08T1{i}:00:00+09:00", None, f"메모{i}")
        r = await c.call_tool("get_coach_context", {})
    sc = r.structured_content
    assert [g["id"] for g in sc["goals"]] == [g1]
    assert sc["goals"][0]["metric"] == "deaths" and sc["goals"][0]["status"] == "진행 중"
    assert len(sc["findings"]) == 5
    assert sc["findings"][0]["metric"] == "damage_share"
    assert all(f["stale"] is False for f in sc["findings"])
    assert [j["text"] for j in sc["journal"]] == ["메모4", "메모3", "메모2"]
    text = r.content[0].text
    assert "하세요" not in text and "deaths" in text and "메모4" in text


async def test_context_stale_by_age_and_by_patch_major(store):
    async with Client(make_server(store, EmptyRiot(), now=lambda: NOW)) as c:
        await _setup(c, store, [_ms(i, patch="16.22") for i in range(3)])
        store.add_findings([
            _finding("deaths", created="2026-11-08T10:00:00+09:00", major="16"),
            _finding("kda", created="2026-10-09T11:59:00+09:00", major="16"),   # 30일 초과(30일 0시간 1분)
            _finding("cs_per_min", created="2026-10-09T12:00:00+09:00", major="16"),  # 정확히 30일
            _finding("gold_per_min", created="2026-11-07T10:00:00+09:00", major="15"),
        ])
        r = await c.call_tool("get_coach_context", {})
    by = {f["metric"]: f for f in r.structured_content["findings"]}
    assert by["deaths"]["stale"] is False
    assert by["kda"]["stale"] is True
    assert by["cs_per_min"]["stale"] is False
    assert by["gold_per_min"]["stale"] is True
    assert "오래된 기록" in r.content[0].text


async def test_forget_and_account_switch_wipe_memory(store):
    def fill():
        store.add_findings([_finding()])
        store.add_goal(_goal())
        store.add_journal("2026-11-08T10:00:00+09:00", None, "x")

    def empty():
        return store.list_findings(5) == [] and store.list_goals() == [] \
            and store.list_journal(5) == []

    async with Client(make_server(store, EmptyRiot(), now=lambda: NOW)) as c:
        await c.call_tool("my_profile", {"action": "set", "riot_id": RIOT_ID})
        fill()
        await c.call_tool("my_profile", {"action": "forget"})
        assert empty()
        await c.call_tool("my_profile", {"action": "set", "riot_id": RIOT_ID})
        fill()
        await c.call_tool("my_profile", {"action": "forget", "include_matches": True})
        assert empty()
        await c.call_tool("my_profile", {"action": "set", "riot_id": RIOT_ID})
        fill()
        r = await c.call_tool("my_profile", {"action": "set", "riot_id": "다른사람#KR1"})
        assert not r.is_error
        assert empty()
