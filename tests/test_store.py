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
    _fill_memory(store)
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
    _assert_memory_empty(store)
    _fill_memory(store)
    store.forget_all(include_matches=True)
    _assert_memory_empty(store)
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


BASE_ROW = {"tier": "GOLD", "position": "MIDDLE", "metric": "kda", "mean": 2.8, "std": 1.4,
            "n": 500, "patch": "16.22", "collected_at": "2026-11-01", "source": "배포 기준선"}


def test_baselines_roundtrip(store):
    assert store.get_baseline("GOLD", "MIDDLE", "kda") is None
    store.upsert_baselines([BASE_ROW, {**BASE_ROW, "champion": "Ahri", "mean": 3.1, "n": 40}])
    got = store.get_baseline("GOLD", "MIDDLE", "kda")
    assert got == {"mean": 2.8, "std": 1.4, "n": 500, "patch": "16.22",
                   "collected_at": "2026-11-01", "source": "배포 기준선"}
    assert store.get_baseline("GOLD", "MIDDLE", "kda", champion="Ahri")["mean"] == 3.1
    assert store.get_baseline("GOLD", "TOP", "kda") is None
    store.upsert_baselines([{**BASE_ROW, "mean": 2.9, "std": None}])
    got = store.get_baseline("GOLD", "MIDDLE", "kda")
    assert got["mean"] == 2.9 and got["std"] is None
    store.forget_all(include_matches=True)
    assert store.get_baseline("GOLD", "MIDDLE", "kda") is not None


def test_migrates_v1_db_in_place(tmp_path):
    import sqlite3

    from lol_coach_mcp import store as store_mod

    path = tmp_path / "old.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(store_mod._SCHEMA_V1)
    conn.execute("PRAGMA user_version = 1")
    conn.execute(
        "INSERT INTO profile (id, puuid, game_name, tag_line, tone, detail_level, registered_at) "
        "VALUES (1, 'p1', 'a', 'b', '격려', '보통', 't')"
    )
    conn.commit()
    assert conn.execute(
        "SELECT name FROM sqlite_master WHERE name = 'baselines'").fetchone() is None
    conn.close()

    s = Store(path)
    try:
        assert s.get_profile().puuid == "p1"
        s.upsert_baselines([BASE_ROW])
        assert s.get_baseline("GOLD", "MIDDLE", "kda")["n"] == 500
    finally:
        s.close()
    conn = sqlite3.connect(str(path))
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == store_mod.SCHEMA_VERSION == 4
    finally:
        conn.close()


def test_migrates_v2_db_in_place_adds_caches(tmp_path):
    import sqlite3

    from lol_coach_mcp import store as store_mod

    path = tmp_path / "v2.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(store_mod._SCHEMA_V1)
    conn.executescript(store_mod._SCHEMA_V2)
    conn.execute("PRAGMA user_version = 2")
    conn.execute(
        "INSERT INTO baselines (tier, position, champion, metric, mean) "
        "VALUES ('GOLD', 'MIDDLE', '', 'kda', 2.5)"
    )
    conn.commit()
    assert conn.execute(
        "SELECT name FROM sqlite_master WHERE name = 'opgg_cache'").fetchone() is None
    conn.close()

    s = Store(path)
    try:
        assert s.get_baseline("GOLD", "MIDDLE", "kda")["mean"] == 2.5
        s.put_opgg_cache("k", '{"a": 1}', "2026-11-08T00:00:00+00:00")
        assert s.get_opgg_cache("k") == ('{"a": 1}', "2026-11-08T00:00:00+00:00")
        s.put_ddragon_cache("champion:16.22.1", "{}", "16.22.1", "t")
        assert s.get_ddragon_cache("champion:16.22.1") == ("{}", "16.22.1", "t")
    finally:
        s.close()
    conn = sqlite3.connect(str(path))
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
    finally:
        conn.close()


def test_caches_roundtrip_overwrite_and_prune(store):
    assert store.get_opgg_cache("missing") is None
    store.put_opgg_cache("k", "1", "t1")
    store.put_opgg_cache("k", "2", "t2")
    assert store.get_opgg_cache("k") == ("2", "t2")
    store.put_ddragon_cache("versions", "[]", "16.23.1", "t")
    store.put_ddragon_cache("champion:16.22.1", "{}", "16.22.1", "t")
    store.put_ddragon_cache("champion:16.23.1", "{}", "16.23.1", "t")
    store.prune_ddragon_cache("16.23.1")
    assert store.get_ddragon_cache("champion:16.22.1") is None
    assert store.get_ddragon_cache("champion:16.23.1") is not None
    assert store.get_ddragon_cache("versions") is not None
    # 캐시는 개인 데이터가 아니므로 forget_all 이후에도 유지
    store.forget_all(include_matches=True)
    assert store.get_opgg_cache("k") == ("2", "t2")


FINDING = {
    "created_at": "2026-11-08T10:00:00+09:00", "kind": "약점", "metric": "deaths",
    "my_value": 5.1, "ref_value": 3.2, "ref_source": "GOLD BOTTOM 기준선", "games": 20,
    "period_desc": "최근 20판", "season_major": "16", "position": "BOTTOM",
}


def _fill_memory(store):
    store.add_findings([FINDING])
    store.add_goal({
        "created_at": "2026-11-08T10:00:00+09:00", "metric": "deaths", "direction": "down",
        "target": 3.5, "baseline_value": 5.1, "start_after_ms": 1000, "games_to_track": 10,
        "position": "BOTTOM",
    })
    store.add_journal("2026-11-08T10:00:00+09:00", None, "오늘 갱을 많이 당함")


def _assert_memory_empty(store):
    assert store.list_findings(10) == []
    assert store.list_goals() == []
    assert store.list_journal(10) == []


def test_migrates_v3_db_in_place_adds_memory_tables(tmp_path):
    import sqlite3

    from lol_coach_mcp import store as store_mod

    path = tmp_path / "v3.db"
    conn = sqlite3.connect(str(path))
    for script in (store_mod._SCHEMA_V1, store_mod._SCHEMA_V2, store_mod._SCHEMA_V3):
        conn.executescript(script)
    conn.execute("PRAGMA user_version = 3")
    conn.execute(
        "INSERT INTO profile (id, puuid, game_name, tag_line, tone, detail_level, registered_at) "
        "VALUES (1, 'p1', 'a', 'b', '격려', '보통', 't')"
    )
    conn.commit()
    assert conn.execute(
        "SELECT name FROM sqlite_master WHERE name = 'coach_findings'").fetchone() is None
    conn.close()

    s = Store(path)
    try:
        assert s.get_profile().puuid == "p1"
        _fill_memory(s)
        assert len(s.list_findings(10)) == 1
    finally:
        s.close()
    conn = sqlite3.connect(str(path))
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"coach_findings", "goals", "journal"} <= names
    finally:
        conn.close()


def test_findings_dedupe_same_kst_date_kind_metric_and_order(store):
    assert store.add_findings([FINDING]) == 1
    later_same_day = {**FINDING, "created_at": "2026-11-08T23:30:00+09:00", "my_value": 6.0}
    other_metric = {**FINDING, "metric": "kda", "kind": "강점"}
    same_metric_other_kind = {**FINDING, "kind": "강점"}
    next_day = {**FINDING, "created_at": "2026-11-09T00:10:00+09:00"}
    assert store.add_findings([later_same_day, other_metric, same_metric_other_kind]) == 2
    assert store.add_findings([next_day]) == 1
    got = store.list_findings(10)
    assert len(got) == 4
    assert got[0]["created_at"] == "2026-11-09T00:10:00+09:00"
    assert got[-1]["my_value"] == 5.1
    assert len(store.list_findings(2)) == 2
    assert {"id", "kind", "metric", "ref_source", "games", "season_major", "position"} <= set(got[0])


def test_findings_dedupe_within_one_batch(store):
    assert store.add_findings([FINDING, dict(FINDING)]) == 1


GOAL = {
    "created_at": "2026-11-08T10:00:00+09:00", "metric": "deaths", "direction": "down",
    "target": 3.5, "baseline_value": 5.1, "start_after_ms": 1000, "games_to_track": 10,
    "position": "BOTTOM",
}


def test_goals_crud(store):
    gid = store.add_goal(GOAL)
    g2 = store.add_goal({**GOAL, "metric": "cs_per_min", "direction": "up", "target": 7.5})
    got = store.get_goal(gid)
    assert got["status"] == "진행 중" and got["closed_at"] is None and got["target"] == 3.5
    assert store.get_goal(999) is None
    assert [g["id"] for g in store.list_goals()] == [g2, gid]
    store.update_goal(gid, status="달성", closed_at="2026-11-09T00:00:00+09:00")
    assert store.get_goal(gid)["status"] == "달성"
    assert [g["id"] for g in store.list_goals(status="진행 중")] == [g2]
    assert [g["id"] for g in store.list_goals(status="달성")] == [gid]


def test_goal_update_rejects_unknown_field_and_bad_status(store):
    gid = store.add_goal(GOAL)
    with pytest.raises(ValueError):
        store.update_goal(gid, bogus=1)
    with pytest.raises(ValueError):
        store.update_goal(gid, status="이상함")


def test_journal_add_list_newest_first(store):
    a = store.add_journal("2026-11-08T10:00:00+09:00", "KR_1", "첫 메모")
    b = store.add_journal("2026-11-08T11:00:00+09:00", None, "둘째 메모")
    got = store.list_journal(10)
    assert [j["id"] for j in got] == [b, a]
    assert got[0]["match_id"] is None and got[1]["match_id"] == "KR_1"
    assert [j["text"] for j in store.list_journal(1)] == ["둘째 메모"]
