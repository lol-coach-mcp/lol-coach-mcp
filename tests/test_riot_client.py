from pathlib import Path
from urllib.parse import quote

import httpx
import pytest
import respx

from lol_coach_mcp.config import Settings, load_settings
from lol_coach_mcp.ratelimit import HostLimiters, RateLimiter
from lol_coach_mcp.riot.client import RiotClient
from lol_coach_mcp.riot.errors import (
    RiotAuthError,
    RiotNotFound,
    RiotRateLimited,
    RiotServerError,
)

ASIA = "https://asia.api.riotgames.com"
KR = "https://kr.api.riotgames.com"


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FakeHostLimiters(HostLimiters):
    def __init__(self, default_limits, ft: FakeTime) -> None:
        super().__init__(default_limits)
        self._ft = ft

    def for_host(self, host: str) -> RateLimiter:
        if host not in self._limiters:
            self._limiters[host] = RateLimiter(
                self._default, clock=self._ft.clock, sleep=self._ft.sleep
            )
        return self._limiters[host]


@pytest.fixture
def settings() -> Settings:
    return Settings(
        riot_api_key="RGAPI-test",
        data_dir=Path("."),
        user_agent="lol-coach-mcp/0.2.0",
    )


@pytest.fixture
def ft() -> FakeTime:
    return FakeTime()


@pytest.fixture
async def client(settings, ft):
    c = RiotClient(
        settings,
        limiters=FakeHostLimiters([(20, 1), (100, 120)], ft),
        sleep=ft.sleep,
    )
    yield c
    await c.aclose()


@respx.mock
async def test_sends_token_and_user_agent(client):
    route = respx.get(f"{ASIA}/lol/match/v5/matches/KR_1").respond(json={"ok": 1})
    await client.get_match("KR_1")
    req = route.calls[0].request
    assert req.headers["X-Riot-Token"] == "RGAPI-test"
    assert req.headers["User-Agent"] == "lol-coach-mcp/0.2.0"


@respx.mock
async def test_encodes_korean_riot_id(client):
    name, tag = "서석하는댄스머신밤만되면센스머신", "빨고싶어"
    route = respx.route(host="asia.api.riotgames.com").respond(json={"puuid": "p"})
    await client.get_account_by_riot_id(name, tag)
    url = route.calls[0].request.url
    assert url.host == "asia.api.riotgames.com"
    expected = (
        f"/riot/account/v1/accounts/by-riot-id/{quote(name, safe='')}/{quote(tag, safe='')}"
    )
    assert url.raw_path.decode() == expected
    assert "%EC%84%9C" in expected


@respx.mock
async def test_encodes_space_in_name(client):
    route = respx.route(host="asia.api.riotgames.com").respond(json={"puuid": "p"})
    await client.get_account_by_riot_id("Hide on bush", "KR1")
    assert route.calls[0].request.url.raw_path.decode().endswith("/Hide%20on%20bush/KR1")


@respx.mock
async def test_routes_league_to_kr_and_match_to_asia(client):
    league = respx.get(f"{KR}/lol/league/v4/entries/by-puuid/PU").respond(json=[])
    ids = respx.get(f"{ASIA}/lol/match/v5/matches/by-puuid/PU/ids").respond(json=["KR_1"])
    assert await client.get_league_entries("PU") == []
    assert await client.get_match_ids("PU") == ["KR_1"]
    assert league.called and ids.called


@respx.mock
async def test_retries_after_429_using_retry_after(client, ft):
    route = respx.get(f"{ASIA}/lol/match/v5/matches/KR_1").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "2"}),
            httpx.Response(200, json={"ok": 1}),
        ]
    )
    assert await client.get_match("KR_1") == {"ok": 1}
    assert route.call_count == 2
    assert any(s >= 2 for s in ft.sleeps)


@respx.mock
async def test_429_exhausted_raises(client):
    respx.get(f"{ASIA}/lol/match/v5/matches/KR_1").respond(
        429, headers={"Retry-After": "1"}
    )
    with pytest.raises(RiotRateLimited):
        await client.get_match("KR_1")


@respx.mock
async def test_401_403_raise_auth_error_without_retry(client):
    route = respx.get(f"{ASIA}/lol/match/v5/matches/KR_1").respond(403)
    with pytest.raises(RiotAuthError) as ei:
        await client.get_match("KR_1")
    assert route.call_count == 1
    assert "Riot API 키가 만료되었거나 올바르지 않음. 개발자 포털에서 키를 확인해야 함." == str(
        ei.value
    )


@respx.mock
async def test_404_raises_not_found(client):
    respx.get(f"{ASIA}/lol/match/v5/matches/KR_1").respond(404)
    with pytest.raises(RiotNotFound):
        await client.get_match("KR_1")


@respx.mock
async def test_5xx_retries_then_raises(client):
    route = respx.get(f"{ASIA}/lol/match/v5/matches/KR_1").respond(503)
    with pytest.raises(RiotServerError):
        await client.get_match("KR_1")
    assert route.call_count == 4


@respx.mock
async def test_updates_limits_from_headers(client):
    respx.get(f"{ASIA}/lol/match/v5/matches/KR_1").respond(
        json={}, headers={"X-App-Rate-Limit": "50:10"}
    )
    await client.get_match("KR_1")
    assert (50, 10) in client._limiters.for_host(ASIA)._limits


@respx.mock
async def test_malformed_limit_header_ignored(client):
    respx.get(f"{ASIA}/lol/match/v5/matches/KR_1").respond(
        json={}, headers={"X-App-Rate-Limit": "garbage"}
    )
    assert await client.get_match("KR_1") == {}


@respx.mock
async def test_match_ids_count_clamped(client):
    route = respx.get(f"{ASIA}/lol/match/v5/matches/by-puuid/PU/ids").respond(json=[])
    await client.get_match_ids("PU", count=500)
    assert route.calls[0].request.url.params["count"] == "100"


@pytest.mark.live
async def test_live_account_lookup():
    s = load_settings()
    async with RiotClient(s) as c:
        acc = await c.get_account_by_riot_id("위험한자식김준현", "KR1")
    assert "puuid" in acc


@respx.mock
async def test_429_exhausted_pauses_limiter_before_raising(client):
    respx.get(f"{ASIA}/lol/match/v5/matches/KR_1").respond(429, headers={"Retry-After": "7"})
    with pytest.raises(RiotRateLimited):
        await client.get_match("KR_1")
    assert client._limiters.for_host(ASIA)._paused_until >= 7


@respx.mock
async def test_retry_after_clamped_to_300(client, ft):
    respx.get(f"{ASIA}/lol/match/v5/matches/KR_1").respond(429, headers={"Retry-After": "99999"})
    with pytest.raises(RiotRateLimited):
        await client.get_match("KR_1")
    assert client._limiters.for_host(ASIA)._paused_until <= ft.now + 300
    assert max(ft.sleeps) <= 300


@respx.mock
async def test_negative_retry_after_clamped_to_zero(client, ft):
    respx.get(f"{ASIA}/lol/match/v5/matches/KR_1").respond(429, headers={"Retry-After": "-50"})
    with pytest.raises(RiotRateLimited):
        await client.get_match("KR_1")
    assert client._limiters.for_host(ASIA)._paused_until <= ft.now + 0.001


@respx.mock
async def test_league_entries_page_path_and_params(client):
    route = respx.get(f"{KR}/lol/league/v4/entries/RANKED_SOLO_5x5/GOLD/II").respond(
        json=[{"puuid": "p1"}])
    got = await client.get_league_entries_page("RANKED_SOLO_5x5", "GOLD", "II", 3)
    assert got == [{"puuid": "p1"}]
    assert route.calls[0].request.url.params["page"] == "3"


@respx.mock
@pytest.mark.parametrize(
    "tier,seg", [("MASTER", "masterleagues"), ("GRANDMASTER", "grandmasterleagues"),
                 ("CHALLENGER", "challengerleagues")])
async def test_apex_league_path(client, tier, seg):
    route = respx.get(f"{KR}/lol/league/v4/{seg}/by-queue/RANKED_SOLO_5x5").respond(
        json={"entries": []})
    assert await client.get_apex_league(tier, "RANKED_SOLO_5x5") == {"entries": []}
    assert route.called


async def test_apex_league_rejects_non_apex_tier(client):
    with pytest.raises(ValueError):
        await client.get_apex_league("GOLD", "RANKED_SOLO_5x5")
