from lol_coach_mcp.dashboard import views
from lol_coach_mcp.dashboard.render import SafeHtml


def test_moving_average_full_windows_only():
    assert views.moving_average([1, 2, 3, 4], 2) == [1.5, 2.5, 3.5]
    assert views.moving_average([1, 2], 3) == []
    assert views.moving_average([], 3) == []


def test_ma_series_x_is_game_number_of_window_end():
    s = views.ma_series("KDA", [1.0, 2.0, 3.0, 4.0], 2)
    assert s["label"] == "KDA" and s["points"] == [(2, 1.5), (3, 2.5), (4, 3.5)]


def test_gap_label():
    assert views.gap_label({"status": "ok", "strong_gap": True, "clear_gap": True}) == "큰 차이"
    assert views.gap_label({"status": "ok", "strong_gap": False, "clear_gap": True}) == "뚜렷한 차이"
    assert views.gap_label({"status": "ok", "strong_gap": False, "clear_gap": False}) == "뚜렷하지 않은 차이"
    assert views.gap_label({"status": "기준선 없음", "strong_gap": False, "clear_gap": False}) == "기준선 없음"


def test_match_view_no_timeline_shows_message_not_chart():
    secs = views.match_view(
        summary=[("챔피언", "카이사")], gold_points=None, death_points=None,
        minimap_uri=None, objective_events=None,
    )
    html = "".join(str(s["html"]) for s in secs)
    assert "타임라인 데이터 없음" in html and "<polyline" not in html and "<circle" not in html
    assert all(isinstance(s["html"], SafeHtml) for s in secs)
