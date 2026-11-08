import re
from dataclasses import replace

import pytest

from conftest import PUUID_ME, load_fixture
from lol_coach_mcp.stats import MatchStats, extract_match_stats, kda, match_tags, recent_form


def _me(match):
    return next(p for p in match["info"]["participants"] if p["puuid"] == PUUID_ME)


def test_extract_normal_match():
    match = load_fixture("match_normal")
    me = _me(match)
    s = extract_match_stats(match, PUUID_ME)
    assert s.cs == me["totalMinionsKilled"] + me["neutralMinionsKilled"]
    assert s.cs_per_min == round(s.cs / (s.duration_s / 60), 1)
    assert re.fullmatch(r"\d+\.\d+", s.patch)
    assert len(s.teammates) == 4
    assert PUUID_ME not in s.teammates


def test_remake_flag():
    s = extract_match_stats(load_fixture("match_remake"), PUUID_ME)
    assert s.is_remake is True
    assert match_tags(s) == ["다시하기"]


def test_support_never_cs_tag():
    s = extract_match_stats(load_fixture("match_support"), PUUID_ME)
    assert s.position == "UTILITY"
    assert "CS 부족" not in match_tags(s)


def test_kda_zero_deaths():
    assert kda(5, 0, 7) == 12.0
    assert kda(3, 2, 1) == 2.0


def test_unknown_puuid_raises():
    with pytest.raises(ValueError):
        extract_match_stats(load_fixture("match_normal"), "puuid-nobody")


def _stat(created, win=True, remake=False):
    base = extract_match_stats(load_fixture("match_normal"), PUUID_ME)
    return replace(base, game_creation=created, win=win, is_remake=remake)


def test_recent_form_sorts_and_skips_remakes():
    stats = [
        _stat(3, True),
        _stat(7, False, remake=True),
        _stat(1, False),  # oldest, falls outside latest 5
        _stat(5, True),
        _stat(2, True),
        _stat(6, False, remake=True),
        _stat(4, True),
    ]
    # valid games by recency: 5(W),4(W),3(W),2(W),1(L) -> 4 wins of 5
    r = recent_form(stats)
    assert r == {"games": 5, "wins": 4, "label": "상승세"}


def test_recent_form_insufficient():
    r = recent_form([_stat(1), _stat(2)])
    assert r["games"] == 2
    assert r["label"] == "판단 보류"
