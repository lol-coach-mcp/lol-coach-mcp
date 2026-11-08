import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from mcp.types import CallToolResult, TextContent

from lol_coach_mcp.opgg import (
    CHAMPION_ANALYSIS_FIELDS,
    OpggClient,
    opgg_champion_key,
    opgg_position,
)
from lol_coach_mcp.opgg.parser import parse_opgg
from lol_coach_mcp.store import Store

try:
    _ExceptionGroup = ExceptionGroup
except NameError:  # Python 3.10: anyio 의존성인 backport 사용
    from exceptiongroup import ExceptionGroup as _ExceptionGroup

OPGG_DIR = Path(__file__).parent / "fixtures" / "opgg"
T0 = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)


def _sample(name: str) -> str:
    return (OPGG_DIR / f"{name}.txt").read_text(encoding="utf-8")


class FakeOpgg:
    """connect 인자로 주입하는 가짜 OP.GG MCP 접속."""

    def __init__(self, responses=None, *, connect_error=None, call_error=None,
                 is_error=False, delay=0.0):
        self.responses = responses or {}
        self.connect_error = connect_error
        self.call_error = call_error
        self.is_error = is_error
        self.delay = delay
        self.calls: list[tuple[str, dict]] = []
        self.connects = 0

    def __call__(self):
        self.connects += 1
        if self.connect_error is not None:
            raise self.connect_error
        return _FakeSession(self)


class _FakeSession:
    def __init__(self, fake: FakeOpgg) -> None:
        self.fake = fake

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def call_tool(self, name, arguments=None):
        self.fake.calls.append((name, arguments))
        if self.fake.delay:
            await asyncio.sleep(self.fake.delay)
        if self.fake.call_error is not None:
            raise self.fake.call_error
        text = self.fake.responses.get(name, "")
        return CallToolResult(
            content=[TextContent(type="text", text=text)], is_error=self.fake.is_error
        )


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


ALL = {
    "lol_get_champion_analysis": _sample("champion_analysis_kaisa_adc_gold"),
    "lol_get_lane_matchup_guide": _sample("lane_matchup_kaisa_ezreal"),
    "lol_list_lane_meta_champions": _sample("lane_meta_adc"),
    "lol_list_champions": _sample("champions_list"),
}


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "coach.db")
    yield s
    s.close()


@pytest.fixture
def clock():
    return Clock(T0)


async def test_champion_analysis_args_and_result(store, clock):
    fake = FakeOpgg(ALL)
    c = OpggClient(store, connect=fake, now=clock)
    got = await c.champion_analysis("KAISA", "adc", "GOLD")
    assert got["summary"]["average_stats"]["win_rate"] == 0.49
    assert got["strong_counters"][0]["champion_name"] == "이즈리얼"
    name, args = fake.calls[0]
    assert name == "lol_get_champion_analysis"
    assert args == {
        "game_mode": "ranked", "champion": "KAISA", "position": "adc", "tier": "gold",
        "lang": "ko_KR", "desired_output_fields": list(CHAMPION_ANALYSIS_FIELDS),
    }


async def test_champion_analysis_without_tier_omits_param(store, clock):
    fake = FakeOpgg(ALL)
    await OpggClient(store, connect=fake, now=clock).champion_analysis("KAISA", "adc", None)
    assert "tier" not in fake.calls[0][1]


async def test_cache_hit_and_ttl_expiry(store, clock):
    fake = FakeOpgg(ALL)
    c = OpggClient(store, connect=fake, now=clock)
    first = await c.champion_analysis("KAISA", "adc", "gold")
    clock.now = T0 + timedelta(hours=23, minutes=59)
    assert await c.champion_analysis("KAISA", "adc", "gold") == first
    assert fake.connects == 1
    # 캐시는 저장소에 남아 새 클라이언트도 재사용
    assert await OpggClient(store, connect=fake, now=clock).champion_analysis(
        "KAISA", "adc", "gold") == first
    assert fake.connects == 1
    # 다른 인자는 별도 캐시 키
    await c.champion_analysis("KAISA", "adc", None)
    assert fake.connects == 2
    clock.now = T0 + timedelta(hours=24, minutes=1)
    await c.champion_analysis("KAISA", "adc", "gold")
    assert fake.connects == 3


@pytest.mark.parametrize("fake", [
    FakeOpgg(ALL, connect_error=OSError("connection refused")),
    FakeOpgg(ALL, call_error=RuntimeError("Unknown champion provided.")),
    # 실제 mcp Client는 접속 실패를 anyio TaskGroup의 ExceptionGroup으로 감싸 던짐
    FakeOpgg(ALL, call_error=_ExceptionGroup("tg", [ConnectionError("x")])),
    FakeOpgg(ALL, is_error=True),
    FakeOpgg({"lol_get_champion_analysis": "class R: a\n\nR(1, 2)"}),
])
async def test_failures_return_none_and_are_not_cached(store, clock, fake):
    c = OpggClient(store, connect=fake, now=clock)
    assert await c.champion_analysis("KAISA", "adc", "gold") is None
    good = FakeOpgg(ALL)
    c2 = OpggClient(store, connect=good, now=clock)
    assert await c2.champion_analysis("KAISA", "adc", "gold") is not None
    assert good.connects == 1


async def test_timeout_returns_none(store, clock):
    fake = FakeOpgg(ALL, delay=5.0)
    c = OpggClient(store, connect=fake, now=clock, timeout=0.01)
    assert await c.champions() is None


async def test_lane_matchup_extracts_needed_keys(store, clock):
    fake = FakeOpgg(ALL)
    got = await OpggClient(store, connect=fake, now=clock).lane_matchup("adc", "KAISA", "EZREAL")
    assert fake.calls[0] == ("lol_get_lane_matchup_guide", {
        "position": "adc", "my_champion": "KAISA", "opponent_champion": "EZREAL",
        "lang": "ko_KR",
    })
    assert got["my_champion"] == "카이사" and got["opponent_champion"] == "이즈리얼"
    assert got["average_stats"]["play"] == 190863
    assert got["matchup"] == {"play": 13613, "win": 7467, "win_rate": 0.5485}
    assert got["opponent_champion_tip"].startswith("신비한 화살")
    assert got["lane_advantage_champion"] == "EVEN"
    assert got["core_items"][0]["ids_names"] == ["크라켄 학살자", "구인수의 격노검", "경계"]
    assert len(got["core_items"]) <= 3
    assert got["runes"][0]["primary_page_name"] == "정밀"
    assert "trends" not in got and "skills" not in got


async def test_lane_matchup_missing_opponent_row(store, clock):
    raw = parse_opgg(ALL["lol_get_lane_matchup_guide"])
    raw["data"]["counters"] = []
    fake = FakeOpgg({"lol_get_lane_matchup_guide": json.dumps(raw, ensure_ascii=False)})
    got = await OpggClient(store, connect=fake, now=clock).lane_matchup("adc", "KAISA", "EZREAL")
    assert got["matchup"] is None


async def test_lane_meta_and_champions(store, clock):
    fake = FakeOpgg(ALL)
    c = OpggClient(store, connect=fake, now=clock)
    meta = await c.lane_meta("adc")
    assert meta[0]["champion"] == "자야" and len(meta) == 37
    assert fake.calls[0] == ("lol_list_lane_meta_champions", {
        "position": "adc", "lang": "ko_KR",
        "desired_output_fields": [
            "data.positions.adc[].{champion,win_rate,pick_rate,ban_rate,kda,tier,rank,play}"
        ],
    })
    champs = await c.champions()
    assert {"champion_id": 145, "key": "Kaisa", "name": "카이사"} in champs
    assert fake.calls[1][1]["desired_output_fields"] == ["data.champions[].{champion_id,key,name}"]


async def test_invalid_position_raises(store, clock):
    c = OpggClient(store, connect=FakeOpgg(ALL), now=clock)
    with pytest.raises(ValueError):
        await c.champion_analysis("KAISA", "BOTTOM", None)
    with pytest.raises(ValueError):
        await c.lane_meta("bot")


def test_opgg_position():
    assert [opgg_position(p) for p in ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY")] == [
        "top", "jungle", "mid", "adc", "support"]
    assert opgg_position("") is None
    assert opgg_position(None) is None
    assert opgg_position("Invalid") is None


def test_opgg_champion_key():
    champs = parse_opgg(_sample("champions_list"))["data"]["champions"]
    assert opgg_champion_key("Kaisa", champs) == "KAISA"
    assert opgg_champion_key("MonkeyKing", champs) == "MONKEYKING"
    assert opgg_champion_key("Nunu", champs) == "NUNU"
    # Match-V5 일부 데이터의 대소문자 차이(FiddleSticks)도 키 기준으로 정규화
    assert opgg_champion_key("FiddleSticks", champs) == "FIDDLESTICKS"
    assert opgg_champion_key("TwistedFate", champs) == "TWISTEDFATE"
    # 목록에 없거나 목록을 못 받은 경우: 영숫자만 남겨 대문자
    assert opgg_champion_key("New Champ'X", champs) == "NEWCHAMPX"
    assert opgg_champion_key("Kaisa", None) == "KAISA"


@pytest.mark.live
async def test_live_opgg_champion_analysis(tmp_path):
    s = Store(tmp_path / "live.db")
    try:
        c = OpggClient(s)
        got = await c.champion_analysis("KAISA", "adc", "gold")
        assert got is not None
        assert 0 < got["summary"]["average_stats"]["win_rate"] < 1
        assert got["core_items"]["ids"]
        assert got["strong_counters"] and got["weak_counters"]
    finally:
        s.close()


async def test_unexpected_payload_returns_none_and_warns(store, clock, caplog):
    fake = FakeOpgg({"lol_get_champion_analysis": '{"error": "x"}'})
    c = OpggClient(store, connect=fake, now=clock)
    with caplog.at_level("WARNING", logger="lol_coach_mcp.opgg.client"):
        assert await c.champion_analysis("KAISA", "adc", None) is None
    msgs = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any("lol_get_champion_analysis" in m and "data" in m for m in msgs)


async def test_parse_error_logged_with_message(store, clock, caplog):
    fake = FakeOpgg({"lol_list_champions": "class R: a\n\nR(1, 2)"})
    with caplog.at_level("WARNING", logger="lol_coach_mcp.opgg.client"):
        assert await OpggClient(store, connect=fake, now=clock).champions() is None
    assert any("필드 수" in r.getMessage() for r in caplog.records)


async def test_corrupt_cache_row_is_refetched(store, clock):
    fake = FakeOpgg(ALL)
    c = OpggClient(store, connect=fake, now=clock)
    await c.champions()
    key = next(r[0] for r in store._conn.execute("SELECT key FROM opgg_cache"))
    store.put_opgg_cache(key, "{broken", T0.isoformat())
    champs = await c.champions()
    assert champs and fake.connects == 2


async def test_empty_result_not_cached(store, clock):
    empty_meta = (
        "class LolListLaneMetaChampions: data\nclass Data: positions\n"
        "class Positions: adc\n\nLolListLaneMetaChampions(Data(Positions([])))"
    )
    fake = FakeOpgg({"lol_list_lane_meta_champions": empty_meta})
    c = OpggClient(store, connect=fake, now=clock)
    assert await c.lane_meta("adc") == []
    assert await c.lane_meta("adc") == []
    assert fake.connects == 2


def test_champion_key_exceptions_apply_to_both_branches(monkeypatch):
    from lol_coach_mcp.opgg import client as client_mod

    monkeypatch.setattr(client_mod, "CHAMPION_KEY_EXCEPTIONS",
                        {"KAISA": "KAI_SA", "NEWCHAMPX": "NEW_CHAMP_X"})
    champs = parse_opgg(_sample("champions_list"))["data"]["champions"]
    assert opgg_champion_key("Kaisa", champs) == "KAI_SA"          # 목록 매칭 경로
    assert opgg_champion_key("New Champ'X", champs) == "NEW_CHAMP_X"  # fallback 경로
    assert opgg_champion_key("kaisa", None) == "KAI_SA"
