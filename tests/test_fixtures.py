import json
import re

import pytest

from conftest import FIXTURES_DIR, PUUID_ME, load_fixture

ALLOWED = {PUUID_ME} | {f"puuid-p{i:02d}" for i in range(1, 10)}
MATCHES = ["match_normal", "match_remake", "match_support"]


def _me(match):
    return next(p for p in match["info"]["participants"] if p["puuid"] == PUUID_ME)


@pytest.mark.parametrize(
    "name",
    ["match_normal", "match_remake", "match_support", "timeline_normal", "league_ranked", "league_empty"],
)
def test_fixture_loads(name):
    assert load_fixture(name) is not None


@pytest.mark.parametrize("name", MATCHES)
def test_match_anonymized(name):
    m = load_fixture(name)
    puuids = {p["puuid"] for p in m["info"]["participants"]}
    assert PUUID_ME in puuids
    assert puuids <= ALLOWED
    assert set(m["metadata"]["participants"]) <= ALLOWED
    assert all(p["summonerId"] == "" and p["summonerName"] == "" for p in m["info"]["participants"])
    assert _me(m)["riotIdGameName"] == "테스트유저"


def test_timeline_matches_match():
    t = load_fixture("timeline_normal")
    m = load_fixture("match_normal")
    assert t["metadata"]["participants"] == m["metadata"]["participants"]
    assert {p["puuid"] for p in t["info"]["participants"]} <= ALLOWED


def test_remake_flag():
    assert _me(load_fixture("match_remake"))["gameEndedInEarlySurrender"] is True


def test_support_position():
    assert _me(load_fixture("match_support"))["teamPosition"] == "UTILITY"


def test_league():
    assert load_fixture("league_empty") == []
    entries = load_fixture("league_ranked")
    assert len(entries) > 0
    assert all(e["puuid"] == PUUID_ME for e in entries)


@pytest.mark.parametrize("name", MATCHES + ["timeline_normal"])
def test_match_id_fake(name):
    d = load_fixture(name)
    assert re.fullmatch(r"KR_00000000\d\d", d["metadata"]["matchId"])




def _bad_ids(node):
    """진짜 매치 식별자로 보이는 값(큰 gameId, 가짜가 아닌 matchId/gameName)을 모은다."""
    bad = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "gameId" and not (isinstance(v, int) and v < 100):
                bad.append((k, v))
            elif k == "matchId" and not (isinstance(v, str) and re.fullmatch(r"KR_00000000\d\d", v)):
                bad.append((k, v))
            elif k == "gameName" and not (isinstance(v, str) and re.fullmatch(r"teambuilder-match-\d", v)):
                bad.append((k, v))
            else:
                bad.extend(_bad_ids(v))
    elif isinstance(node, list):
        for x in node:
            bad.extend(_bad_ids(x))
    return bad


REAL_ID_PATTERNS = [re.compile(r"KR_(?!0{8})\d{10}"), re.compile(r"(?<!\d)8\d{9}(?!\d)")]


def _text_hits(text):
    return [m.group(0) for p in REAL_ID_PATTERNS for m in p.finditer(text)]


@pytest.mark.parametrize("name", MATCHES + ["timeline_normal", "league_ranked", "league_empty"])
def test_no_real_match_ids(name):
    assert _bad_ids(load_fixture(name)) == []
    assert _text_hits((FIXTURES_DIR / f"{name}.json").read_text(encoding="utf-8")) == []


FAKE_BIG = str(9 * 10**9 + 1)


def test_bad_ids_checker_flags_real_ids():
    sample = {"metadata": {"matchId": "KR_" + FAKE_BIG}, "info": {"gameId": int(FAKE_BIG),
              "gameName": "teambuilder-match-" + FAKE_BIG}, "events": [{"gameId": int(FAKE_BIG)}]}
    assert len(_bad_ids(sample)) == 4


def test_text_scan_flags_real_ids_but_not_timestamps():
    big = "8" + "1" * 9
    assert _text_hits(json.dumps({"participants": ["KR_" + big]}))
    assert _text_hits(json.dumps({"platformGameId": int(big)}))
    assert _text_hits(json.dumps({"ts": 1758909490178})) == []
