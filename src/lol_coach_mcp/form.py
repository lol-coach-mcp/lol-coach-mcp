"""기간 분석 조립 (순수 함수): 기간 선택 → 비교 기준별 근거 행 → 판정."""

from __future__ import annotations

from collections.abc import Callable

from lol_coach_mcp import analysis, periods
from lol_coach_mcp.stats import MatchStats

BEST_SELF_SOURCE = "내 승리 판 평균"
TILT_RECENT_GAMES = 10  # session이 아닌 기간의 틸트 신호 대상 판수
NOTE_UNRANKED = "솔로랭크 언랭크라 티어 기준선을 쓸 수 없어 내 승리 판 평균으로 비교함"
NOTE_TIER_UNKNOWN = "현재 티어를 확인하지 못해 티어 기준선을 쓸 수 없어 내 승리 판 평균으로 비교함"
NOTE_ALL_WINS = "분석 기간의 모든 판이 승리라 승리 판과 비교할 수 없음"
NOTE_NO_WINS = "분석 기간에 승리 판이 없어 승리 판과 비교할 수 없음"

BaselineGetter = Callable[[str, str, str], dict | None]


def _baseline_source(base: dict | None, default: str, tier: str, position: str) -> str:
    """행별 기준 출처 문구: 출처(배포/사용자 수집), 티어·포지션, 패치, 표본 수. 출처를 모르면 기본 문구."""
    if not base or not base.get("source"):
        return default
    parts = [f"{tier} {position}"]
    if base.get("patch"):
        parts.append(f"패치 {base['patch']}")
    if base.get("n") is not None:
        parts.append(f"n={base['n']}")
    return f"{base['source']} 기준선 ({', '.join(parts)})"


def _best_self_rows(mine: list[MatchStats]) -> tuple[list[analysis.EvidenceRow], list[str]]:
    """같은 포지션 승리 판을 기준으로 비교. win 지표는 기준 자체가 1.0이라 제외함."""
    wins = [s for s in mine if s.win]
    position = analysis.main_position(mine)
    notes: list[str] = []
    ref_series = analysis.metric_series(wins)
    if wins and len(wins) == len(mine):
        # 모든 판이 승리: 자기 자신과의 비교라 의미가 없음
        ref_series = {m: [] for m in analysis.METRICS}
        notes.append(NOTE_ALL_WINS)
    elif not wins and mine:
        notes.append(NOTE_NO_WINS)
    series = analysis.metric_series(mine)
    rows = [
        analysis.compare_metric(m, series[m], ref_series[m], BEST_SELF_SOURCE, position)
        for m in analysis.METRICS
        if m != "win"
    ]
    return rows, notes


def build_form(
    stats: list[MatchStats],
    *,
    period: str,
    count: int,
    compare_to: str,
    tier: str | None,
    tier_note: str | None,
    get_baseline: BaselineGetter,
) -> dict:
    """stats: 큐로 걸러진 저장 경기(다시하기 포함 가능). 결과 dict는 FormOut 필드를 담음."""
    notes: list[str] = []
    prior: list[MatchStats] = []
    picked, period_desc = periods.select(stats, period, count)
    if compare_to == "previous_period":
        window = count if period == "recent_n" else len(picked)
        picked, prior, period_desc = periods.previous_period(stats, window)
        if period != "recent_n":
            period_desc = f"{periods.select(stats, period, count)[1]}: {period_desc}"

    position = analysis.main_position(picked)
    mine = [s for s in picked if s.position == position]
    excluded = len(picked) - len(mine)
    prior = [s for s in prior if s.position == position]

    series = analysis.metric_series(mine)
    compare_used = compare_to
    best_rows = best_found = None
    best_source = None

    if compare_to == "tier_baseline" and tier is None:
        compare_used = "best_self"
        notes.append(tier_note or NOTE_TIER_UNKNOWN)

    if compare_used == "best_self":
        rows, extra = _best_self_rows(mine)
        notes += extra
        ref_source = BEST_SELF_SOURCE
    elif compare_used == "previous_period":
        prior_series = analysis.metric_series(prior)
        ref_source = f"직전 {len(prior)}판"
        rows = [
            analysis.compare_metric(m, series[m], prior_series[m], ref_source, position)
            for m in analysis.METRICS
        ]
    else:
        ref_source = f"{tier} {position} 기준선"
        rows = []
        found = 0
        for m in analysis.METRICS:
            base = get_baseline(tier, position, m)
            found += base is not None
            rows.append(analysis.compare_metric(
                m, series[m], base, _baseline_source(base, ref_source, tier, position), position))
        if found == 0:
            best_rows, extra = _best_self_rows(mine)
            notes += extra
            best_source = BEST_SELF_SOURCE
            best_found = analysis.findings(best_rows)

    judged = analysis.findings(rows)

    if period == "session":
        tilt_games = mine
    else:
        tilt_games = mine[:TILT_RECENT_GAMES]  # 최신순
    return {
        "period_desc": period_desc,
        "games": len(mine),
        "position": position,
        "excluded_other_positions": excluded,
        "compare_to": compare_to,
        "compare_used": compare_used,
        "ref_source": ref_source,
        "rows": rows,
        "weaknesses": judged["weaknesses"],
        "strengths": judged["strengths"],
        "best_self_rows": best_rows,
        "best_self_ref_source": best_source,
        "best_self_weaknesses": best_found["weaknesses"] if best_found else None,
        "best_self_strengths": best_found["strengths"] if best_found else None,
        "tilt_signals": analysis.tilt_signals(tilt_games),
        "style": analysis.style_labels(mine, position),
        "notes": notes,
    }
