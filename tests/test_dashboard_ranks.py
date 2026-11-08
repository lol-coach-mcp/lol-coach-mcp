from lol_coach_mcp.dashboard.ranks import ladder_value


def test_division_steps_and_tier_steps():
    assert ladder_value("IRON", "IV", 0) == 0
    assert ladder_value("IRON", "III", 0) == 100
    assert ladder_value("IRON", "I", 99) == 399
    assert ladder_value("BRONZE", "IV", 0) == 400
    assert ladder_value("GOLD", "II", 76) == 3 * 400 + 200 + 76
    assert ladder_value("DIAMOND", "I", 0) == 6 * 400 + 300


def test_promotion_is_increasing_and_demotion_decreasing():
    assert ladder_value("PLATINUM", "IV", 12) > ladder_value("GOLD", "I", 95)
    assert ladder_value("GOLD", "I", 0) > ladder_value("GOLD", "II", 99)
    assert ladder_value("GOLD", "IV", 5) < ladder_value("GOLD", "I", 95) - 0
    assert ladder_value("SILVER", "I", 90) < ladder_value("GOLD", "IV", 0)


def test_apex_tiers_and_unknown():
    assert ladder_value("MASTER", "I", 120) == 2800 + 120
    assert ladder_value("CHALLENGER", "", 900) == 2800 + 900
    assert ladder_value("DIAMOND", "I", 99) < ladder_value("MASTER", "I", 0)
    assert ladder_value("WOOD", "I", 0) is None
    assert ladder_value("GOLD", "IX", 0) is None
