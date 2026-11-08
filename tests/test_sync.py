import copy
from datetime import datetime

import pytest

from conftest import PUUID_ME, load_fixture
from lol_coach_mcp.store import Store
from lol_coach_mcp.sync import sync_matches


class FakeClient:
    def __init__(self, ids):
        self.ids = list(ids)
        self.id_calls = []
        self.match_calls = []
        self.timeline_calls = []
        self._base = load_fixture("match_normal")
        self._tl = load_fixture("timeline_normal")

    async def get_match_ids(self, puuid, *, queue=None, start=0, count=100, start_time=None):
        self.id_calls.append(start)
        return self.ids[start : start + min(count, 100)]

    async def get_match(self, match_id):
        self.match_calls.append(match_id)
        m = copy.deepcopy(self._base)
        m["metadata"]["matchId"] = match_id
        m["info"]["gameCreation"] = 1_700_000_000_000 + int(match_id.split("_")[1])
        return m

    async def get_timeline(self, match_id):
        self.timeline_calls.append(match_id)
        return copy.deepcopy(self._tl)


def make_ids(n):
    # newest first
    return [f"KR_{i}" for i in range(n, 0, -1)]


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "t.db")
    yield s
    s.close()


async def test_first_sync_stores_up_to_limit(store):
    c = FakeClient(make_ids(250))
    r = await sync_matches(c, store, PUUID_ME, limit=100)
    assert r.new_matches == 100
    assert r.stopped_reason == "limit_reached"
    assert len(c.match_calls) == 100
    assert r.total_stored == 100
    assert c.id_calls == [0]


async def test_incremental_stops_at_known(store):
    ids = make_ids(35)
    await sync_matches(FakeClient(ids[5:]), store, PUUID_ME)
    c = FakeClient(ids)
    r = await sync_matches(c, store, PUUID_ME)
    assert r.new_matches == 5
    assert r.stopped_reason == "caught_up"
    assert len(c.match_calls) == 5
    assert r.total_stored == 35


async def test_full_sync_reads_all_pages(store):
    c = FakeClient(make_ids(250))
    r = await sync_matches(c, store, PUUID_ME, full=True, limit=10)
    assert r.new_matches == 250
    assert r.stopped_reason == "no_more"
    assert c.id_calls == [0, 100, 200]


async def test_timeline_optional(store):
    c = FakeClient(make_ids(3))
    await sync_matches(c, store, PUUID_ME, with_timeline=False)
    assert c.timeline_calls == []
    c2 = FakeClient(make_ids(5))
    await sync_matches(c2, store, PUUID_ME)
    assert len(c2.timeline_calls) == 2


async def test_progress_called(store):
    calls = []

    async def cb(done, total, msg):
        calls.append((done, total, msg))

    await sync_matches(FakeClient(make_ids(7)), store, PUUID_ME, progress=cb)
    assert calls[-1][0] == calls[-1][1] == 7
    assert "7/7" in calls[-1][2]


async def test_sets_last_sync_at(store):
    await sync_matches(FakeClient(make_ids(2)), store, PUUID_ME)
    assert datetime.fromisoformat(store.get_meta("last_sync_at")).tzinfo is not None


async def test_not_found_match_skipped(store):
    from lol_coach_mcp.riot.errors import RiotNotFound

    class C(FakeClient):
        async def get_match(self, match_id):
            if match_id == "KR_2":
                raise RiotNotFound("gone")
            return await super().get_match(match_id)

    r = await sync_matches(C(make_ids(3)), store, PUUID_ME)
    assert r.new_matches == 2
    assert not store.has_match("KR_2")


async def test_midsync_failure_leaves_no_gap(store):
    from lol_coach_mcp.riot.errors import RiotServerError

    ids = make_ids(10)

    class Flaky(FakeClient):
        async def get_match(self, match_id):
            if len(self.match_calls) == 4:
                raise RiotServerError("boom")
            return await super().get_match(match_id)

    with pytest.raises(RiotServerError):
        await sync_matches(Flaky(ids), store, PUUID_ME)
    r = await sync_matches(FakeClient(ids), store, PUUID_ME)
    assert all(store.has_match(m) for m in ids)
    assert r.total_stored == 10


async def test_timeline_error_does_not_store_match(store):
    from lol_coach_mcp.riot.errors import RiotServerError

    class C(FakeClient):
        async def get_timeline(self, match_id):
            raise RiotServerError("boom")

    with pytest.raises(RiotServerError):
        await sync_matches(C(make_ids(2)), store, PUUID_ME)
    assert not store.has_match("KR_1")
    assert not store.has_match("KR_2")


async def test_full_skips_known_and_fetches_rest(store):
    ids = make_ids(250)
    await sync_matches(FakeClient(ids), store, PUUID_ME, limit=100)
    c = FakeClient(ids)
    r = await sync_matches(c, store, PUUID_ME, full=True)
    assert r.new_matches == 150
    assert r.stopped_reason == "no_more"
    assert len(c.match_calls) == 150
    assert r.total_stored == 250
