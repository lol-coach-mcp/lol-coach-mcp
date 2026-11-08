"""티어·디비전·LP를 그래프용 단일 축 값으로 환산 (표시용 변환이며 MMR 추정이 아님)."""
from __future__ import annotations

TIERS = ("IRON", "BRONZE", "SILVER", "GOLD", "PLATINUM", "EMERALD", "DIAMOND")
APEX_TIERS = ("MASTER", "GRANDMASTER", "CHALLENGER")
DIVISIONS = {"IV": 0, "III": 1, "II": 2, "I": 3}
TIER_SPAN = 400
DIVISION_SPAN = 100
APEX_BASE = len(TIERS) * TIER_SPAN


def ladder_value(tier: str, division: str, lp: int) -> int | None:
    """IRON IV 0LP = 0. 디비전 100, 티어 400 간격. 마스터 이상은 2800 + LP. 알 수 없으면 None."""
    tier = str(tier).upper()
    if tier in APEX_TIERS:
        return APEX_BASE + int(lp)
    if tier not in TIERS or str(division).upper() not in DIVISIONS:
        return None
    return TIERS.index(tier) * TIER_SPAN + DIVISIONS[str(division).upper()] * DIVISION_SPAN + int(lp)
