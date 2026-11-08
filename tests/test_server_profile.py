import copy

import httpx
import pytest
from mcp import Client

from conftest import load_fixture
from lol_coach_mcp.config import Settings
from lol_coach_mcp.riot.errors import RiotAuthError
from lol_coach_mcp.server import AppState, create_server
from lol_coach_mcp.store import Store

PUUID = "puuid-me"


class FakeRiot:
    def __init__(self, mode="ok", n_matches=3):
        self.mode = mode
        self.ids = [f"KR_{i}" for i in range(n_matches, 0, -1)]
        self._base = load_fixture("match_normal")
        self._tl = load_fixture("timeline_normal")
        self.closed = False
        self.puuids = {"다른사람": "puuid-other"}

    async def get_account_by_riot_id(self, game_name, tag_line):
        if self.mode == "403":
            raise RiotAuthError()
        if self.mode == "network":
            raise httpx.ConnectTimeout("timeout")
        return {"puuid": self.puuids.get(game_name, PUUID), "gameName": game_name, "tagLine": tag_line}

    async def get_match_ids(self, puuid, *, queue=None, start=0, count=100, start_time=None):
        return self.ids[start : start + min(count, 100)]

    async def get_match(self, match_id):
        m = copy.deepcopy(self._base)
        m["metadata"]["matchId"] = match_id
        m["info"]["gameCreation"] = 1_700_000_000_000 + int(match_id.split("_")[1])
        return m

    async def get_timeline(self, match_id):
        return copy.deepcopy(self._tl)

    async def aclose(self):
        self.closed = True


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "t.db")
    yield s
    s.close()


def make_server(store, riot=None):
    riot = riot or FakeRiot()
    settings = Settings(riot_api_key="k", data_dir=store_dir(store), user_agent="t")
    return create_server(lambda: AppState(settings=settings, riot=riot, store=store))


def store_dir(store):
    from pathlib import Path

    return Path(".")


RIOT_ID = "서석하는댄스머신밤만되면센스머신#빨고싶어"


async def test_lists_three_tools_with_output_schema(store):
    async with Client(make_server(store)) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
    assert {"my_profile", "get_coach_context", "sync_matches"} <= set(tools)
    for name in ("my_profile", "get_coach_context", "sync_matches"):
        assert tools[name].output_schema


async def test_set_profile_with_korean_riot_id(store):
    async with Client(make_server(store)) as c:
        r = await c.call_tool("my_profile", {"action": "set", "riot_id": RIOT_ID})
    assert not r.is_error
    assert r.structured_content["game_name"] == "서석하는댄스머신밤만되면센스머신"
    assert r.structured_content["tag_line"] == "빨고싶어"
    p = store.get_profile()
    assert p.puuid == PUUID
    assert p.game_name == "서석하는댄스머신밤만되면센스머신"
    assert p.registered_at


async def test_set_without_hash_errors(store):
    async with Client(make_server(store)) as c:
        r = await c.call_tool("my_profile", {"action": "set", "riot_id": "이름만"})
    assert r.is_error
    assert "#" in r.content[0].text


async def test_update_tone_only_keeps_riot_id(store):
    async with Client(make_server(store)) as c:
        await c.call_tool("my_profile", {"action": "set", "riot_id": RIOT_ID})
        r = await c.call_tool("my_profile", {"action": "set", "tone": "직설"})
    assert not r.is_error
    assert r.structured_content["tone"] == "직설"
    p = store.get_profile()
    assert p.tone == "직설"
    assert p.puuid == PUUID
    assert p.tag_line == "빨고싶어"


async def test_forget_clears_profile(store):
    async with Client(make_server(store)) as c:
        await c.call_tool("my_profile", {"action": "set", "riot_id": RIOT_ID})
        r = await c.call_tool("my_profile", {"action": "forget"})
    assert not r.is_error
    assert store.get_profile() is None
    assert r.structured_content["registered"] is False


async def test_context_without_profile_returns_guidance(store):
    async with Client(make_server(store)) as c:
        r = await c.call_tool("get_coach_context", {})
    assert r.is_error
    assert "my_profile" in r.content[0].text


async def test_auth_error_message(store):
    async with Client(make_server(store, FakeRiot("403"))) as c:
        r = await c.call_tool("my_profile", {"action": "set", "riot_id": RIOT_ID})
    assert r.is_error
    assert "API 키" in r.content[0].text


async def test_network_error_message(store):
    async with Client(make_server(store, FakeRiot("network"))) as c:
        r = await c.call_tool("my_profile", {"action": "set", "riot_id": RIOT_ID})
    assert r.is_error
    assert "네트워크" in r.content[0].text


async def test_sync_reports_counts(store):
    async with Client(make_server(store)) as c:
        await c.call_tool("my_profile", {"action": "set", "riot_id": RIOT_ID})
        r = await c.call_tool("sync_matches", {"count": 5})
        ctx = await c.call_tool("get_coach_context", {})
    assert not r.is_error
    assert r.structured_content["new_matches"] == 3
    assert r.structured_content["total_stored"] == 3
    assert ctx.structured_content["stored_matches"] == 3
    assert ctx.structured_content["last_sync_at"]


async def test_sync_without_profile_errors(store):
    async with Client(make_server(store)) as c:
        r = await c.call_tool("sync_matches", {})
    assert r.is_error
    assert "my_profile" in r.content[0].text


async def test_text_content_is_korean_summary(store):
    async with Client(make_server(store)) as c:
        r = await c.call_tool("my_profile", {"action": "set", "riot_id": RIOT_ID})
        r2 = await c.call_tool("get_coach_context", {})
    for res in (r, r2):
        text = res.content[0].text
        assert not text.startswith("{")
        assert "격려" in text


async def test_get_without_profile_is_not_error(store):
    async with Client(make_server(store)) as c:
        r = await c.call_tool("my_profile", {"action": "get"})
    assert not r.is_error
    assert r.structured_content["registered"] is False
    assert "my_profile" in r.content[0].text


@pytest.mark.parametrize("bad", ["a#b#c", "#KR1", "이름#", " # "])
async def test_bad_riot_ids_rejected(store, bad):
    async with Client(make_server(store)) as c:
        r = await c.call_tool("my_profile", {"action": "set", "riot_id": bad})
    assert r.is_error
    assert "#" in r.content[0].text
    assert store.get_profile() is None


async def test_changing_account_clears_previous_data(store):
    async with Client(make_server(store)) as c:
        await c.call_tool("my_profile", {"action": "set", "riot_id": RIOT_ID, "tone": "직설"})
        await c.call_tool("sync_matches", {})
        assert store.get_meta("last_sync_at")
        # 같은 계정 재설정: 데이터 유지
        await c.call_tool("my_profile", {"action": "set", "riot_id": RIOT_ID})
        assert len(store.list_my_stats(queue=None, include_remakes=True)) == 3
        assert store.get_profile().tone == "직설"
        # 다른 계정: 정리 + 기본값
        r = await c.call_tool("my_profile", {"action": "set", "riot_id": "다른사람#KR1"})
    assert not r.is_error
    assert "삭제됨" in r.content[0].text
    assert store.list_my_stats(queue=None, include_remakes=True) == []
    assert store.get_meta("last_sync_at") is None
    assert store.get_profile().puuid == "puuid-other"
    assert store.get_profile().tone == "격려"
