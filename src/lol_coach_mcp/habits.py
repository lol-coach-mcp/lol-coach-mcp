"""반복 습관 탐지 (순수 함수, I/O 없음). 입력은 [(match, timeline)] 목록과 puuid."""

from __future__ import annotations

from collections import Counter

from lol_coach_mcp import review

BUCKET_MS = 5 * 60 * 1000
LAST_BUCKET_START_MIN = 35
TOP_ZONE_LIMIT = 3
FIRST_BACK_AFTER_MS = 90_000
MIN_TIMELINE_GAMES = 5
MIN_WIN_COMPARE_GAMES = 3

STATUS_OK = "ok"
STATUS_NO_TIMELINE = "판단 보류(타임라인 없음)"
STATUS_FEW = "판단 보류(표본 부족)"
STATUS_HOLD = "판단 보류"

COMPARE_KEYS = (
    "deaths_per_game",
    "pre_objective_death_rate",
    "consecutive_death_game_rate",
    "first_back_avg_s",
    "solo_death_rate",
)


def bucket_labels() -> list[str]:
    last = LAST_BUCKET_START_MIN
    step = BUCKET_MS // 60_000
    labels = [f"{m}-{m + step}분" for m in range(0, last, step)]
    return labels + [f"{last}분+"]


def _bucket_index(ms: int) -> int:
    return min(int(ms) // BUCKET_MS, len(bucket_labels()) - 1)


def first_back_ms(timeline: dict, puuid: str) -> int | None:
    """내 첫 ITEM_PURCHASED 중 시작 아이템 이후(90초 초과)의 첫 시각."""
    pid = review.participant_id(timeline, puuid)
    times = [
        int(e["timestamp"])
        for frame in timeline["info"]["frames"]
        for e in frame.get("events", [])
        if e["type"] == "ITEM_PURCHASED"
        and e.get("participantId") == pid
        and int(e["timestamp"]) > FIRST_BACK_AFTER_MS
    ]
    return min(times) if times else None


def _rate(num: int, den: int) -> float | None:
    return num / den if den else None


def _is_win(match: dict, puuid: str) -> bool:
    return bool(review._me(match, puuid)["win"])


def _metrics(games: list[tuple[dict, dict]], puuid: str) -> dict:
    """게임 목록 전체에 대한 습관 지표와 근거 횟수."""
    bucket_counts = [0] * len(bucket_labels())
    zones: Counter[str] = Counter()
    total = pre_obj = solo = consec_games = 0
    backs: list[int] = []
    for match, timeline in games:
        ds = review.deaths(match, timeline, puuid)
        total += len(ds)
        if any(review.TAG_CONSECUTIVE in d["tags"] for d in ds):
            consec_games += 1
        for d in ds:
            bucket_counts[_bucket_index(d["ms"])] += 1
            zones[d["zone"]] += 1
            if review.TAG_BEFORE_OBJECTIVE in d["tags"]:
                pre_obj += 1
            if review.TAG_SOLO in d["tags"]:
                solo += 1
        fb = first_back_ms(timeline, puuid)
        if fb is not None:
            backs.append(fb)
    n = len(games)
    avg_s = round(sum(backs) / len(backs) / 1000) if backs else None
    return {
        "games": n,
        "deaths_total": total,
        "deaths_per_game": round(total / n, 2) if n else None,
        "death_time_buckets": [
            {"bucket": lb, "deaths": c} for lb, c in zip(bucket_labels(), bucket_counts)
        ],
        "top_death_zones": [
            {"zone": z, "deaths": c}
            for z, c in sorted(zones.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_ZONE_LIMIT]
        ],
        "pre_objective_deaths": pre_obj,
        "pre_objective_death_rate": _rate(pre_obj, total),
        "consecutive_death_games": consec_games,
        "consecutive_death_game_rate": _rate(consec_games, n),
        "first_back_games": len(backs),
        "first_back_avg_s": avg_s,
        "first_back_avg": review.mmss(avg_s * 1000) if avg_s is not None else None,
        "solo_deaths": solo,
        "solo_death_rate": _rate(solo, total),
    }


def _win_compare(games: list[tuple[dict, dict]], puuid: str, overall: dict) -> dict:
    wins = [g for g in games if _is_win(g[0], puuid)]
    losses = len(games) - len(wins)
    base = {"wins": len(wins), "losses": losses}
    if len(wins) < MIN_WIN_COMPARE_GAMES or losses < MIN_WIN_COMPARE_GAMES:
        return {**base, "status": STATUS_HOLD}
    win_m = _metrics(wins, puuid)
    diffs = {
        k: (
            None
            if win_m[k] is None or overall[k] is None
            else round(win_m[k] - overall[k], 4)
        )
        for k in COMPARE_KEYS
    }
    keep = ("games", "deaths_total", *COMPARE_KEYS)
    return {
        **base,
        "status": STATUS_OK,
        "win_games": {k: win_m[k] for k in keep},
        "all_games": {k: overall[k] for k in keep},
        "diff_win_minus_all": diffs,
    }


def detect_habits(games: list[tuple[dict, dict]], puuid: str) -> dict:
    """타임라인이 있는 경기들에서 반복 습관 지표를 계산함."""
    if not games:
        return {"status": STATUS_NO_TIMELINE, "games_with_timeline": 0, "deaths_total": 0,
                "death_time_buckets": [], "top_death_zones": [],
                "pre_objective_death_rate": None, "pre_objective_deaths": 0,
                "consecutive_death_game_rate": None, "consecutive_death_games": 0,
                "first_back_avg": None, "solo_death_rate": None, "solo_deaths": 0,
                "win_compare": {"status": STATUS_HOLD}}
    m = _metrics(games, puuid)
    status = STATUS_OK if len(games) >= MIN_TIMELINE_GAMES else STATUS_FEW
    win_compare = (
        _win_compare(games, puuid, m) if status == STATUS_OK else {"status": STATUS_HOLD}
    )
    return {
        "status": status,
        "games_with_timeline": m["games"],
        "deaths_total": m["deaths_total"],
        "death_time_buckets": m["death_time_buckets"],
        "top_death_zones": m["top_death_zones"],
        "pre_objective_deaths": m["pre_objective_deaths"],
        "pre_objective_death_rate": m["pre_objective_death_rate"],
        "consecutive_death_games": m["consecutive_death_games"],
        "consecutive_death_game_rate": m["consecutive_death_game_rate"],
        "first_back_avg": (
            None
            if m["first_back_avg_s"] is None
            else {"time": m["first_back_avg"], "seconds": m["first_back_avg_s"],
                  "games": m["first_back_games"]}
        ),
        "solo_deaths": m["solo_deaths"],
        "solo_death_rate": m["solo_death_rate"],
        "win_compare": win_compare,
    }
