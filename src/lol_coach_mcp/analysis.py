"""근거 기반 판정 엔진 (순수 함수).

지표 추출·요약·비교 판정·틸트 신호·스타일 라벨·개인 최고 기록.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter

from lol_coach_mcp.stats import MatchStats

# 판정 규칙
Z = 1.96
MIN_GAMES = 10  # 내 표본 최소 판수
MIN_REF_GAMES = 10  # 비교 표본 최소 판수
TOP_FINDINGS = 3

STATUS_OK = "ok"
STATUS_SHORT = "판단 보류(표본 부족)"
STATUS_NO_BASELINE = "기준선 없음"
STATUS_REFERENCE = "참고용"
STATUS_NOT_COMPARABLE = "비교 불가"

SUPPORT = "UTILITY"
SUPPORT_REFERENCE_ONLY = ("cs_per_min", "gold_per_min")

# 키 -> (한국어 라벨, 높을수록 좋은가)
METRICS: dict[str, tuple[str, bool]] = {
    "kda": ("KDA", True),
    "cs_per_min": ("분당 CS", True),
    "gold_per_min": ("분당 골드", True),
    "vision_per_min": ("분당 시야점수", True),
    "damage_share": ("팀 내 딜 비중", True),
    "kill_participation": ("킬 관여율", True),
    "deaths": ("데스", False),
    "win": ("승률", True),
}
RATE_METRICS = ("damage_share", "kill_participation", "win")  # 표시 소수 3자리, 나머지 2자리

# 틸트 신호
TILT_LOSS_STREAK = 3
TILT_DEATH_RISE = 2.0
TILT_SHORT_RATIO = 0.8  # 마지막 3판 평균 시간 <= 전체 평균의 80%
TILT_LAST_GAMES = 3
TILT_MIN_GAMES = 4
TILT_LABEL_STREAK = "3연패 이상"
TILT_LABEL_DEATHS = "후반부 데스 증가"
TILT_LABEL_SHORT = "최근 게임 시간 단축"

# 스타일 라벨 (절대 기준, 설명형)
STYLE_KP_MIN = 0.60
STYLE_DAMAGE_SHARE_MIN = 0.28
STYLE_VISION_MIN = 1.5
STYLE_VISION_MIN_SUPPORT = 2.0
STYLE_CS_MIN = 7.5
STYLE_DEATHS_MAX = 3.0
STYLE_NONE = "뚜렷한 성향 없음"

RECORD_QUEUE = 420
RECORD_LABELS = {
    "best_kda": "최고 KDA",
    "best_cs_per_min": "최고 분당 CS",
    "best_damage_share": "최고 딜 비중",
    "most_vision_score": "최다 시야점수",
    "longest_deathless_win": "최장 무데스 승리(게임 시간 초)",
}


def _per_min(value: float, duration_s: int) -> float:
    return value / (duration_s / 60) if duration_s > 0 else 0.0


def _metric_value(s: MatchStats, metric: str) -> float:
    if metric == "kda":
        # stats.kda()와 같은 정의(데스 0이면 K+A)지만 반올림 전 원값
        return (s.kills + s.assists) / max(s.deaths, 1)
    if metric == "cs_per_min":
        return _per_min(s.cs, s.duration_s)
    if metric == "gold_per_min":
        return _per_min(s.gold, s.duration_s)
    if metric == "vision_per_min":
        return _per_min(s.vision_score, s.duration_s)
    if metric == "damage_share":
        return float(s.damage_share)
    if metric == "kill_participation":
        return float(s.kill_participation)
    if metric == "deaths":
        return float(s.deaths)
    if metric == "win":
        return 1.0 if s.win else 0.0
    raise ValueError(f"unknown metric: {metric}")


def metric_series(stats: list[MatchStats]) -> dict[str, list[float]]:
    return {m: [_metric_value(s, m) for s in stats] for m in METRICS}


def summary(values: list[float]) -> dict:
    n = len(values)
    if n == 0:
        return {"mean": None, "std": None, "n": 0}
    std = statistics.stdev(values) if n >= 2 else 0.0
    return {"mean": statistics.fmean(values), "std": std, "n": n}


def _round(metric: str, v: float | None) -> float | None:
    if v is None:
        return None
    return round(v, 3 if metric in RATE_METRICS else 2)


def _ref_summary(ref: list[float] | dict | None) -> dict | None:
    if ref is None:
        return None
    if isinstance(ref, dict):
        return {"mean": ref.get("mean"), "std": ref.get("std"), "n": ref.get("n")}
    return summary(list(ref))


def _half_width(std: float | None, n: int | None) -> float:
    """1.96*sd/sqrt(n). 표준편차나 표본 수가 없으면 0 (평균만 있는 기준)."""
    if std is None or not n:
        return 0.0
    return Z * std / math.sqrt(n)


def compare_metric(
    metric: str,
    mine: list[float],
    ref: list[float] | dict | None,
    ref_source: str,
    position: str,
) -> dict:
    label, higher = METRICS[metric]
    me = summary(list(mine))
    r = _ref_summary(ref)
    ref_mean = r["mean"] if r else None
    ref_n = r["n"] if r else None

    diff = margin = None
    if me["mean"] is not None and ref_mean is not None:
        diff = me["mean"] - ref_mean
        margin = math.hypot(_half_width(me["std"], me["n"]), _half_width(r["std"], ref_n))

    if position == SUPPORT and metric in SUPPORT_REFERENCE_ONLY:
        status = STATUS_REFERENCE
    elif r is None:
        status = STATUS_NO_BASELINE
    elif ref_mean is None or ref_n == 0 or me["n"] == 0:
        status = STATUS_NOT_COMPARABLE
    elif me["n"] < MIN_GAMES or (ref_n is not None and ref_n < MIN_REF_GAMES):
        status = STATUS_SHORT
    else:
        status = STATUS_OK

    clear = strong = False
    if status == STATUS_OK and diff is not None and margin is not None:
        clear = abs(diff) > margin
        strong = clear and abs(diff) >= 2 * margin

    return {
        "metric": metric,
        "label": label,
        "higher_is_better": higher,
        "my_value": _round(metric, me["mean"]),
        "my_sample": me["n"],
        "ref_value": _round(metric, ref_mean),
        "ref_sample": ref_n,
        "ref_source": ref_source,
        "difference": _round(metric, diff),
        "margin": _round(metric, margin),
        "clear_gap": clear,
        "strong_gap": strong,
        "status": status,
    }


def _row_raw(row: dict) -> tuple[float, float]:
    # clear/strong 판정은 원값으로 이미 끝남. 정렬은 표시값(반올림)으로 충분
    return float(row.get("difference") or 0.0), float(row.get("margin") or 0.0)


def _effect(row: dict) -> float:
    diff, margin = _row_raw(row)
    if margin == 0:
        return math.inf if diff else 0.0
    return abs(diff) / margin


def findings(rows: list[dict]) -> dict:
    weak: list[dict] = []
    strong: list[dict] = []
    for row in rows:
        if row.get("status") != STATUS_OK or not row.get("clear_gap"):
            continue
        diff, _ = _row_raw(row)
        good = (diff > 0) == bool(row["higher_is_better"])
        (strong if good else weak).append(row)

    def key(row: dict) -> tuple:
        return (not row.get("strong_gap"), -_effect(row), -abs(_row_raw(row)[0]))

    return {
        "weaknesses": sorted(weak, key=key)[:TOP_FINDINGS],
        "strengths": sorted(strong, key=key)[:TOP_FINDINGS],
    }


def main_position(stats: list[MatchStats]) -> str:
    if not stats:
        return "UNKNOWN"
    counts = Counter(s.position for s in stats)
    latest = {}
    for s in stats:
        latest[s.position] = max(latest.get(s.position, s.game_creation), s.game_creation)
    return max(counts, key=lambda p: (counts[p], latest[p]))


def _chrono(stats: list[MatchStats]) -> list[MatchStats]:
    return sorted(stats, key=lambda s: s.game_creation)


def tilt_signals(stats: list[MatchStats]) -> list[str]:
    games = _chrono(stats)
    signals: list[str] = []
    streak = best = 0
    for s in games:
        streak = 0 if s.win else streak + 1
        best = max(best, streak)
    if best >= TILT_LOSS_STREAK:
        signals.append(TILT_LABEL_STREAK)

    n = len(games)
    if n >= TILT_MIN_GAMES:
        half = n // 2
        first = statistics.fmean(s.deaths for s in games[:half])
        second = statistics.fmean(s.deaths for s in games[n - half:])
        if second - first >= TILT_DEATH_RISE:
            signals.append(TILT_LABEL_DEATHS)
        avg = statistics.fmean(s.duration_s for s in games)
        last = statistics.fmean(s.duration_s for s in games[-TILT_LAST_GAMES:])
        if avg > 0 and last <= avg * TILT_SHORT_RATIO:
            signals.append(TILT_LABEL_SHORT)
    return signals


def style_labels(stats: list[MatchStats], position: str) -> list[str]:
    if not stats:
        return [STYLE_NONE]
    m = {k: statistics.fmean(v) for k, v in metric_series(stats).items()}
    support = position == SUPPORT
    labels: list[str] = []
    if m["kill_participation"] >= STYLE_KP_MIN:
        labels.append("교전 참여형")
    if m["damage_share"] >= STYLE_DAMAGE_SHARE_MIN:
        labels.append("딜 중심형")
    if m["vision_per_min"] >= (STYLE_VISION_MIN_SUPPORT if support else STYLE_VISION_MIN):
        labels.append("시야 장악형")
    if not support and m["cs_per_min"] >= STYLE_CS_MIN:
        labels.append("성장형")
    if m["deaths"] <= STYLE_DEATHS_MAX:
        labels.append("안정형")
    return labels or [STYLE_NONE]


def _record(key: str, games: list[MatchStats], value_fn, decimals: int | None) -> dict | None:
    if not games:
        return None
    # 동률이면 먼저 세운 기록 유지
    best = max(games, key=lambda s: (value_fn(s), -s.game_creation))
    v = value_fn(best)
    return {
        "label": RECORD_LABELS[key],
        "value": round(v, decimals) if decimals is not None else v,
        "match_id": best.match_id,
        "champion": best.champion,
        "game_creation": best.game_creation,
    }


def personal_records(all_stats: list[MatchStats], latest: MatchStats | None) -> dict:
    games = [s for s in all_stats if s.queue_id == RECORD_QUEUE and not s.is_remake]
    non_support = [s for s in games if s.position != SUPPORT]
    deathless_wins = [s for s in games if s.win and s.deaths == 0]
    rec: dict = {
        "best_kda": _record("best_kda", games, lambda s: _metric_value(s, "kda"), 2),
        "best_cs_per_min": _record(
            "best_cs_per_min", non_support, lambda s: _metric_value(s, "cs_per_min"), 2),
        "best_damage_share": _record(
            "best_damage_share", games, lambda s: s.damage_share, 3),
        "most_vision_score": _record("most_vision_score", games, lambda s: s.vision_score, None),
        "longest_deathless_win": _record(
            "longest_deathless_win", deathless_wins, lambda s: s.duration_s, None),
    }
    updated = [
        k for k, v in rec.items()
        if v is not None and latest is not None and v["match_id"] == latest.match_id
    ]
    rec["updated_by_latest"] = updated
    return rec
