import math
from dataclasses import replace

import pytest

from lol_coach_mcp import analysis
from lol_coach_mcp.analysis import (
    METRICS,
    MIN_GAMES,
    compare_metric,
    findings,
    main_position,
    metric_series,
    personal_records,
    style_labels,
    summary,
    tilt_signals,
)
from lol_coach_mcp.stats import MatchStats



def _ms(i, *, created=None, win=True, position="MIDDLE", deaths=2, kills=5, assists=5,
        cs=210, duration_s=1800, gold=12000, vision=30, dmg_share=0.25, kp=0.5,
        champion="Ahri", queue_id=420, is_remake=False, patch="16.22"):
    return MatchStats(
        match_id=f"KR_{i:010d}", queue_id=queue_id,
        game_creation=created if created is not None else 1_000_000_000_000 + i * 3_600_000,
        duration_s=duration_s, patch=patch, is_remake=is_remake, win=win, position=position,
        champion=champion, kills=kills, deaths=deaths, assists=assists, cs=cs,
        cs_per_min=round(cs / (duration_s / 60), 1), gold=gold, vision_score=vision,
        damage_to_champions=20000, damage_share=dmg_share, kill_participation=kp,
        team_id=100, teammates=("a", "b", "c", "d"),
    )


def test_metrics_table():
    assert set(METRICS) == {"kda", "cs_per_min", "gold_per_min", "vision_per_min",
                            "damage_share", "kill_participation", "deaths", "win"}
    assert METRICS["deaths"] == ("데스", False)
    assert METRICS["win"] == ("승률", True)
    assert MIN_GAMES == 10 and analysis.MIN_REF_GAMES == 10


def test_metric_series_derives_per_minute():
    s = [_ms(1, cs=200, duration_s=1500, gold=15000, vision=50, deaths=0, kills=3, assists=4,
             win=False)]
    m = metric_series(s)
    assert m["cs_per_min"] == [pytest.approx(8.0)]
    assert m["gold_per_min"] == [pytest.approx(600.0)]
    assert m["vision_per_min"] == [pytest.approx(2.0)]
    assert m["kda"] == [7.0]
    assert m["deaths"] == [0.0]
    assert m["win"] == [0.0]
    assert m["damage_share"] == [0.25]
    assert metric_series([]) == {k: [] for k in METRICS}


def test_summary():
    assert summary([1, 2, 3, 4]) == {"mean": 2.5, "std": pytest.approx(1.290994, abs=1e-6), "n": 4}
    assert summary([5]) == {"mean": 5, "std": 0.0, "n": 1}
    assert summary([]) == {"mean": None, "std": None, "n": 0}


MINE = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]  # mean 5.5, sd 3.02765, n 10 -> 1.96*sd/sqrt(n)=1.876557


def test_margin_mean_only_ref_clear_not_strong():
    row = compare_metric("kda", MINE, {"mean": 3.0, "n": 100}, "기준선", "MIDDLE")
    assert row["status"] == "ok"
    assert row["margin"] == pytest.approx(1.88, abs=1e-9)
    assert row["difference"] == 2.5
    assert row["clear_gap"] is True and row["strong_gap"] is False
    assert row["my_sample"] == 10 and row["ref_sample"] == 100
    assert row["ref_source"] == "기준선" and row["label"] == "KDA"
    assert row["higher_is_better"] is True


def test_margin_strong_gap():
    row = compare_metric("kda", MINE, {"mean": 1.5, "std": None, "n": 100}, "기준선", "MIDDLE")
    assert row["difference"] == 4.0 and row["strong_gap"] is True and row["clear_gap"] is True


def test_margin_with_ref_sd():
    # hypot(1.876557, 1.96*2/sqrt(100)=0.392) = 1.917060
    row = compare_metric("kda", MINE, {"mean": 3.6, "std": 2.0, "n": 100}, "기준선", "MIDDLE")
    assert row["margin"] == 1.92
    assert row["clear_gap"] is False  # 1.9 < 1.917
    row2 = compare_metric("kda", MINE, {"mean": 3.5, "std": 2.0, "n": 100}, "기준선", "MIDDLE")
    assert row2["clear_gap"] is True  # 2.0 > 1.917


def test_margin_with_list_ref():
    ref = [x + 3 for x in MINE]  # same sd, mean 8.5 -> margin hypot(t, t) = 2.653829
    row = compare_metric("kda", MINE, ref, "내 승리 판 평균", "MIDDLE")
    assert row["margin"] == 2.65
    assert row["difference"] == -3.0
    assert row["clear_gap"] is True and row["strong_gap"] is False
    assert row["ref_sample"] == 10


def test_sample_shortage():
    row = compare_metric("kda", MINE[:9], {"mean": 0.0, "n": 100}, "기준선", "MIDDLE")
    assert row["status"] == "판단 보류(표본 부족)"
    assert row["clear_gap"] is False and row["strong_gap"] is False
    assert row["my_value"] == 5.0 and row["my_sample"] == 9
    row2 = compare_metric("kda", MINE, MINE[:9], "내 승리 판 평균", "MIDDLE")
    assert row2["status"] == "판단 보류(표본 부족)"
    assert findings([row, row2]) == {"weaknesses": [], "strengths": []}


def test_no_baseline_and_not_comparable():
    row = compare_metric("kda", MINE, None, "티어 기준선", "MIDDLE")
    assert row["status"] == "기준선 없음"
    assert row["ref_value"] is None and row["difference"] is None and row["margin"] is None
    row2 = compare_metric("kda", MINE, [], "내 승리 판 평균", "MIDDLE")
    assert row2["status"] == "비교 불가"
    assert row2["clear_gap"] is False


def test_support_cs_gold_reference_only():
    for metric in ("cs_per_min", "gold_per_min"):
        row = compare_metric(metric, [1.0] * 20, {"mean": 7.0, "n": 100}, "기준선", "UTILITY")
        assert row["status"] == "참고용"
        assert row["clear_gap"] is False
        assert row["my_value"] == 1.0
    row = compare_metric("cs_per_min", [1.0] * 20, {"mean": 7.0, "n": 100}, "기준선", "BOTTOM")
    assert row["status"] == "ok" and row["clear_gap"] is True


def test_direction_and_findings_sort():
    ten = lambda v: [v] * 10  # sd 0 -> margin 0
    rows = [
        compare_metric("deaths", [6.0] * 5 + [4.0] * 5, {"mean": 3.0, "n": 100}, "b", "MIDDLE"),
        compare_metric("kda", [2.0] * 5 + [4.0] * 5, {"mean": 4.0, "n": 100}, "b", "MIDDLE"),
        compare_metric("cs_per_min", ten(5.0), {"mean": 6.0, "n": 100}, "b", "MIDDLE"),
        compare_metric("vision_per_min", ten(1.5), {"mean": 1.0, "n": 100}, "b", "MIDDLE"),
        compare_metric("win", [1.0] * 6 + [0.0] * 4, {"mean": 0.5, "n": 100}, "b", "MIDDLE"),
    ]
    by = {r["metric"]: r for r in rows}
    # deaths higher than ref -> weakness (lower is better)
    assert by["deaths"]["difference"] == 2.0 and by["deaths"]["clear_gap"]
    f = findings(rows)
    weak = [r["metric"] for r in f["weaknesses"]]
    strong = [r["metric"] for r in f["strengths"]]
    assert "vision_per_min" in strong and "win" not in strong  # win gap not clear
    # cs (margin 0, strong) first; deaths z=2/0.653=3.06 strong; kda z=1/0.653=1.53 clear only
    assert weak[0] == "cs_per_min"
    assert weak[1] == "deaths"
    assert weak[2] == "kda"
    assert by["kda"]["strong_gap"] is False and by["deaths"]["strong_gap"] is True


def test_findings_top3():
    rows = [compare_metric(m, [1.0] * 10, {"mean": 2.0 + i, "n": 100}, "b", "MIDDLE")
            for i, m in enumerate(["kda", "cs_per_min", "gold_per_min", "vision_per_min"])]
    f = findings(rows)
    assert len(f["weaknesses"]) == 3 and f["strengths"] == []


def test_rounding_rates():
    row = compare_metric("damage_share", [0.2512] * 10, {"mean": 0.20001, "n": 100}, "b", "MIDDLE")
    assert row["my_value"] == 0.251 and row["ref_value"] == 0.2 and row["difference"] == 0.051


def test_main_position_tie_breaks_by_recent():
    s = [_ms(1, position="TOP"), _ms(2, position="MIDDLE"), _ms(3, position="MIDDLE"),
         _ms(4, position="TOP")]
    assert main_position(s) == "TOP"
    assert main_position(s[:3]) == "MIDDLE"
    assert main_position([]) == "UNKNOWN"


def test_tilt_signals():
    # chronological: W W L L L ; deaths rise in second half; last 3 short
    s = [
        _ms(1, win=True, deaths=1, duration_s=2400),
        _ms(2, win=True, deaths=1, duration_s=2400),
        _ms(3, win=False, deaths=4, duration_s=1200),
        _ms(4, win=False, deaths=5, duration_s=1200),
        _ms(5, win=False, deaths=5, duration_s=1200),
    ]
    sig = tilt_signals(list(reversed(s)))
    assert sig == ["3연패 이상", "후반부 데스 증가", "최근 게임 시간 단축"]
    calm = [_ms(i, win=(i % 2 == 0)) for i in range(1, 7)]
    assert tilt_signals(calm) == []
    assert tilt_signals([]) == []


def test_style_labels():
    s = [_ms(i, kp=0.65, dmg_share=0.30, vision=45, cs=240, duration_s=1800, deaths=2)
         for i in range(5)]
    assert style_labels(s, "MIDDLE") == ["교전 참여형", "딜 중심형", "시야 장악형", "성장형", "안정형"]
    sup = [replace(x, position="UTILITY") for x in s]
    # support: vision 1.5/min < 2.0, cs label excluded
    assert style_labels(sup, "UTILITY") == ["교전 참여형", "딜 중심형", "안정형"]
    plain = [_ms(i, kp=0.4, dmg_share=0.2, vision=10, cs=150, deaths=6) for i in range(5)]
    assert style_labels(plain, "MIDDLE") == ["뚜렷한 성향 없음"]


def test_personal_records():
    a = _ms(1, kills=10, deaths=1, assists=10, cs=270, dmg_share=0.35, vision=40)
    b = _ms(2, kills=2, deaths=0, assists=3, cs=150, win=True, duration_s=2100, vision=20)
    sup = _ms(3, position="UTILITY", cs=400, vision=90, deaths=3)
    other_q = _ms(4, queue_id=440, kills=50, deaths=0)
    remake = _ms(5, is_remake=True, vision=200)
    latest = _ms(6, kills=30, deaths=1, assists=10, cs=100)
    rec = personal_records([a, b, sup, other_q, remake, latest], latest)
    assert rec["best_kda"]["match_id"] == latest.match_id and rec["best_kda"]["value"] == 40.0
    assert rec["best_cs_per_min"]["match_id"] == a.match_id  # support excluded
    assert rec["best_cs_per_min"]["value"] == 9.0
    assert rec["best_damage_share"]["match_id"] == a.match_id
    assert rec["most_vision_score"]["match_id"] == sup.match_id
    assert rec["longest_deathless_win"]["match_id"] == b.match_id
    assert rec["longest_deathless_win"]["value"] == 2100
    assert rec["updated_by_latest"] == ["best_kda"]
    assert rec["best_kda"]["label"] == "최고 KDA"


def test_personal_records_tie_not_updated_and_empty():
    a = _ms(1, kills=5, deaths=1, assists=5)
    latest = _ms(2, kills=5, deaths=1, assists=5)
    rec = personal_records([a, latest], latest)
    assert rec["best_kda"]["match_id"] == a.match_id
    assert "best_kda" not in rec["updated_by_latest"]
    empty = personal_records([], None)
    assert empty["best_kda"] is None and empty["longest_deathless_win"] is None
    assert empty["updated_by_latest"] == []


def test_margin_hand_value():
    t = 1.96 * 3.0276503540974917 / math.sqrt(10)
    assert t == pytest.approx(1.876557, abs=1e-6)


def test_evidence_row_keys():
    row = compare_metric("win", [1.0] * 10, [0.0] * 10, "내 승리 판 평균", "MIDDLE")
    assert set(row) == {"metric", "label", "higher_is_better", "my_value", "my_sample",
                        "ref_value", "ref_sample", "ref_source", "difference", "margin",
                        "clear_gap", "strong_gap", "status"}
