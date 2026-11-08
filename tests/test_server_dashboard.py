import re
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from mcp import Client

from lol_coach_mcp.config import Settings
from lol_coach_mcp.server import AppState, create_server
from test_server_champions import ANALYSIS, DD, LANE_META, FakeOpgg
from test_server_form import EmptyRiot, _mixed, _ms, _save
from test_server_profile import RIOT_ID, FakeRiot, store  # noqa: F401
from test_server_review import ReviewRiot

NOW = datetime(2026, 11, 8, 12, 30, 45, tzinfo=timezone.utc)
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
VIEWS = ("overview", "season", "champion", "match")
DISCLAIMER_HEAD = "lol-coach-mcp is not endorsed by Riot Games"


class FakeDD:
    def __init__(self, map_error=None):
        self.map_error = map_error
        self.map_calls = []

    async def champions(self):
        return DD

    async def map_image(self, cache_dir):
        self.map_calls.append(cache_dir)
        if self.map_error:
            raise self.map_error
        return PNG


class Opener:
    def __init__(self, result=True, error=None):
        self.result, self.error = result, error
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        if self.error:
            raise self.error
        return self.result


def make(store, tmp_path, riot=None, *, dd="default", opener=None, opgg="default"):
    riot = riot or EmptyRiot()
    opener = opener if opener is not None else Opener()
    settings = Settings(riot_api_key="k", data_dir=tmp_path / "data", user_agent="t")
    state = AppState(
        settings=settings, riot=riot, store=store, now=lambda: NOW, open_url=opener,
        ddragon=FakeDD() if dd == "default" else dd,
        opgg=FakeOpgg() if opgg == "default" else opgg,
    )
    return create_server(lambda: state), opener, tmp_path / "data" / "reports"


async def _register(c):
    await c.call_tool("my_profile", {"action": "set", "riot_id": RIOT_ID})


def _html(out):
    from pathlib import Path

    return Path(out["path"]).read_text(encoding="utf-8")


def _poly_points(html):
    return [len(m.split()) for m in re.findall(r'<polyline[^>]*points="([^"]*)"', html)]


async def test_tool_listed_with_schema(store, tmp_path):
    server, _, _ = make(store, tmp_path)
    async with Client(server) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
    assert tools["open_dashboard"].output_schema


async def test_requires_profile(store, tmp_path):
    server, opener, _ = make(store, tmp_path)
    async with Client(server) as c:
        r = await c.call_tool("open_dashboard", {})
    assert r.is_error and "my_profile" in r.content[0].text
    assert opener.calls == []


async def test_overview_renders_and_opens(store, tmp_path):
    server, opener, reports = make(store, tmp_path)
    async with Client(server) as c:
        await _register(c)
        _save(store, _mixed(20))
        r = await c.call_tool("open_dashboard", {})
    assert not r.is_error
    out = r.structured_content
    assert out["view"] == "overview" and out["opened"] is True
    path = reports / out["path"].split("\\")[-1].split("/")[-1]
    assert path.exists() and path.parent == reports
    assert re.fullmatch(r"overview-20261108-213045\.html", path.name)
    assert opener.calls == [path.as_uri()]
    html = _html(out)
    assert DISCLAIMER_HEAD in html
    assert {"프로필", "최근 20판 결과", "폼 분석", "진행 중 과제"} <= set(out["sections"])
    assert 'class="result-win"' in html and 'class="result-loss"' in html
    assert "GOLD" in html  # EmptyRiot league_ranked 스냅샷
    assert "http" not in html.replace(DISCLAIMER_HEAD, "")  # 외부 URL 없음
    assert "하세요" not in html
    # 티어 기준선이 없으면 내 승리 판 평균 비교와 약점·강점이 함께 표시됨
    assert "참고 비교: 내 승리 판 평균" in html and "데스" in html
    assert "약점" in html and "강점" in html


async def test_overview_shows_active_goal(store, tmp_path):
    server, _, _ = make(store, tmp_path)
    async with Client(server) as c:
        await _register(c)
        _save(store, _mixed(20))
        await c.call_tool("goals", {"action": "set", "metric": "deaths", "target": 2.5})
        r = await c.call_tool("open_dashboard", {"open_browser": False})
    html = _html(r.structured_content)
    assert "데스" in html and "2.50 이하" in html


async def test_season_renders_lp_line_since_registration_and_ma(store, tmp_path):
    server, _, _ = make(store, tmp_path)
    async with Client(server) as c:
        await _register(c)
        _save(store, _mixed(25))
        entry = lambda lp: [{"queueType": "RANKED_SOLO_5x5", "tier": "GOLD", "rank": "II",  # noqa: E731
                             "leaguePoints": lp, "wins": 1, "losses": 1}]
        store.add_rank_snapshot(entry(99), "2020-01-01T00:00:00+00:00")  # 등록 이전 → 제외
        store.add_rank_snapshot(entry(10), (datetime.now(timezone.utc) + timedelta(days=1)).isoformat())
        store.add_rank_snapshot(entry(40), (datetime.now(timezone.utc) + timedelta(days=2)).isoformat())
        r = await c.call_tool("open_dashboard", {"view": "season", "open_browser": False})
    assert not r.is_error
    out = r.structured_content
    html = _html(out)
    assert out["sections"][0] == "LP 추이"
    pts = _poly_points(html)
    assert pts[0] == 2  # 등록 이후 스냅샷 2개만
    assert pts[1:4] == [16, 16, 16]  # 25판, 10판 이동평균 → 16점
    assert "10판 이동평균: KDA" in out["sections"]
    assert "챔피언별 판수" in out["sections"] and "<rect class=\"bar\"" in html
    assert ">99<" not in html


async def test_season_under_ten_games_has_no_ma(store, tmp_path):
    server, _, _ = make(store, tmp_path)
    async with Client(server) as c:
        await _register(c)
        _save(store, _mixed(6))
        r = await c.call_tool("open_dashboard", {"view": "season", "open_browser": False})
    assert "10판 미만" in _html(r.structured_content)


async def test_champion_view_table_and_bars(store, tmp_path):
    server, _, _ = make(store, tmp_path)
    stats = _mixed(12) + [replace(_ms(100 + i), champion="Kaisa") for i in range(6)]
    async with Client(server) as c:
        await _register(c)
        _save(store, stats)
        r = await c.call_tool("open_dashboard", {"view": "champion", "open_browser": False})
    assert not r.is_error
    out = r.structured_content
    html = _html(out)
    assert "챔피언 요약" in out["sections"]
    assert "징크스" in html and "카이사" in html  # Data Dragon 한국어 이름
    assert html.count('class="bar"') >= 2


async def test_champion_view_without_opgg_or_ddragon_still_renders(store, tmp_path):
    server, _, _ = make(store, tmp_path, dd=None, opgg=None)
    async with Client(server) as c:
        await _register(c)
        _save(store, _mixed(12))
        r = await c.call_tool("open_dashboard", {"view": "champion", "open_browser": False})
    assert not r.is_error
    html = _html(r.structured_content)
    assert "Jinx" in html
    assert any("Data Dragon" in n for n in r.structured_content["notes"])


async def test_match_view_gold_deaths_objectives_and_minimap(store, tmp_path):
    dd = FakeDD()
    server, _, _ = make(store, tmp_path, ReviewRiot(), dd=dd)
    async with Client(server) as c:
        await _register(c)
        r = await c.call_tool("open_dashboard", {"view": "match", "match_id": "KR_1",
                                                 "open_browser": False})
    assert not r.is_error
    out = r.structured_content
    html = _html(out)
    assert {"경기 요약", "15분까지 골드 차", "데스 위치", "오브젝트"} <= set(out["sections"])
    assert _poly_points(html)[0] >= 10
    assert '<circle class="dot"' in html and re.search(r">\d\d:\d\d<", html)
    assert 'href="data:image/png;base64,' in html
    assert "http" not in html.replace(DISCLAIMER_HEAD, "")
    assert len(dd.map_calls) == 1 and dd.map_calls[0].parent == tmp_path / "data"


async def test_match_view_default_is_latest_stored(store, tmp_path):
    server, _, _ = make(store, tmp_path, ReviewRiot())
    async with Client(server) as c:
        await _register(c)
        r = await c.call_tool("open_dashboard", {"view": "match", "open_browser": False})
    assert not r.is_error and "경기 요약" in r.structured_content["sections"]


async def test_match_view_timeline_missing_note(store, tmp_path):
    server, _, _ = make(store, tmp_path, ReviewRiot(timeline_404=True))
    async with Client(server) as c:
        await _register(c)
        r = await c.call_tool("open_dashboard", {"view": "match", "match_id": "KR_1",
                                                 "open_browser": False})
    assert not r.is_error
    out = r.structured_content
    html = _html(out)
    assert "타임라인 데이터 없음" in out["notes"] and "타임라인 데이터 없음" in html
    assert "<polyline" not in html and "<circle" not in html


async def test_match_view_minimap_failure_uses_fallback_rect(store, tmp_path):
    dd = FakeDD(map_error=httpx.ConnectError("down"))
    server, _, _ = make(store, tmp_path, ReviewRiot(), dd=dd)
    async with Client(server) as c:
        await _register(c)
        r = await c.call_tool("open_dashboard", {"view": "match", "match_id": "KR_1",
                                                 "open_browser": False})
    assert not r.is_error
    html = _html(r.structured_content)
    assert 'class="map-bg"' in html and "data:image" not in html
    assert '<circle class="dot"' in html
    assert any("미니맵" in n for n in r.structured_content["notes"])


async def test_match_view_without_ddragon_uses_fallback_rect(store, tmp_path):
    server, _, _ = make(store, tmp_path, ReviewRiot(), dd=None)
    async with Client(server) as c:
        await _register(c)
        r = await c.call_tool("open_dashboard", {"view": "match", "match_id": "KR_1",
                                                 "open_browser": False})
    assert 'class="map-bg"' in _html(r.structured_content)


async def test_match_view_remake_has_note_and_no_charts(store, tmp_path):
    server, _, _ = make(store, tmp_path, ReviewRiot(remake_ids={"KR_9"}))
    async with Client(server) as c:
        await _register(c)
        r = await c.call_tool("open_dashboard", {"view": "match", "match_id": "KR_9",
                                                 "open_browser": False})
    assert not r.is_error
    assert any("다시하기" in n for n in r.structured_content["notes"])
    assert "<polyline" not in _html(r.structured_content)


async def test_match_view_not_my_match_is_error(store, tmp_path):
    class Foreign(ReviewRiot):
        async def get_match(self, match_id):
            m = await super().get_match(match_id)
            if match_id == "KR_77":
                for p in m["info"]["participants"]:
                    if p["puuid"] == "puuid-me":
                        p["puuid"] = "someone-else"
            return m

    server, _, _ = make(store, tmp_path, Foreign())
    async with Client(server) as c:
        await _register(c)
        r = await c.call_tool("open_dashboard", {"view": "match", "match_id": "KR_77",
                                                 "open_browser": False})
    assert r.is_error and "참가하지" in r.content[0].text


@pytest.mark.parametrize("view", VIEWS)
async def test_no_data_renders_guidance_not_error(store, tmp_path, view):
    server, opener, reports = make(store, tmp_path)
    async with Client(server) as c:
        await _register(c)
        r = await c.call_tool("open_dashboard", {"view": view, "open_browser": False})
    assert not r.is_error
    out = r.structured_content
    html = _html(out)
    assert "동기화" in html and DISCLAIMER_HEAD in html
    assert out["sections"] and out["opened"] is False and opener.calls == []
    assert (reports / out["path"].replace("\\", "/").split("/")[-1]).exists()


async def test_sync_failure_without_data_still_renders(store, tmp_path):
    from lol_coach_mcp.riot.errors import RiotServerError

    class Down(EmptyRiot):
        async def get_match_ids(self, *a, **k):
            raise RiotServerError("500")

    server, _, _ = make(store, tmp_path, Down())
    async with Client(server) as c:
        await _register(c)
        r = await c.call_tool("open_dashboard", {"open_browser": False})
    assert not r.is_error and r.structured_content["notes"]


async def test_xss_champion_name_is_escaped(store, tmp_path):
    server, _, _ = make(store, tmp_path, dd=None)
    stats = [replace(s, champion="<img src=x onerror=alert(2)>") for s in _mixed(14)]
    async with Client(server) as c:
        await _register(c)
        _save(store, stats)
        for view in ("overview", "season", "champion"):
            r = await c.call_tool("open_dashboard", {"view": view, "open_browser": False})
            html = _html(r.structured_content)
            assert "<img" not in html, view
            assert "&lt;img src=x onerror=alert(2)&gt;" in html or view == "overview", view


async def test_xss_riot_id_in_overview(store, tmp_path):
    server, _, _ = make(store, tmp_path)
    async with Client(server) as c:
        await c.call_tool("my_profile", {"action": "set", "riot_id": "<script>alert(1)</script>#KR1"})
        r = await c.call_tool("open_dashboard", {"open_browser": False})
    html = _html(r.structured_content)
    assert "<script" not in html and "&lt;script&gt;alert(1)&lt;/script&gt;" in html


async def test_opener_exception_returns_path_and_not_opened(store, tmp_path):
    opener = Opener(error=RuntimeError("no browser"))
    server, _, _ = make(store, tmp_path, opener=opener)
    async with Client(server) as c:
        await _register(c)
        r = await c.call_tool("open_dashboard", {})
    assert not r.is_error
    out = r.structured_content
    assert out["opened"] is False and len(opener.calls) == 1
    assert _html(out)
    assert out["path"] in r.content[0].text


async def test_opener_returning_false_is_not_opened(store, tmp_path):
    opener = Opener(result=False)
    server, _, _ = make(store, tmp_path, opener=opener)
    async with Client(server) as c:
        await _register(c)
        r = await c.call_tool("open_dashboard", {})
    assert r.structured_content["opened"] is False and _html(r.structured_content)


async def test_open_browser_false_does_not_call_opener(store, tmp_path):
    server, opener, _ = make(store, tmp_path)
    async with Client(server) as c:
        await _register(c)
        r = await c.call_tool("open_dashboard", {"open_browser": False})
    assert opener.calls == [] and r.structured_content["opened"] is False


def test_default_opener_is_webbrowser_open():
    import webbrowser

    st = AppState(settings=None, riot=None, store=None)
    assert st.open_url is webbrowser.open


async def test_season_lp_line_uses_ladder_value_across_promotion(store, tmp_path):
    server, _, _ = make(store, tmp_path)
    async with Client(server) as c:
        await _register(c)
        _save(store, _mixed(12))
        entry = lambda tier, rank, lp: [{"queueType": "RANKED_SOLO_5x5", "tier": tier,  # noqa: E731
                                         "rank": rank, "leaguePoints": lp}]
        now = datetime.now(timezone.utc)
        store.add_rank_snapshot(entry("GOLD", "I", 95), (now + timedelta(days=1)).isoformat())
        store.add_rank_snapshot(entry("PLATINUM", "IV", 12), (now + timedelta(days=2)).isoformat())
        r = await c.call_tool("open_dashboard", {"view": "season", "open_browser": False})
    html = _html(r.structured_content)
    assert "래더 점수(LP 환산)" in html
    assert ">1612.0<" in html and ">1595.0<" in html  # 축 라벨에 환산값
    assert "PLATINUM IV" in html


async def test_same_second_reports_do_not_collide(store, tmp_path):
    server, _, reports = make(store, tmp_path)
    async with Client(server) as c:
        await _register(c)
        a = await c.call_tool("open_dashboard", {"open_browser": False})
        b = await c.call_tool("open_dashboard", {"open_browser": False})
    pa, pb = a.structured_content["path"], b.structured_content["path"]
    assert pa != pb and len(list(reports.glob("overview-*.html"))) == 2
    assert pb.endswith("-2.html")


async def test_opener_runs_off_event_loop_thread(store, tmp_path):
    import threading

    seen = []

    def opener(url):
        seen.append(threading.current_thread())
        return True

    server, _, _ = make(store, tmp_path, opener=opener)
    async with Client(server) as c:
        await _register(c)
        r = await c.call_tool("open_dashboard", {})
    assert r.structured_content["opened"] is True
    assert seen and seen[0] is not threading.main_thread()
