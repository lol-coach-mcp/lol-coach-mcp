"""일지 대조 (순수 함수): 키워드 → 연결된 경기의 근거 데이터. 판정 문장은 만들지 않음."""

from __future__ import annotations

import re

from lol_coach_mcp import review
from lol_coach_mcp.stats import MatchStats

# (주제, 라벨, 키워드) - 소문자 부분 일치, 여러 개 동시 가능
KEYWORD_TABLE: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("jungle", "갱/정글", ("갱", "정글")),
    ("farming", "CS/파밍", ("cs", "파밍")),
    ("vision", "시야/와드", ("시야", "와드")),
    ("lane", "라인전", ("라인전",)),
    ("teamfight", "한타", ("한타",)),
    ("damage", "딜", ("딜",)),
)
# 키워드를 포함하지만 다른 뜻인 단어는 매칭 전에 제거
FALSE_POSITIVE_WORDS = ("딜레이", "딜러", "갱신")
# 영문 키워드는 독립 토큰으로만 매칭 (ics, abcs, csv 제외; cs10 같은 숫자 접미는 허용)
STANDALONE_KEYWORDS = ("cs",)
TOPIC_LABELS = {t: label for t, label, _ in KEYWORD_TABLE}
TOPIC_LABELS["default"] = "기본 지표"
TIMELINE_TOPICS = ("jungle", "farming", "vision", "lane", "teamfight")

EARLY_END_MS = 900_000  # 초반 데스: 15분 미만
LATE_START_MS = 1_500_000  # 후반 데스: 25분 이상
JUNGLE_ZONES = ("위쪽 정글", "아래쪽 정글")
CS_CHECK_MINUTE = 10
LANE_MINUTES = review.CHECKPOINT_MINUTES


def match_topics(text: str) -> list[str]:
    low = text.lower()
    for w in FALSE_POSITIVE_WORDS:
        low = low.replace(w, " ")
    return [t for t, _, kws in KEYWORD_TABLE if any(_has_keyword(low, k) for k in kws)]


def _has_keyword(low: str, kw: str) -> bool:
    if kw in STANDALONE_KEYWORDS:
        return re.search(rf"(?<![a-z0-9]){re.escape(kw)}(?![a-z])", low) is not None
    return kw in low


def is_jungle_zone(zone: str) -> bool:
    return zone in JUNGLE_ZONES


def _per_min(value: float, s: MatchStats) -> float:
    return round(value / (s.duration_s / 60), 2) if s.duration_s > 0 else 0.0


def _cs_at(timeline: dict, puuid: str, minute: int) -> int | None:
    frames = timeline["info"]["frames"]
    if minute >= len(frames):
        return None
    pid = review.participant_id(timeline, puuid)
    return review.cs_of(frames[minute]["participantFrames"][str(pid)])


def build_bundle(
    topic: str, s: MatchStats, match: dict, timeline: dict | None, puuid: str
) -> dict:
    """주제별 근거 묶음. 타임라인이 필요한 항목은 타임라인이 없으면 None."""
    if topic == "jungle":
        out: dict = {"early_deaths": None, "jungle_zone_deaths": None, "deaths": None}
        if timeline is not None:
            early = [d for d in review.deaths(match, timeline, puuid) if d["ms"] < EARLY_END_MS]
            rows = [
                {
                    "time": d["time"],
                    "zone": d["zone"],
                    "jungle_zone": is_jungle_zone(d["zone"]),
                    "killer_champion": d["killer_champion"],
                }
                for d in early
            ]
            out = {
                "early_deaths": len(rows),
                "jungle_zone_deaths": sum(r["jungle_zone"] for r in rows),
                "deaths": rows,
            }
        return out
    if topic == "farming":
        return {
            "cs_per_min": round(s.cs / (s.duration_s / 60), 2) if s.duration_s > 0 else 0.0,
            "cs_at_10": None if timeline is None else _cs_at(timeline, puuid, CS_CHECK_MINUTE),
        }
    if topic == "vision":
        out = {
            "vision_score": s.vision_score,
            "vision_per_min": _per_min(s.vision_score, s),
            "wards_placed": None,
            "control_wards": None,
            "wards_killed": None,
        }
        if timeline is not None:
            v = review.vision(match, timeline, puuid)
            out["wards_placed"] = sum(v["wards_placed"].values())
            out["control_wards"] = v["control_wards"]
            out["wards_killed"] = v["wards_killed"]
        return out
    if topic == "lane":
        if timeline is None:
            return {"opponent_champion": None, "status": None, "points": None}
        lane = review.lane_checkpoints(match, timeline, puuid, LANE_MINUTES)
        return {
            "opponent_champion": lane["opponent_champion"],
            "status": lane["status"],
            "points": lane["points"],
        }
    if topic == "teamfight":
        out = {"kill_participation": s.kill_participation, "late_deaths": None}
        if timeline is not None:
            out["late_deaths"] = sum(
                1 for d in review.deaths(match, timeline, puuid) if d["ms"] >= LATE_START_MS
            )
        return out
    if topic == "damage":
        return {"damage_share": s.damage_share}
    return {
        "kda": f"{s.kills}/{s.deaths}/{s.assists}",
        "cs_per_min": round(s.cs / (s.duration_s / 60), 2) if s.duration_s > 0 else 0.0,
        "vision_per_min": _per_min(s.vision_score, s),
        "damage_share": s.damage_share,
        "kill_participation": s.kill_participation,
    }


def build_evidence(
    text: str, s: MatchStats, match: dict, timeline: dict | None, puuid: str
) -> dict:
    topics = match_topics(text)
    keys = topics or ["default"]
    return {
        "match_id": s.match_id,
        "champion": s.champion,
        "position": s.position,
        "topics": topics,
        "bundles": {k: build_bundle(k, s, match, timeline, puuid) for k in keys},
    }
