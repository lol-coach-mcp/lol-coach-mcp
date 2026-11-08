import hashlib
import json
import re
import sqlite3

import pytest

from lol_coach_mcp.analysis import METRICS
from lol_coach_mcp.baseline import cli
from lol_coach_mcp.baseline.builder import MIN_SAMPLES, build_baselines, export_baselines
from lol_coach_mcp.baseline.collector import collect
from lol_coach_mcp.config import Settings
from lol_coach_mcp.riot.errors import RiotAuthError
from lol_coach_mcp.store import BASELINE_METRIC_COLUMNS, Store

POSITIONS = ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY")


def make_match(match_id: str, *, queue: int = 420, remake: bool = False, blank_pos: bool = False) -> dict:
    n = int(match_id.split("_")[1])
    parts = []
    for i in range(10):
        parts.append({
            "puuid": f"anon-{n}-{i}", "teamId": 100 if i < 5 else 200, "win": i < 5,
            "championName": f"Champ{i % 5}", "teamPosition": POSITIONS[i % 5],
            "kills": 2 + i, "deaths": 1 + (n + i) % 5, "assists": 3 + i,
            "totalMinionsKilled": 150 + 5 * i, "neutralMinionsKilled": 10, "goldEarned": 9000 + 100 * i,
            "visionScore": 20 + i, "totalDamageDealtToChampions": 10000 + 500 * i,
            "gameEndedInEarlySurrender": remake and i == 0,
        })
    if blank_pos:
        parts[9]["teamPosition"] = ""
    return {"metadata": {"matchId": match_id},
            "info": {"gameCreation": 1_700_000_000_000 + n, "gameDuration": 1800,
                     "gameVersion": "16.21.1", "queueId": queue, "participants": parts}}


class FakeClient:
    """네트워크 없는 RiotClient 대역. 인접 시드는 매치 1건을 공유함."""

    def __init__(self, seeds: dict[tuple, int] | None = None, apex: dict[str, int] | None = None,
                 fail_after_matches: int | None = None, special: dict | None = None) -> None:
        self.seeds = seeds if seeds is not None else {("GOLD", "I", 1): 12}
        self.apex = apex or {}
        self.fail_after = fail_after_matches
        self.special = special or {}
        self.fetched: list[str] = []
        self.apex_calls: list[str] = []

    async def get_league_entries_page(self, queue, tier, division, page):
        n = self.seeds.get((tier, division, page), 0)
        return [{"puuid": f"seed-{tier}-{division}-{page}-{i}"} for i in range(n)]

    async def get_apex_league(self, tier, queue):
        self.apex_calls.append(tier)
        return {"entries": [{"puuid": f"seed-{tier}-{i}"} for i in range(self.apex.get(tier, 0))]}

    async def get_match_ids(self, puuid, *, queue=None, start=0, count=100, start_time=None):
        i = int(puuid.rsplit("-", 1)[1])
        base = i * 2 + 1 + (200 if "MASTER" in puuid else 0)
        return [f"KR_{base + k:010d}" for k in range(3)][:count]

    async def get_match(self, match_id):
        if self.fail_after is not None and len(self.fetched) >= self.fail_after:
            raise RiotAuthError()
        self.fetched.append(match_id)
        return make_match(match_id, **self.special.get(match_id, {}))


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "t.db")
    yield s
    s.close()


async def run(client, store, **kw):
    args = dict(tiers=["GOLD"], matches_per_tier=4, queue=420, resume=False, rng_seed=7,
                progress=lambda m: None)
    args.update(kw)
    return await collect(client, store, **args)


def all_text(store) -> str:
    conn = store._conn
    out = []
    for t in ("baseline_samples", "baseline_jobs", "baseline_seen"):
        out += [str(tuple(r)) for r in conn.execute(f"SELECT * FROM {t}")]
    return "\n".join(out)


def test_metric_columns_match_analysis_metrics():
    assert set(BASELINE_METRIC_COLUMNS) == set(METRICS)


async def test_collect_stores_samples_without_identifiers(store):
    await run(FakeClient(), store)
    assert store.baseline_match_count("GOLD") == 4
    assert store.baseline_sample_count("GOLD") == 40
    rows = store.list_baseline_samples()
    assert {r["tier"] for r in rows} == {"GOLD"}
    assert {r["position"] for r in rows} == set(POSITIONS)
    assert {r["patch"] for r in rows} == {"16.21"}
    text = all_text(store)
    assert "seed-" not in text and "anon-" not in text and "KR_" not in text
    # 매치 ID 해시는 솔트가 있어 단순 sha256과 다름
    assert hashlib.sha256(b"KR_0000000001").hexdigest() not in text
    job = store.get_baseline_job("GOLD")
    assert job["status"] == "done" and job["matches"] == 4


async def test_collect_samples_reuse_metric_semantics(store):
    c = FakeClient()
    await run(c, store, matches_per_tier=1)
    n = int(c.fetched[0].split("_")[1])
    r = next(x for x in store.list_baseline_samples() if x["position"] == "TOP" and x["win"] == 1.0)
    deaths = 1 + n % 5  # participant 0
    assert r["deaths"] == deaths
    assert r["kda"] == pytest.approx((2 + 3) / max(deaths, 1))
    assert r["cs_per_min"] == pytest.approx(160 / 30)
    assert r["gold_per_min"] == pytest.approx(9000 / 30)


async def test_collect_dedupes_shared_matches(store):
    # 시드 5명 x 3경기, 인접 시드가 1경기 공유 -> 고유 11경기
    c = FakeClient(seeds={("GOLD", "I", 1): 5})
    await run(c, store, matches_per_tier=100)
    assert store.baseline_match_count("GOLD") == 11
    assert store.baseline_sample_count("GOLD") == 110
    assert len(c.fetched) == len(set(c.fetched)) == 11


async def test_collect_skips_remake_queue_mismatch_and_blank_position(store):
    c = FakeClient(seeds={("GOLD", "I", 1): 2}, special={
        "KR_0000000001": {"remake": True}, "KR_0000000002": {"queue": 450},
        "KR_0000000003": {"blank_pos": True}})
    await run(c, store, matches_per_tier=100)
    # seed 0: ids 1,2,3 ; seed 1: ids 3,4,5 -> 1,2 제외, 3은 9명, 4,5 정상
    assert store.baseline_match_count("GOLD") == 3
    assert store.baseline_sample_count("GOLD") == 9 + 10 + 10


async def test_resume_after_auth_error_has_no_duplicates(store, tmp_path):
    ref = Store(tmp_path / "ref.db")
    try:
        await run(FakeClient(), ref)
        expected = ref.baseline_sample_count("GOLD")
    finally:
        ref.close()

    first = FakeClient(fail_after_matches=2)
    with pytest.raises(RiotAuthError):
        await run(first, store)
    assert store.baseline_match_count("GOLD") == 2
    assert store.get_baseline_job("GOLD")["status"] == "interrupted"

    second = FakeClient()
    await run(second, store, resume=True)
    assert store.baseline_match_count("GOLD") == 4
    assert store.baseline_sample_count("GOLD") == expected
    assert not set(first.fetched) & set(second.fetched)
    assert store.get_baseline_job("GOLD")["status"] == "done"


async def test_resume_can_extend_target(store):
    await run(FakeClient(), store, matches_per_tier=3)
    c = FakeClient()
    await run(c, store, matches_per_tier=6, resume=True)
    assert store.baseline_match_count("GOLD") == 6
    assert len(c.fetched) == 3  # 이미 수집한 3경기는 다시 받지 않음


async def test_without_resume_restarts_tier(store):
    await run(FakeClient(), store)
    await run(FakeClient(), store)
    assert store.baseline_sample_count("GOLD") == 40


async def test_same_rng_seed_is_reproducible(store, tmp_path):
    seeds = {("GOLD", d, p): 4 for d in ("I", "II") for p in (1, 2, 3)}
    a, b = FakeClient(seeds=seeds), FakeClient(seeds=seeds)
    await run(a, store, rng_seed=3)
    other = Store(tmp_path / "o.db")
    try:
        await run(b, other, rng_seed=3)
    finally:
        other.close()
    assert a.fetched == b.fetched


async def test_pages_exhaust_gracefully(store):
    c = FakeClient(seeds={("GOLD", "I", 1): 1})
    await run(c, store, matches_per_tier=50)
    assert store.baseline_match_count("GOLD") == 3
    assert store.get_baseline_job("GOLD")["status"] == "exhausted"


async def test_apex_tier_uses_apex_endpoint(store):
    c = FakeClient(seeds={}, apex={"MASTER": 3})
    await run(c, store, tiers=["MASTER"], matches_per_tier=3)
    assert c.apex_calls == ["MASTER"]
    assert store.baseline_match_count("MASTER") == 3
    assert {r["tier"] for r in store.list_baseline_samples()} == {"MASTER"}


def _insert(store, tier, position, count, *, patch="16.21", start=0):
    for k in range(count):
        v = start + k
        store.add_baseline_match(tier, f"M{tier}{position}{start}-{k}", [{
            "position": position, "champion": "Ahri", "patch": patch,
            **{c: float(v) for c in BASELINE_METRIC_COLUMNS}}])


def test_build_applies_min_samples_threshold(store):
    _insert(store, "GOLD", "TOP", MIN_SAMPLES)
    _insert(store, "GOLD", "JUNGLE", MIN_SAMPLES - 1)
    _insert(store, "GOLD", "TOP", 5, patch="16.20", start=100)
    res = build_baselines(store, now="2026-11-09T00:00:00+09:00")
    assert MIN_SAMPLES == 30
    assert res["rows"] == len(METRICS) and res["skipped_groups"] == 1
    got = store.get_baseline("GOLD", "TOP", "kda")
    assert got["n"] == 35 and got["source"] == "사용자 수집"
    assert got["patch"] == "16.21" and got["collected_at"] == "2026-11-09T00:00:00+09:00"
    assert got["std"] is not None
    assert store.get_baseline("GOLD", "JUNGLE", "kda") is None  # 판정은 "기준선 없음"
    assert store.get_baseline("GOLD", "TOP", "kda", champion="Ahri") is None  # champion 무관 행만


def test_build_replaces_stale_user_rows_but_keeps_other_sources(store):
    store.upsert_baselines([{"tier": "GOLD", "position": "MIDDLE", "metric": "kda", "mean": 1.0,
                             "n": 99, "source": "사용자 수집"},
                            {"tier": "GOLD", "position": "MIDDLE", "metric": "deaths", "mean": 1.0,
                             "n": 99, "source": "배포"}])
    build_baselines(store, now="t")
    assert store.get_baseline("GOLD", "MIDDLE", "kda") is None
    assert store.get_baseline("GOLD", "MIDDLE", "deaths")["source"] == "배포"


ID_PATTERNS = [r"KR_\d+", r"(?i)puuid", r"(?i)riotid", r"(?i)matchid", r"#", r"[A-Za-z0-9_-]{60,}",
               r"seed-", r"anon-"]


async def test_export_has_no_identifiers_and_has_metadata(store, tmp_path):
    await run(FakeClient(seeds={("GOLD", "I", 1): 12}), store, matches_per_tier=25)
    assert build_baselines(store, now="2026-11-09T01:00:00+09:00")["rows"] > 0
    out = tmp_path / "out" / "baselines.json"
    export_baselines(store, out)
    text = out.read_text(encoding="utf-8")
    for pat in ID_PATTERNS:
        assert not re.search(pat, text), pat
    data = json.loads(text)
    assert data["metadata"] == {"source": "배포", "collected_at": "2026-11-09T01:00:00+09:00",
                                "patch": "16.21", "note": "티어 라벨은 시드 기준 근사"}
    assert data["rows"] and all(r["source"] == "배포" for r in data["rows"])
    assert {"tier", "position", "champion", "metric", "mean", "std", "n", "patch",
            "collected_at", "source"} == set(data["rows"][0])
    assert all(r["n"] >= MIN_SAMPLES and r["champion"] is None for r in data["rows"])


def test_export_without_rows_errors(store, tmp_path):
    with pytest.raises(ValueError):
        export_baselines(store, tmp_path / "x.json")


# ---- CLI ----
@pytest.fixture
def settings(tmp_path):
    return Settings(riot_api_key="k", data_dir=tmp_path, user_agent="t")


def test_cli_full_flow(settings, tmp_path, capsys):
    client = FakeClient(seeds={("GOLD", "I", 1): 12})
    assert cli.main(["collect", "--tiers", "gold", "--matches-per-tier", "20", "--seed", "1"],
                    settings=settings, client_factory=lambda s: client) == 0
    cap = capsys.readouterr()
    assert cap.out == "" and "GOLD" in cap.err
    assert cli.main(["build"], settings=settings) == 0
    out = tmp_path / "b.json"
    assert cli.main(["export", "--out", str(out)], settings=settings) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["metadata"]["source"] == "배포"


def test_cli_auth_error_exit_2_and_progress_kept(settings, capsys):
    client = FakeClient(fail_after_matches=2)
    code = cli.main(["collect", "--tiers", "GOLD", "--matches-per-tier", "4"],
                    settings=settings, client_factory=lambda s: client)
    cap = capsys.readouterr()
    assert code == 2
    assert "키" in cap.err and "--resume" in cap.err and cap.out == ""
    conn = sqlite3.connect(str(settings.data_dir / "lol-coach.sqlite3"))
    try:
        assert conn.execute("SELECT COUNT(*) FROM baseline_samples").fetchone()[0] == 20
    finally:
        conn.close()
    code = cli.main(["collect", "--tiers", "GOLD", "--matches-per-tier", "4", "--resume"],
                    settings=settings, client_factory=lambda s: FakeClient())
    assert code == 0


def test_cli_rejects_unknown_tier(settings, capsys):
    assert cli.main(["collect", "--tiers", "WOOD"], settings=settings,
                    client_factory=lambda s: FakeClient()) == 1
    assert "WOOD" in capsys.readouterr().err
