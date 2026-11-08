from dataclasses import replace

import pytest

from conftest import PUUID_ME, load_fixture
from lol_coach_mcp.store import Profile, Store
from lol_coach_mcp.stats import extract_match_stats


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "sub" / "dir" / "coach.db")
    yield s
    s.close()


def _stats(match_id, game_creation, queue_id=420, is_remake=False):
    base = extract_match_stats(load_fixture("match_normal"), PUUID_ME)
    return replace(
        base,
        match_id=match_id,
        game_creation=game_creation,
        queue_id=queue_id,
        is_remake=is_remake,
    )


def _save(store, stats):
    store.save_match(stats.match_id, {"metadata": {"matchId": stats.match_id}}, stats)


def test_profile_roundtrip_and_delete(store):
    assert store.get_profile() is None
    p = Profile(puuid="p1", game_name="폴", tag_line="KR1", main_position="MIDDLE",
                registered_at="2026-11-08T00:00:00+00:00")
    store.save_profile(p)
    assert store.get_profile() == p
    p2 = replace(p, tone="직설", detail_level="숙련")
    store.save_profile(p2)
    assert store.get_profile() == p2
    store.delete_profile()
    assert store.get_profile() is None


def test_save_and_list_stats_ordering(store):
    for mid, gc in (("KR_1", 200), ("KR_2", 300), ("KR_3", 100)):
        _save(store, _stats(mid, gc))
    got = store.list_my_stats()
    assert [s.match_id for s in got] == ["KR_2", "KR_1", "KR_3"]
    assert got[0] == _stats("KR_2", 300)
    assert isinstance(got[0].teammates, tuple)
    assert [s.match_id for s in store.list_my_stats(limit=2)] == ["KR_2", "KR_1"]
    assert store.has_match("KR_1") and not store.has_match("KR_9")


def test_list_excludes_remakes_by_default(store):
    _save(store, _stats("KR_1", 100))
    _save(store, _stats("KR_R", 200, is_remake=True))
    assert [s.match_id for s in store.list_my_stats()] == ["KR_1"]
    assert [s.match_id for s in store.list_my_stats(include_remakes=True)] == ["KR_R", "KR_1"]


def test_queue_filter(store):
    _save(store, _stats("KR_1", 100, queue_id=420))
    _save(store, _stats("KR_2", 200, queue_id=440))
    assert [s.match_id for s in store.list_my_stats(queue=440)] == ["KR_2"]
    assert {s.match_id for s in store.list_my_stats(queue=None)} == {"KR_1", "KR_2"}
    assert [s.match_id for s in store.list_my_stats()] == ["KR_1"]


def test_match_and_timeline_roundtrip(store):
    match = load_fixture("match_normal")
    tl = load_fixture("timeline_normal")
    stats = extract_match_stats(match, PUUID_ME)
    assert store.get_match(stats.match_id) is None
    assert store.get_timeline(stats.match_id) is None
    store.save_match(stats.match_id, match, stats)
    store.save_timeline(stats.match_id, tl)
    assert store.get_match(stats.match_id) == match
    assert store.get_timeline(stats.match_id) == tl


def test_rank_snapshot_latest(store):
    assert store.latest_rank_snapshot() is None
    store.add_rank_snapshot([{"tier": "GOLD"}], "2026-11-01T00:00:00+00:00")
    store.add_rank_snapshot([{"tier": "PLATINUM"}], "2026-11-08T00:00:00+00:00")
    assert store.latest_rank_snapshot() == (
        "2026-11-08T00:00:00+00:00", [{"tier": "PLATINUM"}]
    )


def test_forget_all_keeps_matches_by_default(store):
    store.save_profile(Profile(puuid="p1", game_name="a", tag_line="b"))
    store.add_rank_snapshot([{"tier": "GOLD"}], "t")
    store.set_meta("last_sync_at", "x")
    assert store.get_meta("last_sync_at") == "x"
    _save(store, _stats("KR_1", 100))
    store.save_timeline("KR_1", {"a": 1})
    store.forget_all()
    assert store.get_profile() is None
    assert store.latest_rank_snapshot() is None
    assert store.get_meta("last_sync_at") is None
    assert store.has_match("KR_1") and len(store.list_my_stats()) == 1
    store.forget_all(include_matches=True)
    assert not store.has_match("KR_1")
    assert store.get_timeline("KR_1") is None
    assert store.list_my_stats() == []


def test_reopen_persists(tmp_path):
    path = tmp_path / "x" / "coach.db"
    s = Store(path)
    p = Profile(puuid="p1", game_name="a", tag_line="b")
    s.save_profile(p)
    s.close()
    s2 = Store(path)
    try:
        assert s2.get_profile() == p
    finally:
        s2.close()
