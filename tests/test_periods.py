import pytest

from lol_coach_mcp.periods import previous_period, select
from lol_coach_mcp.stats import MatchStats

MIN = 60_000
DAY = 24 * 60 * MIN
BASE = 1_780_000_000_000


def _ms(i, created, *, duration_s=1800, patch="16.22", is_remake=False):
    return MatchStats(
        match_id=f"KR_{i:010d}", queue_id=420, game_creation=created, duration_s=duration_s,
        patch=patch, is_remake=is_remake, win=True, position="MIDDLE", champion="Ahri",
        kills=1, deaths=1, assists=1, cs=200, cs_per_min=6.7, gold=10000, vision_score=20,
        damage_to_champions=10000, damage_share=0.2, kill_participation=0.5, team_id=100,
        teammates=(),
    )


def _ids(games):
    return [g.match_id[-2:] for g in games]


def test_recent_n_newest_first():
    games = [_ms(i, BASE + i * DAY) for i in range(1, 6)]
    shuffled = [games[2], games[0], games[4], games[1], games[3]]
    got, desc = select(shuffled, "recent_n", 3)
    assert _ids(got) == ["05", "04", "03"]
    assert desc == "최근 3판"


def test_session_gap_60min():
    # each game 30 min; next starts gap minutes after previous ended
    g1 = _ms(1, BASE)                         # ends BASE+30
    g2 = _ms(2, BASE + 30 * MIN + 61 * MIN)   # gap 61 -> break
    g3 = _ms(3, g2.game_creation + 30 * MIN + 60 * MIN)  # gap 60 -> same session
    g4 = _ms(4, g3.game_creation + 30 * MIN + 5 * MIN)
    got, desc = select([g1, g3, g4, g2], "session", 20)
    assert _ids(got) == ["04", "03", "02"]
    assert "세션" in desc and "3판" in desc


def test_week_and_month_use_latest_game_as_now():
    latest = BASE + 100 * DAY
    games = [
        _ms(1, latest),
        _ms(2, latest - 7 * DAY),        # boundary included
        _ms(3, latest - 7 * DAY - 1),    # excluded from week
        _ms(4, latest - 30 * DAY),
        _ms(5, latest - 31 * DAY),
    ]
    w, wdesc = select(games, "week", 20)
    assert _ids(w) == ["01", "02"]
    assert "7일" in wdesc
    m, mdesc = select(games, "month", 20)
    assert _ids(m) == ["01", "02", "03", "04"]
    assert "30일" in mdesc


def test_season_patch_major():
    games = [
        _ms(1, BASE + 5, patch="16.22"),
        _ms(2, BASE + 4, patch="16.1"),
        _ms(3, BASE + 3, patch="15.24"),
        _ms(4, BASE + 2, patch="16.3"),
    ]
    got, desc = select(games, "season", 20)
    assert _ids(got) == ["01", "02", "04"]
    assert "16.x" in desc


def test_remakes_excluded_and_empty():
    games = [_ms(1, BASE + 2, is_remake=True), _ms(2, BASE + 1)]
    got, _ = select(games, "recent_n", 5)
    assert _ids(got) == ["02"]
    for period in ("recent_n", "session", "week", "month", "season"):
        got, desc = select([], period, 5)
        assert got == [] and isinstance(desc, str)


def test_unknown_period():
    with pytest.raises(ValueError):
        select([], "year", 5)


def test_recent_n_actual_count_and_clamp():
    games = [_ms(i, BASE + i * DAY) for i in range(1, 4)]
    got, desc = select(games, "recent_n", 20)
    assert len(got) == 3 and desc == "최근 20판(실제 3판)"
    for bad in (0, -5):
        got, desc = select(games, "recent_n", bad)
        assert _ids(got) == ["03"] and desc == "최근 1판"


def test_previous_period():
    games = [_ms(i, BASE + i * DAY) for i in range(1, 8)]
    recent, prev, desc = previous_period(list(reversed(games)), 3)
    assert _ids(recent) == ["07", "06", "05"] and _ids(prev) == ["04", "03", "02"]
    assert desc == "최근 3판 vs 그 이전 3판"
    recent, prev, desc = previous_period(games, 5)
    assert _ids(recent) == ["07", "06", "05", "04", "03"] and _ids(prev) == ["02", "01"]
    assert desc == "최근 5판 vs 그 이전 2판"
    recent, prev, _ = previous_period(games + [_ms(9, BASE + 99 * DAY, is_remake=True)], 0)
    assert _ids(recent) == ["07"] and _ids(prev) == ["06"]
    assert previous_period([], 3)[:2] == ([], [])
