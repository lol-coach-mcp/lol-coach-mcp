"""분할 비교 (순수 함수): 같은 기간의 경기를 기준별로 나눠 내 지표를 비교."""

from __future__ import annotations

import statistics
from collections import Counter
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from lol_coach_mcp import analysis
from lol_coach_mcp.stats import MatchStats

SPLIT_BYS = ("win_loss", "time_of_day", "weekday", "game_length", "position", "patch", "duo")

KST = timezone(timedelta(hours=9))
MIN_GROUP_GAMES = 5  # 그룹 판수 < 5면 판단 보류
DUO_MIN_GAMES = 3  # 기간 내 같은 팀으로 이 판수 이상 함께하면 듀오 파트너 후보

STATUS_OK = "ok"
STATUS_HOLD = "판단 보류"

TIME_BUCKETS = (  # (시작 시 포함, 끝 시 미포함, 라벨)
    (0, 6, "0~6시"),
    (6, 12, "6~12시"),
    (12, 18, "12~18시"),
    (18, 24, "18~24시"),
)
WEEKDAYS = ("월", "화", "수", "목", "금", "토", "일")
LENGTH_SHORT_S = 25 * 60  # < 25분
LENGTH_LONG_S = 35 * 60  # > 35분
LENGTH_LABELS = ("25분 미만", "25~35분", "35분 초과")

WIN_LABEL = "승리 판"
LOSS_LABEL = "패배 판"
WIN_LOSS_SOURCE = "승리 판 vs 패배 판"
SOLO_LABEL = "혼자"
NOTE_CORRELATION = "상관관계이며 원인은 아님"
NOTE_NO_WINS = "승리 판이 없어 승리 판과 패배 판을 비교할 수 없음"
NOTE_NO_LOSSES = "패배 판이 없어 승리 판과 패배 판을 비교할 수 없음"
NOTE_NO_PARTNER = f"기간 내 같은 팀으로 {DUO_MIN_GAMES}판 이상 함께한 듀오 후보가 없음"
NOTE_SOLO_MEANING = f"'{SOLO_LABEL}'는 듀오 파트너 후보와 함께하지 않은 판(그 밖의 일회성 동료 포함)"
NOTE_MIXED_POSITIONS = "여러 포지션이 섞인 기간이라 포지션별 지표 해석에 주의해야 함"
NOTE_HOLD = f"그룹 판수 {MIN_GROUP_GAMES} 미만은 판단 보류"
UNKNOWN_ID = "Riot ID 확인 불가"

MatchLoader = Callable[[str], "dict | None"]


def group_summary(label: str, games: list[MatchStats]) -> dict:
    """그룹 판수·승률(0~1)·지표 평균·상태. 승률은 metrics에 넣지 않고 win_rate로만 둠."""
    n = len(games)
    series = analysis.metric_series(games)
    metrics: dict[str, float | None] = {}
    for m, values in series.items():
        if m == "win":
            continue
        metrics[m] = analysis._round(m, statistics.fmean(values)) if values else None
    win_rate = analysis._round("win", statistics.fmean(series["win"])) if n else None
    return {
        "label": label,
        "games": n,
        "win_rate": win_rate,
        "metrics": metrics,
        "status": STATUS_OK if n >= MIN_GROUP_GAMES else STATUS_HOLD,
    }


def _bucketed(stats: list[MatchStats], labels: list[str], key: Callable[[MatchStats], str]) -> list[dict]:
    by: dict[str, list[MatchStats]] = {label: [] for label in labels}
    for s in stats:
        by[key(s)].append(s)
    return [group_summary(label, by[label]) for label in labels if by[label]]


def _kst(s: MatchStats) -> datetime:
    return datetime.fromtimestamp(s.game_creation / 1000, tz=KST)


def _time_label(s: MatchStats) -> str:
    hour = _kst(s).hour
    return next(label for lo, hi, label in TIME_BUCKETS if lo <= hour < hi)


def _length_label(s: MatchStats) -> str:
    if s.duration_s < LENGTH_SHORT_S:
        return LENGTH_LABELS[0]
    if s.duration_s > LENGTH_LONG_S:
        return LENGTH_LABELS[2]
    return LENGTH_LABELS[1]


def _patch_sort_key(patch: str) -> tuple:
    return tuple(int(x) if x.isdigit() else 0 for x in patch.split("."))


def win_loss_rows(stats: list[MatchStats]) -> tuple[list[analysis.EvidenceRow], list[str]]:
    wins = [s for s in stats if s.win]
    losses = [s for s in stats if not s.win]
    position = analysis.main_position(stats)
    win_series = analysis.metric_series(wins)
    loss_series = analysis.metric_series(losses)
    rows = [
        analysis.compare_metric(m, win_series[m], loss_series[m], WIN_LOSS_SOURCE, position)
        for m in analysis.METRICS
        if m != "win"
    ]
    notes = [NOTE_CORRELATION]
    if not wins or not losses:
        for row in rows:
            row["status"] = analysis.STATUS_NOT_COMPARABLE
            row["clear_gap"] = row["strong_gap"] = False
        notes.append(NOTE_NO_WINS if not wins else NOTE_NO_LOSSES)
    return rows, notes


def _riot_id(puuid: str, matches: dict[str, dict]) -> str:
    for match in matches.values():
        for p in match.get("info", {}).get("participants", []):
            if p.get("puuid") == puuid and p.get("riotIdGameName"):
                return f"{p['riotIdGameName']}#{p.get('riotIdTagline', '')}"
    return UNKNOWN_ID


def duo_groups(
    stats: list[MatchStats], get_match: MatchLoader
) -> tuple[list[dict], list[dict], list[str]]:
    """듀오 파트너(기간 내 3판 이상 같은 팀) 추정. 파트너 본인 지표는 계산하지 않음."""
    counts = Counter(t for s in stats for t in set(s.teammates))
    partners = sorted(
        (p for p, n in counts.items() if n >= DUO_MIN_GAMES), key=lambda p: (-counts[p], p)
    )
    matches = {s.match_id: m for s in stats if (m := get_match(s.match_id))}
    labels: dict[str, str] = {}
    for p in partners:
        rid = _riot_id(p, matches)
        labels[p] = rid if rid != UNKNOWN_ID else f"{UNKNOWN_ID}:{p[:8]}"
    partner_info = [{"riot_id": labels[p], "games_together": counts[p]} for p in partners]

    by: dict[str, list[MatchStats]] = {p: [] for p in partners}
    solo: list[MatchStats] = []
    for s in stats:
        # 한 판에 파트너가 여럿이면 함께한 판이 가장 많은 파트너에게만 배정(중복 집계 방지)
        mine = next((p for p in partners if p in s.teammates), None)
        (by[mine] if mine else solo).append(s)
    groups = [group_summary(f"듀오({labels[p]})", by[p]) for p in partners if by[p]]
    groups.append(group_summary(SOLO_LABEL, solo))  # 0판이어도 표시(판단 보류)
    notes = [NOTE_SOLO_MEANING] if partners else [NOTE_NO_PARTNER]
    return groups, partner_info, notes


def build_splits(stats: list[MatchStats], split_by: str, get_match: MatchLoader) -> dict:
    """stats: 기간으로 선택된 경기(다시하기 제외). 결과 dict는 SplitsOut 필드(일부)를 담음."""
    if split_by not in SPLIT_BYS:
        raise ValueError(f"unknown split_by: {split_by}")
    rows: list = []
    partners: list[dict] = []
    notes: list[str] = []

    if split_by == "win_loss":
        groups = [
            group_summary(label, [s for s in stats if s.win == is_win])
            for label, is_win in ((WIN_LABEL, True), (LOSS_LABEL, False))
        ]
        groups = [g for g in groups if g["games"]]
        rows, notes = win_loss_rows(stats)
    elif split_by == "time_of_day":
        groups = _bucketed(stats, [b[2] for b in TIME_BUCKETS], _time_label)
    elif split_by == "weekday":
        groups = _bucketed(stats, list(WEEKDAYS), lambda s: WEEKDAYS[_kst(s).weekday()])
    elif split_by == "game_length":
        groups = _bucketed(stats, list(LENGTH_LABELS), _length_label)
    elif split_by == "position":
        order = sorted({s.position for s in stats})
        groups = _bucketed(stats, order, lambda s: s.position)
    elif split_by == "patch":
        order = sorted({s.patch for s in stats}, key=_patch_sort_key, reverse=True)
        groups = _bucketed(stats, order, lambda s: s.patch)
    else:
        groups, partners, notes = duo_groups(stats, get_match)

    if split_by != "position" and len({s.position for s in stats}) > 1:
        notes.append(NOTE_MIXED_POSITIONS)
    if any(g["status"] == STATUS_HOLD for g in groups):
        notes.append(NOTE_HOLD)
    return {"groups": groups, "rows": rows, "duo_partners": partners, "notes": notes}
