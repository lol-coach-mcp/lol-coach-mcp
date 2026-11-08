import copy
from dataclasses import replace

import httpx
import pytest
from mcp import Client

from conftest import load_fixture
from lol_coach_mcp.riot.errors import RiotServerError
from lol_coach_mcp.stats import MatchStats
from test_server_profile import RIOT_ID, FakeRiot, make_server, store  # noqa: F401

BASE = 1_700_000_000_000
HOUR = 3_600_000


def _ms(i, *, win=True, position="BOTTOM", deaths=2, kills=5, assists=5, cs=210,
        duration_s=1800, gold=12000, vision=30, dmg=0.25, kp=0.5, patch="16.22",
        queue_id=420, created=None):
    return MatchStats(
        match_id=f"KR_{i:010d}", queue_id=queue_id,
        game_creation=created if created is not None else BASE + i * HOUR,
        duration_s=duration_s, patch=patch, is_remake=False, win=win, position=position,
        champion="Jinx", kills=kills, deaths=deaths, assists=assists, cs=cs,
        cs_per_min=round(cs / (duration_s / 60), 1), gold=gold, vision_score=vision,
        damage_to_champions=20000, damage_share=dmg, kill_participation=kp,
        team_id=100, teammates=("a", "b", "c", "d"),
    )


def _save(store, stats):
    for s in stats:
        store.save_match(s.match_id, {}, s)


async def _setup(c, store, stats, league=None):
    await c.call_tool("my_profile", {"action": "set", "riot_id": RIOT_ID})
    _save(store, stats)


class EmptyRiot(FakeRiot):
    def __init__(self, league="league_ranked", **kw):
        super().__init__(n_matches=0, **kw)
        self.league = load_fixture(league)
        self.league_calls = 0
        self.fail_league = False

    async def get_league_entries(self, puuid):
        self.league_calls += 1
        if self.fail_league:
            raise httpx.ConnectTimeout("t")
        return copy.deepcopy(self.league)


def _mixed(n=20):
    """승/패가 섞인 n판: 승리 판은 데스가 적고 CS가 높음."""
    out = []
    for i in range(n):
        win = i % 2 == 0
        out.append(_ms(i, win=win, deaths=1 + (i % 3) if win else 6 + (i % 3),
                       cs=240 + i if win else 170 + i))
    return out


async def test_tool_listed_with_schema(store):
    async with Client(make_server(store, EmptyRiot())) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
    assert tools["analyze_form"].output_schema


async def test_requires_profile(store):
    async with Client(make_server(store, EmptyRiot())) as c:
        r = await c.call_tool("analyze_form", {})
    assert r.is_error and "my_profile" in r.content[0].text


async def test_best_self_basic_and_metadata(store):
    async with Client(make_server(store, EmptyRiot())) as c:
        await _setup(c, store, _mixed(20))
        r = await c.call_tool("analyze_form", {"compare_to": "best_self", "count": 20})
    assert not r.is_error
    sc = r.structured_content
    assert sc["games"] == 20 and sc["queue"] == 420 and sc["position"] == "BOTTOM"
    assert sc["excluded_other_positions"] == 0
    assert sc["compare_to"] == "best_self" and sc["ref_source"] == "내 승리 판 평균"
    metrics = [row["metric"] for row in sc["rows"]]
    assert "win" not in metrics and "deaths" in metrics
    assert any(w["metric"] == "deaths" for w in sc["weaknesses"])
    text = r.content[0].text
    assert "최근 20판" in text and "솔로랭크" in text and "BOTTOM" in text
    assert "하세요" not in text


async def test_under_ten_games_all_hold_and_no_weaknesses(store):
    async with Client(make_server(store, EmptyRiot())) as c:
        await _setup(c, store, _mixed(6))
        r = await c.call_tool("analyze_form", {"compare_to": "best_self"})
    assert not r.is_error
    sc = r.structured_content
    assert sc["games"] == 6
    assert {row["status"] for row in sc["rows"]} == {"판단 보류(표본 부족)"}
    assert sc["weaknesses"] == [] and sc["strengths"] == []


async def test_mixed_positions_main_only_and_excluded_count(store):
    stats = _mixed(14) + [_ms(100 + i, position="JUNGLE") for i in range(4)]
    async with Client(make_server(store, EmptyRiot())) as c:
        await _setup(c, store, stats)
        r = await c.call_tool("analyze_form", {"compare_to": "best_self", "count": 30})
    sc = r.structured_content
    assert sc["position"] == "BOTTOM" and sc["games"] == 14
    assert sc["excluded_other_positions"] == 4
    assert "4판 제외" in r.content[0].text


async def test_all_wins_best_self_not_comparable(store):
    stats = [_ms(i, win=True, deaths=i % 4) for i in range(15)]
    async with Client(make_server(store, EmptyRiot())) as c:
        await _setup(c, store, stats)
        r = await c.call_tool("analyze_form", {"compare_to": "best_self"})
    sc = r.structured_content
    assert {row["status"] for row in sc["rows"]} == {"비교 불가"}
    assert "win" not in [row["metric"] for row in sc["rows"]]
    assert sc["weaknesses"] == [] and sc["strengths"] == []


async def test_all_losses_best_self_not_comparable(store):
    stats = [_ms(i, win=False) for i in range(15)]
    async with Client(make_server(store, EmptyRiot())) as c:
        await _setup(c, store, stats)
        r = await c.call_tool("analyze_form", {"compare_to": "best_self"})
    assert {row["status"] for row in r.structured_content["rows"]} == {"비교 불가"}


async def test_tier_baseline_missing_returns_best_self_rows_too(store):
    async with Client(make_server(store, EmptyRiot())) as c:
        await _setup(c, store, _mixed(20))
        r = await c.call_tool("analyze_form", {"compare_to": "tier_baseline"})
    sc = r.structured_content
    assert sc["compare_to"] == "tier_baseline"
    assert {row["status"] for row in sc["rows"]} == {"기준선 없음"}
    assert sc["weaknesses"] == []
    assert sc["best_self_rows"] and sc["best_self_ref_source"] == "내 승리 판 평균"
    assert any(w["metric"] == "deaths" for w in sc["best_self_weaknesses"])
    assert "GOLD" in sc["ref_source"]


async def test_tier_baseline_present_judges_against_baseline(store):
    stats = [_ms(i, win=i % 2 == 0, deaths=6) for i in range(20)]
    async with Client(make_server(store, EmptyRiot())) as c:
        await _setup(c, store, stats)
        store.upsert_baselines([
            {"tier": "GOLD", "position": "BOTTOM", "metric": "deaths", "mean": 3.0,
             "std": 1.0, "n": 200, "patch": "16.22", "collected_at": "2026-11-01", "source": "t"},
        ])
        r = await c.call_tool("analyze_form", {"compare_to": "tier_baseline"})
    sc = r.structured_content
    by = {row["metric"]: row for row in sc["rows"]}
    assert by["deaths"]["status"] == "ok" and by["deaths"]["ref_value"] == 3.0
    assert by["kda"]["status"] == "기준선 없음"
    assert [w["metric"] for w in sc["weaknesses"]] == ["deaths"]
    assert sc["best_self_rows"] is None


async def test_tier_uses_snapshot_without_calling_league(store):
    riot = EmptyRiot()
    async with Client(make_server(store, riot)) as c:
        await _setup(c, store, _mixed(12))
        store.add_rank_snapshot([{"queueType": "RANKED_SOLO_5x5", "tier": "DIAMOND",
                                  "rank": "IV"}], "2026-11-01T00:00:00+00:00")
        r = await c.call_tool("analyze_form", {"compare_to": "tier_baseline"})
    assert riot.league_calls == 0
    assert "DIAMOND" in r.structured_content["ref_source"]


async def test_unranked_falls_back_to_best_self_with_note(store):
    async with Client(make_server(store, EmptyRiot("league_empty"))) as c:
        await _setup(c, store, _mixed(20))
        r = await c.call_tool("analyze_form", {"compare_to": "tier_baseline"})
    sc = r.structured_content
    assert sc["compare_to"] == "tier_baseline"
    assert sc["compare_used"] == "best_self" and sc["ref_source"] == "내 승리 판 평균"
    assert any("언랭" in n for n in sc["notes"])
    assert sc["best_self_rows"] is None
    assert any(w["metric"] == "deaths" for w in sc["weaknesses"])


async def test_previous_period(store):
    # 이전 20판은 데스 2, 최근 20판은 데스 7
    old = [_ms(i, win=i % 2 == 0, deaths=2 + i % 2) for i in range(20)]
    new = [_ms(100 + i, win=i % 2 == 0, deaths=7 + i % 2) for i in range(20)]
    async with Client(make_server(store, EmptyRiot())) as c:
        await _setup(c, store, old + new)
        r = await c.call_tool("analyze_form", {"compare_to": "previous_period", "count": 20})
    sc = r.structured_content
    by = {row["metric"]: row for row in sc["rows"]}
    assert by["deaths"]["ref_value"] == pytest.approx(2.5)
    assert by["deaths"]["my_value"] == pytest.approx(7.5)
    assert "win" in by
    assert [w["metric"] for w in sc["weaknesses"]][:1] == ["deaths"]
    assert sc["ref_source"] == "직전 20판"


async def test_session_period_and_tilt(store):
    old = [_ms(i, created=BASE + i * HOUR * 10) for i in range(5)]
    sess = [_ms(50 + i, win=False, created=BASE + 10**9 + i * 2_400_000) for i in range(4)]
    async with Client(make_server(store, EmptyRiot())) as c:
        await _setup(c, store, old + sess)
        r = await c.call_tool("analyze_form", {"period": "session", "compare_to": "best_self"})
    sc = r.structured_content
    assert sc["games"] == 4 and "세션" in sc["period_desc"]
    assert "3연패 이상" in sc["tilt_signals"]
    assert sc["style"] and sc["records"]["best_kda"] is not None


async def test_season_period_filters_patch(store):
    stats = [_ms(i, patch="15.20") for i in range(5)] + [_ms(10 + i, patch="16.22") for i in range(12)]
    async with Client(make_server(store, EmptyRiot())) as c:
        await _setup(c, store, stats)
        r = await c.call_tool("analyze_form", {"period": "season", "compare_to": "best_self"})
    assert r.structured_content["games"] == 12


async def test_queue_default_excludes_other_queues(store):
    stats = _mixed(12) + [_ms(200 + i, queue_id=440) for i in range(5)]
    async with Client(make_server(store, EmptyRiot())) as c:
        await _setup(c, store, stats)
        r = await c.call_tool("analyze_form", {"compare_to": "best_self"})
    assert r.structured_content["games"] == 12 and r.structured_content["queue"] == 420


async def test_no_games_is_not_error(store):
    async with Client(make_server(store, EmptyRiot())) as c:
        await c.call_tool("my_profile", {"action": "set", "riot_id": RIOT_ID})
        r = await c.call_tool("analyze_form", {"compare_to": "best_self"})
    assert not r.is_error and r.structured_content["games"] == 0


class _Down(EmptyRiot):
    def __init__(self, exc):
        super().__init__()
        self.exc = exc

    async def get_match_ids(self, puuid, **kw):
        raise self.exc


@pytest.mark.parametrize("exc", [RiotServerError(), httpx.ConnectTimeout("t")], ids=["riot", "net"])
async def test_sync_failure_falls_back_with_note(store, exc):
    async with Client(make_server(store, _Down(exc))) as c:
        await _setup(c, store, _mixed(12))
        r = await c.call_tool("analyze_form", {"compare_to": "best_self"})
    assert not r.is_error
    assert "동기화 실패, 저장된 데이터 기준" in r.structured_content["notes"]
    assert "동기화 실패, 저장된 데이터 기준" in r.content[0].text


async def test_sync_failure_without_data_is_error(store):
    async with Client(make_server(store, _Down(httpx.ConnectTimeout("t")))) as c:
        await c.call_tool("my_profile", {"action": "set", "riot_id": RIOT_ID})
        r = await c.call_tool("analyze_form", {})
    assert r.is_error


async def test_league_failure_degrades_to_best_self(store):
    riot = EmptyRiot()
    riot.fail_league = True
    async with Client(make_server(store, riot)) as c:
        await _setup(c, store, _mixed(12))
        r = await c.call_tool("analyze_form", {"compare_to": "tier_baseline"})
    sc = r.structured_content
    assert not r.is_error and sc["compare_used"] == "best_self"
    assert any("티어" in n for n in sc["notes"])
