"""기간 선택 (순수 함수). '지금'은 최신 경기의 game_creation 기준(벽시계 사용 안 함)."""

from __future__ import annotations

from lol_coach_mcp.stats import MatchStats

PERIODS = ("recent_n", "session", "week", "month", "season")
SESSION_GAP_MS = 60 * 60 * 1000  # 앞 경기 종료 ~ 다음 경기 시작 간격 <= 60분이면 같은 세션
DAY_MS = 24 * 60 * 60 * 1000
WEEK_DAYS = 7
MONTH_DAYS = 30


def _major(patch: str) -> str:
    return patch.split(".")[0]


def select(stats: list[MatchStats], period: str, count: int) -> tuple[list[MatchStats], str]:
    """기간에 해당하는 경기(최신순, 다시하기 제외)와 한국어 기간 설명을 반환."""
    if period not in PERIODS:
        raise ValueError(f"unknown period: {period}")
    games = sorted((s for s in stats if not s.is_remake),
                   key=lambda s: s.game_creation, reverse=True)

    if period == "recent_n":
        picked = games[:max(count, 0)]
        return picked, f"최근 {count}판"

    if not games:
        empty_desc = {
            "session": "최근 세션(0판)",
            "week": f"최근 {WEEK_DAYS}일(최신 경기 기준)",
            "month": f"최근 {MONTH_DAYS}일(최신 경기 기준)",
            "season": "시즌(경기 없음)",
        }
        return [], empty_desc[period]

    latest = games[0]
    if period == "session":
        picked = [latest]
        for older in games[1:]:
            newer = picked[-1]
            gap = newer.game_creation - (older.game_creation + older.duration_s * 1000)
            if gap > SESSION_GAP_MS:
                break
            picked.append(older)
        return picked, f"최근 세션({len(picked)}판, 경기 간격 60분 이내)"

    if period in ("week", "month"):
        days = WEEK_DAYS if period == "week" else MONTH_DAYS
        cutoff = latest.game_creation - days * DAY_MS
        picked = [s for s in games if s.game_creation >= cutoff]
        return picked, f"최근 {days}일(최신 경기 기준)"

    major = _major(latest.patch)
    picked = [s for s in games if _major(s.patch) == major]
    return picked, f"시즌 {major}.x(패치 {major}.x)"
