import subprocess
import sys
from dataclasses import replace

import httpx
import pytest
from mcp import Client

from conftest import PUUID_ME, load_fixture
from lol_coach_mcp import champions, review, server
from lol_coach_mcp.opgg.client import OpggClient
from lol_coach_mcp.riot.errors import RiotRateLimited, RiotServerError
from lol_coach_mcp.stats import extract_match_stats
from test_server_champions import FakeDD, FakeOpgg, _games, make
from test_server_champions import EmptyRiot as ChampRiot
from test_server_dashboard import make as dash_make
from test_server_form import EmptyRiot
from test_server_profile import RIOT_ID, FakeRiot, make_server, store  # noqa: F401
from test_server_review import ReviewRiot


# --- I2: 비 Windows 대시보드 열기 ---
@pytest.mark.parametrize("platform,opener", [("darwin", "open"), ("linux", "xdg-open")])
def test_default_open_url_non_windows_silences_output(monkeypatch, platform, opener):
    calls = []

    class FakePopen:
        def __init__(self, args, **kw):
            calls.append((args, kw))

    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    assert server.default_open_url("file:///x/a.html") is True
    args, kw = calls[0]
    assert args == [opener, "file:///x/a.html"]
    assert kw["stdout"] is subprocess.DEVNULL and kw["stderr"] is subprocess.DEVNULL
    assert kw["start_new_session"] is True


def test_default_open_url_non_windows_missing_opener_returns_false(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("xdg-open")

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(subprocess, "Popen", boom)
    assert server.default_open_url("file:///x/a.html") is False


def test_default_open_url_windows_uses_webbrowser(monkeypatch):
    seen = []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(server.webbrowser, "open", lambda u: seen.append(u) or True)
    assert server.default_open_url("file:///x/a.html") is True
    assert seen == ["file:///x/a.html"]


# --- M4: FiddleSticks 대소문자 ---
FIDDLE_DD = {"Fiddlesticks": {"id": 9, "name": "피들스틱", "tags": ["Mage"]}}


def test_resolve_champion_prefers_played_key_case():
    assert champions.resolve_champion("피들스틱", FIDDLE_DD, ["FiddleSticks"]) == "FiddleSticks"
    assert champions.resolve_champion("fiddlesticks", FIDDLE_DD, ["FiddleSticks"]) == "FiddleSticks"
    assert champions.resolve_champion("피들스틱", FIDDLE_DD, []) == "Fiddlesticks"


async def test_champion_report_focus_with_fiddlesticks_case_mismatch(store):
    stats = _games([("FiddleSticks", 12, 7)])
    for s in stats:
        store.save_match(s.match_id, {}, s)
    async with Client(make(store, ChampRiot(), FakeOpgg(), FakeDD(data=FIDDLE_DD))) as c:
        await c.call_tool("my_profile", {"action": "set", "riot_id": RIOT_ID})
        r = await c.call_tool("champion_report", {"mode": "focus", "champion": "피들스틱"})
    sc = r.structured_content
    assert not r.is_error
    assert sc["champion"] == "FiddleSticks" and sc["champion_name"] == "피들스틱"
    assert sc["summary"]["games"] == 12


# --- M5: 타임라인 오류 통일 ---
class TlErrRiot(ReviewRiot):
    def __init__(self, error, **kw):
        super().__init__(**kw)
        self.error = error

    async def get_timeline(self, match_id):
        self.timeline_calls += 1
        raise self.error


TL_ERRORS = [RiotRateLimited("429"), RiotServerError("503"), httpx.ConnectError("down")]


@pytest.mark.parametrize("err", TL_ERRORS)
async def test_review_match_degrades_on_any_timeline_error(store, err):
    async with Client(make_server(store, TlErrRiot(err))) as c:
        await c.call_tool("my_profile", {"action": "set", "riot_id": RIOT_ID})
        r = await c.call_tool("review_match", {"match_id": "KR_1"})
    assert not r.is_error
    sc = r.structured_content
    assert sc["status"] == "판단 보류" and "타임라인 데이터 없음" in sc["notes"]
    assert sc["summary"] is not None


@pytest.mark.parametrize("err", TL_ERRORS)
async def test_dashboard_match_view_degrades_on_any_timeline_error(store, tmp_path, err):
    server_, _, _ = dash_make(store, tmp_path, TlErrRiot(err))
    async with Client(server_) as c:
        await c.call_tool("my_profile", {"action": "set", "riot_id": RIOT_ID})
        r = await c.call_tool("open_dashboard", {"view": "match", "match_id": "KR_1",
                                                 "open_browser": False})
    assert not r.is_error
    assert "타임라인 데이터 없음" in r.structured_content["notes"]


@pytest.mark.parametrize("err", TL_ERRORS)
async def test_find_habits_backfill_skips_on_any_timeline_error(store, err):
    for i in range(1, 4):
        m = load_fixture("match_normal")
        m["metadata"]["matchId"] = f"KR_{i}"
        m["info"]["gameCreation"] = 1_700_000_000_000 + i * 3_600_000
        store.save_match(f"KR_{i}", m, extract_match_stats(m, PUUID_ME))
    async with Client(make_server(store, TlErrRiot(err))) as c:
        await c.call_tool("my_profile", {"action": "set", "riot_id": RIOT_ID})
        r = await c.call_tool("find_habits", {})
    assert not r.is_error
    assert any("타임라인이 없는 경기 3판 제외" in n for n in r.structured_content["notes"])


# --- M10: 위치 없는 데스 ---
def test_death_without_position_has_unknown_zone():
    match = load_fixture("match_normal")
    timeline = load_fixture("timeline_normal")
    pid = review.participant_id(timeline, PUUID_ME)
    n = 0
    for f in timeline["info"]["frames"]:
        for e in f["events"]:
            if e["type"] == "CHAMPION_KILL" and e.get("victimId") == pid:
                e.pop("position", None)
                n += 1
    assert n
    ds = review.deaths(match, timeline, PUUID_ME)
    assert len(ds) == n and {d["zone"] for d in ds} == {"알 수 없음"}


# --- M11: 손상된 Data Dragon 캐시 ---
async def test_ddragon_value_error_degrades_like_http_error(store):
    dd = FakeDD(error=ValueError("corrupt cache"))
    stats = _games([("Kaisa", 12, 8)])
    for s in stats:
        store.save_match(s.match_id, {}, s)
    async with Client(make(store, ChampRiot(), FakeOpgg(), dd)) as c:
        await c.call_tool("my_profile", {"action": "set", "riot_id": RIOT_ID})
        r = await c.call_tool("champion_report", {"mode": "focus", "champion": "Kaisa"})
    assert not r.is_error and any("Data Dragon" in n for n in r.structured_content["notes"])


# --- M12: OP.GG 파서 RecursionError ---
async def test_opgg_recursion_error_in_parse_returns_none(store, monkeypatch):
    from datetime import datetime, timezone

    from lol_coach_mcp.opgg import client as client_mod

    async def fake(tool, args):
        return "x"

    def deep(text):
        raise RecursionError()

    monkeypatch.setattr(client_mod, "parse_opgg", deep)
    c = OpggClient(store, connect=None, now=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc))
    monkeypatch.setattr(c, "_call", fake)
    assert await c.champions() is None


# --- M9: 대시보드 y 라벨 ---
async def test_season_lp_chart_y_label(store, tmp_path):
    from datetime import datetime, timedelta, timezone

    from test_server_form import _mixed, _save

    server_, _, _ = dash_make(store, tmp_path)
    async with Client(server_) as c:
        await c.call_tool("my_profile", {"action": "set", "riot_id": RIOT_ID})
        _save(store, _mixed(5))
        entry = [{"queueType": "RANKED_SOLO_5x5", "tier": "GOLD", "rank": "II",
                  "leaguePoints": 10, "wins": 1, "losses": 1}]
        store.add_rank_snapshot(entry, (datetime.now(timezone.utc) + timedelta(days=1)).isoformat())
        r = await c.call_tool("open_dashboard", {"view": "season", "open_browser": False})
    from pathlib import Path

    html = Path(r.structured_content["path"]).read_text(encoding="utf-8")
    assert "티어·LP 환산(표시용)" in html
