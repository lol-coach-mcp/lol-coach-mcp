import json
from dataclasses import replace

from lol_coach_mcp import goals
from test_server_form import _ms


def _goal(**kw):
    base = {
        "id": 1, "metric": "deaths", "direction": "down", "target": 3.0, "baseline_value": 5.0,
        "start_after_ms": 1000, "games_to_track": 3, "position": "BOTTOM", "status": "진행 중",
    }
    return {**base, **kw}


def _after(n, **kw):
    return [_ms(i, created=2000 + i, **kw) for i in range(n)]


def test_practice_catalog_covers_metrics_non_imperative():
    cat = goals.load_practice()
    assert set(cat) == {"cs_per_min", "vision_per_min", "deaths", "kill_participation",
                        "damage_share", "kda", "gold_per_min"}
    for items in cat.values():
        assert 2 <= len(items) <= 3
        for t in items:
            assert "도움이 될 수 있음" in t and "하세요" not in t and "하라" not in t
    json.dumps(cat, ensure_ascii=False)


def test_suggest_midpoint_rounding_and_direction():
    series = {"deaths": [6.0] * 10 + [5.0] * 10, "damage_share": [0.2] * 20}
    weak = [{"metric": "deaths", "ref_value": 3.1}, {"metric": "damage_share", "ref_value": 0.3}]
    s = goals.suggest_goal(weak, series)
    assert s == {"metric": "deaths", "direction": "down", "target": 4.3,
                 "baseline_value": 5.5, "games": 10}
    s = goals.suggest_goal(weak, series, metric="damage_share")
    assert s["target"] == 0.25 and s["direction"] == "up" and s["baseline_value"] == 0.2


def test_suggest_none_without_weakness_or_metric_match_or_win():
    series = {"deaths": [5.0] * 20, "win": [1.0] * 20}
    assert goals.suggest_goal([], series) is None
    assert goals.suggest_goal([{"metric": "win", "ref_value": 0.5}], series) is None
    assert goals.suggest_goal([{"metric": "deaths", "ref_value": 3.0}], series, metric="kda") is None


def test_evaluate_in_progress_with_change():
    r = goals.evaluate_goal(_goal(), _after(2, deaths=4))
    assert r["status"] == "진행 중" and r["games_done"] == 2 and r["games_needed"] == 3
    assert r["current_avg"] == 4.0 and r["change_vs_baseline"] == -1.0


def test_evaluate_achieved_missed_down_and_up():
    assert goals.evaluate_goal(_goal(), _after(3, deaths=3))["status"] == "달성"
    assert goals.evaluate_goal(_goal(), _after(3, deaths=4))["status"] == "미달"
    up = _goal(metric="kill_participation", direction="up", target=0.6, baseline_value=0.5)
    assert goals.evaluate_goal(up, _after(3, kp=0.6))["status"] == "달성"
    assert goals.evaluate_goal(up, _after(3, kp=0.55))["status"] == "미달"


def test_evaluate_filters_and_uses_first_n():
    stats = [
        replace(_ms(1, deaths=9, created=500)),                    # 시작 이전
        _ms(2, deaths=9, created=3000, position="TOP"),            # 다른 포지션
        _ms(3, deaths=9, created=3001, queue_id=450),              # 다른 큐
        replace(_ms(4, deaths=9, created=3002), is_remake=True),   # 다시하기
        _ms(5, deaths=1, created=4000), _ms(6, deaths=2, created=4001),
        _ms(7, deaths=3, created=4002), _ms(8, deaths=9, created=4003),
    ]
    r = goals.evaluate_goal(_goal(), stats)
    assert r["games_done"] == 3 and r["current_avg"] == 2.0 and r["status"] == "달성"


def test_evaluate_no_games():
    r = goals.evaluate_goal(_goal(), [])
    assert r["status"] == "진행 중" and r["games_done"] == 0
    assert r["current_avg"] is None and r["change_vs_baseline"] is None
