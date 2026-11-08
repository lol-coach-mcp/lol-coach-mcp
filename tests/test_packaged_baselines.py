"""패키지에 포함된 배포 기준선 파일(data/baselines.json)의 내용 검증."""

from __future__ import annotations

import re

import pytest

from lol_coach_mcp.analysis import METRICS
from lol_coach_mcp.baseline.loader import _parse, read_packaged_baselines

MIN_N = 30
TIERS = {"IRON", "BRONZE", "SILVER", "GOLD", "PLATINUM", "EMERALD", "DIAMOND"}
POSITIONS = {"TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"}
PUUID_LIKE = re.compile(r"[A-Za-z0-9_-]{70,}")
MATCH_ID = re.compile(r"KR_\d+")


@pytest.fixture(scope="module")
def raw() -> str:
    text = read_packaged_baselines()
    assert text is not None, "data/baselines.json이 패키지에 없음"
    return text


@pytest.fixture(scope="module")
def parsed(raw):
    return _parse(raw)


def test_parses_with_loader(parsed):
    collected_at, rows = parsed
    assert collected_at
    assert rows


def test_metadata_source_is_distributed(raw):
    import json

    meta = json.loads(raw)["metadata"]
    assert meta["source"] == "배포"
    assert meta.get("patch")


def test_rows_have_enough_samples(parsed):
    _, rows = parsed
    assert all(isinstance(r["n"], int) and r["n"] >= MIN_N for r in rows)


def test_metrics_tiers_positions_are_known(parsed):
    _, rows = parsed
    assert {r["metric"] for r in rows} <= set(METRICS)
    assert {r["tier"] for r in rows} <= TIERS
    assert {r["position"] for r in rows} <= POSITIONS
    assert all(r["champion"] is None for r in rows)


def test_no_identifiers_in_raw_text(raw):
    assert not PUUID_LIKE.search(raw)
    assert not MATCH_ID.search(raw)
    lowered = raw.lower()
    for word in ("puuid", "riotid", "gamename", "tagline", "summonerid", "summonername"):
        assert word not in lowered
