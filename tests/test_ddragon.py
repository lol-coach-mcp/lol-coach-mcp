from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from lol_coach_mcp.ddragon import DDRAGON_HOST, DataDragon
from lol_coach_mcp.store import Store

T0 = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)
UA = "lol-coach-mcp/test"
VERSIONS = f"{DDRAGON_HOST}/api/versions.json"

CHAMPION_JSON = {
    "type": "champion", "version": "16.22.1",
    "data": {
        "Kaisa": {"id": "Kaisa", "key": "145", "name": "카이사", "tags": ["Marksman"]},
        "MonkeyKing": {"id": "MonkeyKing", "key": "62", "name": "오공",
                       "tags": ["Fighter", "Tank"]},
    },
}
ITEM_JSON = {
    "type": "item", "version": "16.22.1",
    "data": {
        "3006": {"name": "광전사의 군화", "gold": {"total": 1100}, "tags": ["Boots"], "into": ["3172"],
                 "description": "<mainText>긴 설명</mainText>"},
        "6672": {"name": "크라켄 학살자", "gold": {"total": 3000}, "tags": ["Damage"]},
    },
}
RUNES_JSON = [
    {"id": 8000, "key": "Precision", "name": "정밀", "slots": [
        {"runes": [{"id": 8008, "key": "LethalTempo", "name": "치명적 속도", "longDesc": "x"}]},
        {"runes": [{"id": 9103, "key": "LegendBloodline", "name": "전설: 핏빛 길"}]},
    ]},
    {"id": 8300, "key": "Inspiration", "name": "영감", "slots": [
        {"runes": [{"id": 8304, "key": "MagicalFootwear", "name": "마법의 신발"}]},
    ]},
]


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "coach.db")
    yield s
    s.close()


@pytest.fixture
def clock():
    return Clock(T0)


@pytest.fixture
async def http():
    async with httpx.AsyncClient() as c:
        yield c


def _cdn(v, name):
    return f"{DDRAGON_HOST}/cdn/{v}/data/ko_KR/{name}.json"


@respx.mock
async def test_version_cached_with_ttl_and_user_agent(store, http, clock):
    route = respx.get(VERSIONS).respond(json=["16.22.1", "16.21.1"])
    dd = DataDragon(store, http, user_agent=UA, now=clock)
    assert await dd.version() == "16.22.1"
    assert route.calls[0].request.headers["User-Agent"] == UA
    clock.now = T0 + timedelta(hours=23)
    assert await DataDragon(store, http, user_agent=UA, now=clock).version() == "16.22.1"
    assert route.call_count == 1
    clock.now = T0 + timedelta(hours=25)
    route.respond(json=["16.23.1", "16.22.1"])
    assert await dd.version() == "16.23.1"
    assert route.call_count == 2


@respx.mock
async def test_version_falls_back_to_stale_cache_on_network_error(store, http, clock):
    route = respx.get(VERSIONS).respond(json=["16.22.1"])
    dd = DataDragon(store, http, user_agent=UA, now=clock)
    await dd.version()
    clock.now = T0 + timedelta(days=3)
    route.side_effect = httpx.ConnectError("down")
    assert await dd.version() == "16.22.1"


@respx.mock
async def test_version_without_cache_raises_http_error(store, http, clock):
    respx.get(VERSIONS).mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(httpx.HTTPError):
        await DataDragon(store, http, user_agent=UA, now=clock).version()


@respx.mock
async def test_version_http_status_error_raises(store, http, clock):
    respx.get(VERSIONS).respond(503)
    with pytest.raises(httpx.HTTPError):
        await DataDragon(store, http, user_agent=UA, now=clock).version()


@respx.mock
async def test_champions_by_key_cached_per_version(store, http, clock):
    respx.get(VERSIONS).respond(json=["16.22.1"])
    route = respx.get(_cdn("16.22.1", "champion")).respond(json=CHAMPION_JSON)
    dd = DataDragon(store, http, user_agent=UA, now=clock)
    got = await dd.champions()
    assert got == {
        "Kaisa": {"id": 145, "name": "카이사", "tags": ["Marksman"]},
        "MonkeyKing": {"id": 62, "name": "오공", "tags": ["Fighter", "Tank"]},
    }
    assert route.calls[0].request.headers["User-Agent"] == UA
    assert await DataDragon(store, http, user_agent=UA, now=clock).champions() == got
    assert route.call_count == 1


@respx.mock
async def test_items_and_runes(store, http, clock):
    respx.get(VERSIONS).respond(json=["16.22.1"])
    respx.get(_cdn("16.22.1", "item")).respond(json=ITEM_JSON)
    respx.get(_cdn("16.22.1", "runesReforged")).respond(json=RUNES_JSON)
    dd = DataDragon(store, http, user_agent=UA, now=clock)
    items = await dd.items()
    assert items[3006] == {"name": "광전사의 군화", "gold": 1100, "tags": ["Boots"], "into": [3172]}
    assert items[6672]["into"] == []
    assert items[6672]["name"] == "크라켄 학살자"
    runes = await dd.runes()
    assert runes[8000] == {"name": "정밀", "key": "Precision", "tree": "정밀"}
    assert runes[8008] == {"name": "치명적 속도", "key": "LethalTempo", "tree": "정밀"}
    assert runes[8304]["tree"] == "영감"
    # 캐시에서 읽어도 정수 키 유지
    dd2 = DataDragon(store, http, user_agent=UA, now=clock)
    assert await dd2.items() == items
    assert await dd2.runes() == runes


@respx.mock
async def test_new_version_refetches_and_prunes_old(store, http, clock):
    vroute = respx.get(VERSIONS).respond(json=["16.22.1"])
    old = respx.get(_cdn("16.22.1", "champion")).respond(json=CHAMPION_JSON)
    dd = DataDragon(store, http, user_agent=UA, now=clock)
    await dd.champions()
    clock.now = T0 + timedelta(hours=25)
    vroute.respond(json=["16.23.1", "16.22.1"])
    new = respx.get(_cdn("16.23.1", "champion")).respond(json=CHAMPION_JSON)
    await dd.champions()
    assert old.call_count == 1 and new.call_count == 1
    assert store.get_ddragon_cache("champion:16.22.1") is None
    assert store.get_ddragon_cache("champion:16.23.1") is not None


@respx.mock
async def test_cdn_lag_falls_back_to_previous_version_data(store, http, clock):
    vroute = respx.get(VERSIONS).respond(json=["16.22.1"])
    respx.get(_cdn("16.22.1", "champion")).respond(json=CHAMPION_JSON)
    dd = DataDragon(store, http, user_agent=UA, now=clock)
    old = await dd.champions()
    assert dd.used_versions["champion"] == "16.22.1" and not dd.fallbacks
    clock.now = T0 + timedelta(hours=25)
    vroute.respond(json=["16.23.1", "16.22.1"])
    new_route = respx.get(_cdn("16.23.1", "champion")).respond(404)
    assert await dd.version() == "16.23.1"
    assert await dd.champions() == old
    assert dd.used_versions["champion"] == "16.22.1"
    assert "champion" in dd.fallbacks
    # 이전 버전 데이터는 새 버전 데이터 저장 전까지 지우지 않음
    assert store.get_ddragon_cache("champion:16.22.1") is not None
    new_route.respond(json=CHAMPION_JSON)
    await dd.champions()
    assert dd.used_versions["champion"] == "16.23.1" and "champion" not in dd.fallbacks
    assert store.get_ddragon_cache("champion:16.22.1") is None


@respx.mock
async def test_cdn_error_without_previous_data_raises(store, http, clock):
    respx.get(VERSIONS).respond(json=["16.22.1"])
    respx.get(_cdn("16.22.1", "item")).respond(500)
    with pytest.raises(httpx.HTTPError):
        await DataDragon(store, http, user_agent=UA, now=clock).items()


@pytest.mark.parametrize("body", [[], {"x": 1}, [123], "not json"])
@respx.mock
async def test_bad_versions_json(store, http, clock, body):
    route = respx.get(VERSIONS)
    if body == "not json":
        route.respond(text="<html>")
    else:
        route.respond(json=body)
    with pytest.raises(httpx.HTTPError):
        await DataDragon(store, http, user_agent=UA, now=clock).version()


@respx.mock
async def test_bad_versions_json_uses_stale_and_refreshes_fetched_at(store, http, clock):
    route = respx.get(VERSIONS).respond(json=["16.22.1"])
    dd = DataDragon(store, http, user_agent=UA, now=clock)
    await dd.version()
    clock.now = T0 + timedelta(hours=30)
    route.respond(json=[])
    assert await dd.version() == "16.22.1"
    assert route.call_count == 2
    clock.now = T0 + timedelta(hours=31)
    assert await dd.version() == "16.22.1"
    assert route.call_count == 2  # 대체 사용 후 TTL 동안 재요청 안 함
