"""집중 과제 조립 (순수 함수): 과제 제안, 진행 판정, 연습 카탈로그."""

from __future__ import annotations

import json
import statistics
from importlib import resources

from lol_coach_mcp import analysis
from lol_coach_mcp.stats import MatchStats

GOAL_QUEUE = 420
DEFAULT_GAMES = 10
BASELINE_GAMES = 20  # 기준값(내 평균)을 구하는 최근 판수
RATE_DECIMALS = 3  # 비율 지표 목표 반올림 자릿수
OTHER_DECIMALS = 1
STATUS_ACTIVE = "진행 중"
STATUS_ACHIEVED = "달성"
STATUS_MISSED = "미달"
STATUS_CLOSED = "종료"
GOAL_METRICS = tuple(m for m in analysis.METRICS if m != "win")


def load_practice() -> dict[str, list[str]]:
    text = resources.files("lol_coach_mcp").joinpath("data/practice.json").read_text(encoding="utf-8")
    return json.loads(text)


def round_target(metric: str, v: float) -> float:
    return round(v, RATE_DECIMALS if metric in analysis.RATE_METRICS else OTHER_DECIMALS)


def direction_of(metric: str) -> str:
    return "up" if analysis.METRICS[metric][1] else "down"


def suggest_goal(
    weaknesses: list[dict],
    series: dict[str, list[float]],
    *,
    games: int = DEFAULT_GAMES,
    metric: str | None = None,
) -> dict | None:
    """약점 1순위(metric 지정 시 그 지표)의 목표 = 내 평균과 기준값의 중간. 없으면 None."""
    for row in weaknesses:
        m = row["metric"]
        if m not in GOAL_METRICS or (metric is not None and m != metric):
            continue
        values = series.get(m) or []
        ref = row.get("ref_value")
        if not values or ref is None:
            continue
        mean = statistics.fmean(values)
        return {
            "metric": m,
            "direction": direction_of(m),
            "target": round_target(m, (mean + ref) / 2),
            "baseline_value": mean,
            "games": games,
        }
    return None


def evaluate_goal(goal: dict, stats: list[MatchStats]) -> dict:
    """시작 이후(큐 420, 다시하기 제외, 과제 포지션) 앞쪽 games_to_track판으로 판정함."""
    needed = int(goal["games_to_track"])
    after = sorted(
        (
            s for s in stats
            if s.game_creation > goal["start_after_ms"]
            and s.queue_id == GOAL_QUEUE
            and not s.is_remake
            and (goal.get("position") is None or s.position == goal["position"])
        ),
        key=lambda s: s.game_creation,
    )[:needed]
    done = len(after)
    avg = None
    change = None
    if after:
        avg = statistics.fmean(analysis.metric_series(after)[goal["metric"]])
        if goal.get("baseline_value") is not None:
            change = avg - goal["baseline_value"]
    status = STATUS_ACTIVE
    if done >= needed and avg is not None:
        met = avg >= goal["target"] if goal["direction"] == "up" else avg <= goal["target"]
        status = STATUS_ACHIEVED if met else STATUS_MISSED
    return {
        "status": status,
        "games_done": done,
        "games_needed": needed,
        "current_avg": avg,
        "change_vs_baseline": change,
    }
