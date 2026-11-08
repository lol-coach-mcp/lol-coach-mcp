"""지표 설명 (순수 함수): 정의·공식, 티어 순서, 주제 해석."""

from __future__ import annotations

from lol_coach_mcp import analysis

TIER_ORDER = (
    "IRON", "BRONZE", "SILVER", "GOLD", "PLATINUM", "EMERALD", "DIAMOND",
    "MASTER", "GRANDMASTER", "CHALLENGER",
)
TOPIC_TIER_BASELINES = "tier_baselines"
TOPIC_NEXT_TIER = "next_tier"
SPECIAL_TOPICS = (TOPIC_TIER_BASELINES, TOPIC_NEXT_TIER)
RECENT_GAMES = 20
NO_BASELINE = "기준선 없음"

# 지표 키 -> (정의, 공식)
DEFINITIONS: dict[str, tuple[str, str]] = {
    "kda": ("킬과 어시스트 합을 데스로 나눈 값. 데스가 0이면 킬+어시스트",
            "(킬 + 어시스트) / max(데스, 1)"),
    "cs_per_min": ("게임 시간 1분당 처치한 미니언·중립 몬스터 수",
                   "(미니언 + 중립 몬스터 처치 수) / (게임 시간(초) / 60)"),
    "gold_per_min": ("게임 시간 1분당 획득한 골드", "획득 골드 / (게임 시간(초) / 60)"),
    "vision_per_min": ("게임 시간 1분당 시야 점수", "시야 점수 / (게임 시간(초) / 60)"),
    "damage_share": ("우리 팀이 챔피언에게 가한 피해 중 내가 차지한 비중",
                     "내 챔피언 대상 피해량 / 팀 전체 챔피언 대상 피해량"),
    "kill_participation": ("우리 팀 킬 중 내가 킬 또는 어시스트로 관여한 비중",
                           "(킬 + 어시스트) / 팀 전체 킬"),
    "deaths": ("한 판에서 사망한 횟수", "판당 데스 수"),
    "win": ("승리한 판의 비율", "승리 판 수 / 전체 판 수 (한 판 기준 승리 1, 패배 0)"),
}


def next_tier(tier: str | None) -> str | None:
    if not tier:
        return None
    t = tier.upper()
    if t not in TIER_ORDER:
        return None
    i = TIER_ORDER.index(t)
    return TIER_ORDER[i + 1] if i + 1 < len(TIER_ORDER) else None


def resolve_topic(topic: str) -> str | None:
    """지표 키(대소문자 무시)·한국어 라벨·특수 주제를 정규화함. 모르면 None."""
    t = topic.strip()
    low = t.lower()
    if low in SPECIAL_TOPICS:
        return low
    for key, (label, _) in analysis.METRICS.items():
        if low == key or low == label.lower():
            return key
    return None


def valid_topics_text() -> str:
    metrics = ", ".join(f"{k}({label})" for k, (label, _) in analysis.METRICS.items())
    return f"{metrics}, {TOPIC_TIER_BASELINES}, {TOPIC_NEXT_TIER}"
