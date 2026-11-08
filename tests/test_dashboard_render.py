from lol_coach_mcp.dashboard import render, svg

DISCLAIMER = (
    "lol-coach-mcp is not endorsed by Riot Games and does not reflect the views or "
    "opinions of Riot Games or anyone officially involved in producing or managing "
    "Riot Games properties. Riot Games and all associated properties are trademarks "
    "or registered trademarks of Riot Games, Inc."
)
XSS = "<script>alert(1)</script>"


def test_map_coordinate_transform():
    assert svg.map_to_pixel(0, 0, 500) == (0.0, 500.0)
    assert svg.map_to_pixel(14870, 14870, 500) == (500.0, 0.0)
    assert svg.map_to_pixel(7435, 7435, 500) == (250.0, 250.0)


def test_scatter_points_and_background():
    out = svg.scatter_on_image([{"x": 0, "y": 0, "label": "3:00"}], None, 400)
    assert 'cx="0.0" cy="400.0"' in out
    assert "3:00" in out
    assert "<rect" in out and "<image" not in out
    out2 = svg.scatter_on_image([], "data:image/png;base64,AAAA", 400)
    assert '<image href="data:image/png;base64,AAAA"' in out2


def test_scatter_escapes_label():
    out = svg.scatter_on_image([{"x": 1, "y": 1, "label": XSS}], None, 300)
    assert "<script>" not in out and "&lt;script&gt;" in out


def test_line_chart_escapes_and_draws():
    out = svg.line_chart(
        [{"label": XSS, "points": [(0, 1), (1, -2), (2, 3)]}],
        x_label=XSS, y_label="골드", y_zero_line=True,
    )
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
    assert "<polyline" in out
    assert "zero-line" in out
    assert svg.line_chart(
        [{"label": "a", "points": [(0, 1)]}], y_zero_line=False
    ).count("zero-line") == 0


def test_line_chart_empty_gives_empty_state():
    out = svg.line_chart([])
    assert "empty-state" in out and "<svg" not in out
    assert "empty-state" in svg.line_chart([{"label": "a", "points": []}])


def test_bar_chart():
    out = svg.bar_chart([(XSS, 3), ("b", 6)])
    assert "<script>" not in out and out.count("<rect") >= 2
    assert "empty-state" in svg.bar_chart([])
    assert svg.bar_chart([("a", 0)]).count("<svg") == 1  # zero values do not crash


def test_result_strip_colors():
    out = svg.result_strip(["승", "패", "다시하기"])
    assert "result-win" in out and "result-loss" in out and "result-remake" in out
    assert "empty-state" in svg.result_strip([])
    assert svg.result_strip(["승", "승"]).count("result-win") == 2


def test_deterministic():
    s = [{"label": "a", "points": [(0, 1.23456), (1, 2)]}]
    assert svg.line_chart(s) == svg.line_chart(s)


def test_page_structure_and_escape():
    page = render.render_page(
        XSS,
        [{"heading": XSS, "html": render.table([XSS], [[XSS, 1]])}],
        "2026-11-09 12:00 KST",
        [XSS],
    )
    assert page.startswith("<!DOCTYPE html>")
    assert "<script>" not in page
    assert DISCLAIMER in page
    assert "prefers-color-scheme: dark" in page
    assert ":root" in page and "viewport" in page
    assert "2026-11-09 12:00 KST" in page


def test_helpers():
    assert "<table" in render.table(["a"], [["x"]])
    assert "empty-state" in render.table(["a"], [])
    assert "<dl" in render.kv_list([("k", XSS)]) and "<script>" not in render.kv_list([("k", XSS)])
    assert "empty-state" in render.empty_state("없음")


def test_no_external_urls():
    page = render.render_page(
        "t",
        [
            {"heading": "h", "html": svg.line_chart([{"label": "a", "points": [(0, 1), (1, 2)]}])},
            {"heading": "m", "html": svg.scatter_on_image([{"x": 1, "y": 1, "label": "x"}], "data:image/png;base64,AAAA", 300)},
            {"heading": "b", "html": svg.bar_chart([("a", 1)]) + svg.result_strip(["승"])},
        ],
        "now",
        [],
    )
    assert "http" not in page.replace(DISCLAIMER, "")
