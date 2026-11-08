import json
import logging
import re

import pytest
from mcp import Client

from lol_coach_mcp import server as server_mod
from lol_coach_mcp.baseline import loader
from lol_coach_mcp.baseline.builder import SOURCE_DIST, SOURCE_USER
from lol_coach_mcp.config import Settings
from lol_coach_mcp.store import Store
from test_server_form import EmptyRiot, _mixed, _setup
from test_server_profile import make_server, store  # noqa: F401


def _row(metric="deaths", mean=3.0, *, source=SOURCE_DIST, tier="GOLD", position="BOTTOM",
         n=200, patch="16.22", collected_at="2026-11-01T00:00:00+09:00"):
    return {"tier": tier, "position": position, "champion": None, "metric": metric, "mean": mean,
            "std": 1.0, "n": n, "patch": patch, "collected_at": collected_at, "source": source}


def _file(rows, collected_at="2026-11-01T00:00:00+09:00"):
    return json.dumps({"metadata": {"source": SOURCE_DIST, "collected_at": collected_at,
                                    "patch": "16.22", "note": "n", "notes": []}, "rows": rows},
                      ensure_ascii=False)


def test_loads_when_store_has_no_dist_rows(store):
    assert loader.load_distributed_baselines(store, _file([_row()])) is True
    got = store.get_baseline("GOLD", "BOTTOM", "deaths")
    assert got["source"] == SOURCE_DIST and got["mean"] == 3.0


def test_replaces_all_dist_rows_when_file_is_newer_and_keeps_user_rows(store):
    store.upsert_baselines([_row("kda", 1.0, collected_at="2026-10-01T00:00:00+09:00"),
                            _row("assists", 1.0, collected_at="2026-10-01T00:00:00+09:00"),
                            _row("kda", 9.0, source=SOURCE_USER)])
    assert loader.load_distributed_baselines(store, _file([_row("deaths", 3.0)])) is True
    dist = {r["metric"] for r in store.list_baselines(SOURCE_DIST)}
    assert dist == {"deaths"}  # 옛 배포 행은 모두 교체됨
    users = store.list_baselines(SOURCE_USER)
    assert [(r["metric"], r["mean"]) for r in users] == [("kda", 9.0)]


def test_skips_when_store_dist_rows_are_not_older(store):
    store.upsert_baselines([_row("kda", 1.0, collected_at="2026-11-01T00:00:00+09:00")])
    assert loader.load_distributed_baselines(store, _file([_row("deaths")])) is False
    assert {r["metric"] for r in store.list_baselines(SOURCE_DIST)} == {"kda"}


def test_absent_file_does_nothing(store):
    assert loader.load_distributed_baselines(store, None) is False
    assert store.list_baselines() == []


@pytest.mark.parametrize("text", [
    "{not json", "[]", json.dumps({"rows": []}),
    json.dumps({"metadata": {"collected_at": "2026-11-01"}, "rows": [{"tier": "GOLD"}]}),
    json.dumps({"metadata": {"collected_at": "2026-11-01"}, "rows": "x"}),
])
def test_corrupt_file_warns_and_skips(store, caplog, text):
    store.upsert_baselines([_row("kda", 1.0, collected_at="2026-10-01T00:00:00+09:00")])
    with caplog.at_level(logging.WARNING):
        assert loader.load_distributed_baselines(store, text) is False
    assert any(r.levelno == logging.WARNING for r in caplog.records)
    assert {r["metric"] for r in store.list_baselines(SOURCE_DIST)} == {"kda"}  # 기존 행 보존


def test_user_source_wins_over_dist_for_same_key(store):
    store.upsert_baselines([_row("deaths", 3.0), _row("deaths", 4.5, source=SOURCE_USER)])
    got = store.get_baseline("GOLD", "BOTTOM", "deaths")
    assert got["source"] == SOURCE_USER and got["mean"] == 4.5
    store.upsert_baselines([_row("kda", 2.0)])  # 사용자 수집이 없으면 배포를 사용
    assert store.get_baseline("GOLD", "BOTTOM", "kda")["source"] == SOURCE_DIST


def test_loading_dist_does_not_clobber_user_row(store):
    store.upsert_baselines([_row("deaths", 4.5, source=SOURCE_USER)])
    loader.load_distributed_baselines(store, _file([_row("deaths", 3.0)]))
    assert store.get_baseline("GOLD", "BOTTOM", "deaths")["mean"] == 4.5
    assert len(store.list_baselines()) == 2


def test_packaged_reader_returns_none_when_absent(monkeypatch):
    monkeypatch.setattr(loader, "PACKAGED_NAME", "no-such-file.json")
    assert loader.read_packaged_baselines() is None


def test_default_state_loads_packaged_file(tmp_path, monkeypatch):
    settings = Settings(riot_api_key="k", data_dir=tmp_path, user_agent="t")
    monkeypatch.setattr(server_mod, "load_settings", lambda: settings)
    monkeypatch.setattr(loader, "read_packaged_baselines", lambda: _file([_row()]))
    st = server_mod._default_state()
    try:
        assert st.store.get_baseline("GOLD", "BOTTOM", "deaths")["source"] == SOURCE_DIST
    finally:
        st.store.close()


# ---- 판정 엔진 연결 ----
async def test_engine_uses_dist_baseline_and_names_source(store):
    loader.load_distributed_baselines(store, _file([_row("deaths", 3.0)]))
    stats = _mixed(20)
    async with Client(make_server(store, EmptyRiot())) as c:
        await _setup(c, store, stats)
        r = await c.call_tool("analyze_form", {"compare_to": "tier_baseline"})
        res = await c.read_resource("coach://baselines")
    by = {row["metric"]: row for row in r.structured_content["rows"]}
    assert by["deaths"]["status"] == "ok"
    src = by["deaths"]["ref_source"]
    assert src.startswith("배포 기준선") and "16.22" in src and "200" in src
    assert by["kda"]["status"] == "기준선 없음"
    summary = json.loads(res.contents[0].text)
    assert summary["sources"] == [SOURCE_DIST] and summary["patches"] == ["16.22"]


async def test_engine_prefers_user_baseline_over_dist(store):
    store.upsert_baselines([_row("deaths", 3.0, n=200),
                            _row("deaths", 5.0, source=SOURCE_USER, n=77, patch="16.23")])
    async with Client(make_server(store, EmptyRiot())) as c:
        await _setup(c, store, _mixed(20))
        r = await c.call_tool("analyze_form", {"compare_to": "tier_baseline"})
    by = {row["metric"]: row for row in r.structured_content["rows"]}
    assert by["deaths"]["ref_value"] == 5.0 and by["deaths"]["ref_sample"] == 77
    src = by["deaths"]["ref_source"]
    assert src.startswith("사용자 수집 기준선") and "16.23" in src and "77" in src
    assert re.search(r"GOLD", src)


async def test_engine_without_rows_says_no_baseline(store):
    async with Client(make_server(store, EmptyRiot())) as c:
        await _setup(c, store, _mixed(20))
        r = await c.call_tool("analyze_form", {"compare_to": "tier_baseline"})
    assert {row["status"] for row in r.structured_content["rows"]} == {"기준선 없음"}
