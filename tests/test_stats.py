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


SHUFFLED_TIMES = [3, 10, 1, 7, 2, 5, 11, 4, 9, 6, 8]  # 10 and 11 are remakes (newest)


def _shuffled(win_times):
    return [_stat(t, win=t in win_times or t >= 10, remake=t >= 10) for t in SHUFFLED_TIMES]


def test_recent_form_sorts_and_skips_remakes():
    # 9 real games (t=1..9) + 2 remakes flagged as wins; the 4 oldest are wins,
    # the newest 5 (t=5..9) contain exactly 1 win. Input order, oldest-first
    # order, or counting remakes would each give a different label.
    r = recent_form(_shuffled({1, 2, 3, 4, 9}))
    assert r == {"games": 5, "wins": 1, "label": "하락세"}


def test_recent_form_newest_five_rising():
    # Mirror: the 4 oldest are losses, newest 5 have 4 wins.
    r = recent_form(_shuffled({5, 6, 7, 8}))
    assert r == {"games": 5, "wins": 4, "label": "상승세"}


def test_recent_form_insufficient():
    r = recent_form([_stat(1), _stat(2)])
    assert r["games"] == 2
    assert r["label"] == "판단 보류"
