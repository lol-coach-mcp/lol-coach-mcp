from dataclasses import replace

import httpx
import pytest
from mcp import Client

from lol_coach_mcp import champions
from lol_coach_mcp.config import Settings
from lol_coach_mcp.server import AppState, create_server
from test_server_form import EmptyRiot, _ms
from test_server_profile import RIOT_ID, store  # noqa: F401

DD = {
    "Kaisa": {"id": 145, "name": "카이사", "tags": ["Marksman"]},
    "Jinx": {"id": 222, "name": "징크스", "tags": ["Marksman"]},
    "Ezreal": {"id": 81, "name": "이즈리얼", "tags": ["Marksman", "Mage"]},
    "Lucian": {"id": 236, "name": "루시안", "tags": ["Marksman"]},
    "Draven": {"id": 119, "name": "드레이븐", "tags": ["Marksman"]},
    "Caitlyn": {"id": 51, "name": "케이틀린", "tags": ["Marksman"]},
    "MonkeyKing": {"id": 62, "name": "오공", "tags": ["Fighter", "Tank"]},
    "Nunu": {"id": 20, "name": "누누와 윌럼프", "tags": ["Tank", "Mage"]},
    "Swain": {"id": 50, "name": "스웨인", "tags": ["Mage", "Fighter"]},
}
LANE_META = [
    {"champion": "자야", "tier": 1, "rank": 1, "win_rate": 0.52, "pick_rate": 0.1, "kda": 2.3, "play": 100},
    {"champion": "징크스", "tier": 1, "rank": 2, "win_rate": 0.51, "pick_rate": 0.1, "kda": 2.2, "play": 100},
    {"champion": "카이사", "tier": 1, "rank": 3, "win_rate": 0.49, "pick_rate": 0.2, "kda": 2.4, "play": 100},
    {"champion": "드레이븐", "tier": 1, "rank": 4, "win_rate": 0.5, "pick_rate": 0.1, "kda": 2.4, "play": 100},
    {"champion": "스웨인", "tier": 2, "rank": 5, "win_rate": 0.54, "pick_rate": 0.01, "kda": 3.0, "play": 100},
    {"champion": "케이틀린", "tier": 2, "rank": 6, "win_rate": 0.5, "pick_rate": 0.1, "kda": 2.0, "play": 100},
    {"champion": "루시안", "tier": 3, "rank": 7, "win_rate": 0.5, "pick_rate": 0.1, "kda": 2.0, "play": 100},
    {"champion": "이즈리얼", "tier": 2, "rank": 8, "win_rate": 0.49, "pick_rate": 0.1, "kda": 2.0, "play": 100},
]
ANALYSIS = {
    "summary": {"average_stats": {"play": 132812, "win_rate": 0.49, "pick_rate": 0.22,
                                  "ban_rate": 0.04, "kda": 2.38, "tier": 1, "rank": 16}},
    "strong_counters": [{"champion_id": 81, "champion_name": "이즈리얼", "play": 5755, "win_rate": 0.54},
                        {"champion_id": 800, "champion_name": "멜", "play": 196, "win_rate": 0.54},
                        {"champion_id": 236, "champion_name": "루시안", "play": 2474, "win_rate": 0.52},
                        {"champion_id": 1, "champion_name": "넷째", "play": 1, "win_rate": 0.5}],
    "weak_counters": [{"champion_id": 50, "champion_name": "스웨인", "play": 220, "win_rate": 0.69},
                      {"champion_id": 96, "champion_name": "코그모", "play": 809, "win_rate": 0.57},
                      {"champion_id": 221, "champion_name": "제리", "play": 1036, "win_rate": 0.54}],
}


class FakeOpgg:
    def __init__(self, analysis=ANALYSIS, meta=LANE_META):
        self.analysis, self.meta = analysis, meta
        self.analysis_calls: list[tuple] = []
        self.meta_calls: list[str] = []

    async def champion_analysis(self, key, position, tier):
        self.analysis_calls.append((key, position, tier))
        return self.analysis

    async def lane_meta(self, position):
        self.meta_calls.append(position)
        return self.meta


class FakeDD:
    def __init__(self, data=DD, error=None):
        self.data, self.error = data, error

    async def champions(self):
        if self.error:
            raise self.error
        return self.data


def make(store, riot, opgg, dd):
    settings = Settings(riot_api_key="k", data_dir=None, user_agent="t")
    return create_server(lambda: AppState(settings=settings, riot=riot, store=store,
                                          opgg=opgg, ddragon=dd))


def _games(spec, *, start=0):
    """spec: [(챔피언, 판수, 승리 판수)] → 시간순 MatchStats."""
    out, i = [], start
    for champ, n, wins in spec:
        for k in range(n):
            out.append(replace(_ms(i, win=k < wins, position="BOTTOM", kills=6, deaths=3,
                                   assists=6), champion=champ))
            i += 1
    return out


async def _run(store, stats, args, *, opgg=None, dd=None, riot=None):
    for s in stats:
        store.save_match(s.match_id, {}, s)
    opgg = FakeOpgg() if opgg is None else opgg
    dd = FakeDD() if dd is None else dd
    async with Client(make(store, riot or EmptyRiot(), opgg, dd)) as c:
        await c.call_tool("my_profile", {"action": "set", "riot_id": RIOT_ID})
        return await c.call_tool("champion_report", args)


SPEC = [("Kaisa", 12, 8), ("Jinx", 8, 4), ("Ezreal", 6, 3), ("MonkeyKing", 5, 2),
        ("Lucian", 4, 2), ("Draven", 3, 1), ("Nunu", 2, 1)]


async def test_listed_with_schema_and_requires_profile(store):
    async with Client(make(store, EmptyRiot(), FakeOpgg(), FakeDD())) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
        r = await c.call_tool("champion_report", {})
    assert tools["champion_report"].output_schema
    assert r.is_error and "my_profile" in r.content[0].text


async def test_overview_opgg_comparison_limit_and_key_mapping(store):
    opgg = FakeOpgg()
    r = await _run(store, _games(SPEC), {"count": 50}, opgg=opgg)
    assert not r.is_error
    sc = r.structured_content
    rows = {x["champion"]: x for x in sc["rows"]}
    assert [x["champion"] for x in sc["rows"]][:3] == ["Kaisa", "Jinx", "Ezreal"]
    # OP.GG는 판수 상위 5개만, 영문 키 대문자(MonkeyKing → MONKEYKING), 티어는 소문자
    assert [c[0] for c in opgg.analysis_calls] == ["KAISA", "JINX", "EZREAL", "MONKEYKING", "LUCIAN"]
    assert {c[1:] for c in opgg.analysis_calls} == {("adc", "gold")}
    assert rows["Draven"]["opgg"] is None and rows["Nunu"]["opgg"] is None
    assert any("상위 5개" in n for n in sc["notes"])
    k = rows["Kaisa"]
    assert k["games"] == 12 and k["win_rate"] == 0.667 and k["name"] == "카이사"
    assert k["kda"] == 4.0 and k["opgg"]["win_rate_diff"] == 0.177
    assert k["opgg"]["kda_diff"] == 1.62
    assert sc["tier_basis"] == "GOLD 티어 기준"
    # 판수 < 5 → 판단 보류, OP.GG 차이 없음
    assert rows["Lucian"]["status"] == "판단 보류" and rows["Lucian"]["opgg"]["win_rate_diff"] is None
    # 주력 후보 근거: 판수 ≥ 10 (Kaisa만)
    assert [c["champion"] for c in sc["main_candidates"]] == ["Kaisa"]
    assert sc["main_candidates"][0]["evidence"] == ["내 승률이 OP.GG 평균 이상", "내 KDA가 OP.GG 평균 이상"]
    text = r.content[0].text
    assert "솔로랭크" in text and "GOLD 티어 기준" in text
    assert "하세요" not in text and "하라" not in text


async def test_candidate_excluded_when_below_opgg(store):
    spec = [("Kaisa", 12, 3)]
    stats = [replace(s, kills=0, deaths=5, assists=1) for s in _games(spec)]
    r = await _run(store, stats, {})
    assert r.structured_content["main_candidates"] == []


class DownOpgg(FakeOpgg):
    async def champion_analysis(self, key, position, tier):
        return None

    async def lane_meta(self, position):
        return None


async def test_opgg_down_keeps_my_data(store):
    r = await _run(store, _games(SPEC), {}, opgg=DownOpgg())
    sc = r.structured_content
    assert not r.is_error
    k = sc["rows"][0]
    assert k["games"] == 12 and k["win_rate"] == 0.667
    assert k["opgg"] == {"status": "OP.GG 데이터 없음"}
    assert sc["main_candidates"] == []
    assert "OP.GG 데이터 없음" in r.content[0].text


async def test_opgg_not_configured(store):
    for s in _games(SPEC):
        store.save_match(s.match_id, {}, s)
    settings = Settings(riot_api_key="k", data_dir=None, user_agent="t")
    server = create_server(lambda: AppState(settings=settings, riot=EmptyRiot(), store=store))
    async with Client(server) as c:
        await c.call_tool("my_profile", {"action": "set", "riot_id": RIOT_ID})
        r = await c.call_tool("champion_report", {})
    assert not r.is_error
    assert r.structured_content["rows"][0]["opgg"]["status"] == "OP.GG 데이터 없음"


async def test_unranked_omits_tier(store):
    opgg = FakeOpgg()
    riot = EmptyRiot(league="league_empty")
    r = await _run(store, _games(SPEC), {}, opgg=opgg, riot=riot)
    sc = r.structured_content
    assert {c[2] for c in opgg.analysis_calls} == {None}
    assert sc["tier_basis"] == "전체 티어 기준"
    assert any("전체 티어 기준" in n for n in sc["notes"])
    assert "전체 티어 기준" in r.content[0].text


@pytest.mark.parametrize("name", ["카이사", "kaisa", "KAISA"])
async def test_focus_by_korean_or_english_name(store, name):
    opgg = FakeOpgg()
    stats = _games([("Kaisa", 12, 8), ("Jinx", 4, 2)])
    stats = [replace(s, deaths=1, cs=300) if s.win and s.champion == "Kaisa" else s for s in stats]
    r = await _run(store, stats, {"mode": "focus", "champion": name}, opgg=opgg)
    sc = r.structured_content
    assert not r.is_error
    assert sc["champion"] == "Kaisa" and sc["champion_name"] == "카이사"
    assert sc["summary"]["games"] == 12 and sc["opgg"]["status"] == "ok"
    assert [c["champion"] for c in sc["counters"]["hard_against"]] == ["스웨인", "코그모", "제리"]
    assert len(sc["counters"]["easy_against"]) == 3
    by = {x["metric"]: x for x in sc["rows"]}
    assert by["deaths"]["my_value"] == 1.0 and by["deaths"]["ref_source"] == "승리 판 vs 패배 판"
    assert opgg.analysis_calls == [("KAISA", "adc", "gold")]


async def test_focus_few_games_holds(store):
    r = await _run(store, _games([("Kaisa", 12, 6), ("Jinx", 3, 3)]),
                   {"mode": "focus", "champion": "징크스"})
    sc = r.structured_content
    assert sc["status"] == "판단 보류" and sc["summary"]["status"] == "판단 보류"
    assert "판단 보류" in r.content[0].text


async def test_focus_requires_and_resolves_champion(store):
    stats = _games(SPEC)
    r = await _run(store, stats, {"mode": "focus"})
    assert r.is_error and "champion" in r.content[0].text
    r = await _run(store, stats, {"mode": "progress", "champion": "없는챔프"})
    assert r.is_error and "알 수 없는 챔피언" in r.content[0].text


async def test_focus_champion_not_played(store):
    r = await _run(store, _games(SPEC), {"mode": "focus", "champion": "Caitlyn"})
    assert not r.is_error and r.structured_content["status"] == "판단 보류"
    assert any("해당 챔피언 경기가 없음" in n for n in r.structured_content["notes"])


async def test_ddragon_down_english_key_still_works(store):
    dd = FakeDD(error=httpx.ConnectTimeout("t"))
    r = await _run(store, _games(SPEC), {"mode": "focus", "champion": "Kaisa"}, dd=dd)
    sc = r.structured_content
    assert not r.is_error and sc["summary"]["games"] == 12
    assert any("Data Dragon" in n for n in sc["notes"])
    r = await _run(store, [], {"mode": "focus", "champion": "카이사"}, dd=dd)
    assert r.is_error


async def test_pool_labels(store):
    narrow = _games([("Kaisa", 9, 5), ("Jinx", 1, 0)])
    r = await _run(store, narrow, {"mode": "pool"})
    pl = r.structured_content["pool"]
    assert pl["label"] == "좁음" and pl["top3_share"] == 1.0 and pl["champion_count"] == 2
    assert pl["by_position"] == [{"position": "BOTTOM", "position_name": "원딜", "champions": 2}]


def test_pool_wide_mid_and_hold():
    wide = [replace(s, champion=f"C{i % 12}") for i, s in enumerate(_games([("X", 36, 18)]))]
    assert champions.pool_summary(wide)["label"] == "넓음"
    mid = _games([("A", 5, 2), ("B", 5, 2), ("C", 5, 2), ("D", 5, 2)])
    assert champions.pool_summary(mid)["label"] == "보통"
    assert champions.pool_summary(_games([("A", 3, 1)]))["label"] == "판단 보류"


async def test_similar(store):
    opgg = FakeOpgg()
    stats = _games([("Kaisa", 12, 6), ("Draven", 10, 5), ("Jinx", 8, 4)])
    r = await _run(store, stats, {"mode": "similar"}, opgg=opgg)
    sc = r.structured_content
    names = [c["champion"] for c in sc["similar"]["candidates"]]
    # 기준 카이사 제외, 10판 이상 한 드레이븐 제외, tier 3 루시안 제외, 태그 안 겹치는 스웨인 제외
    assert sc["similar"]["base_champion"] == "Kaisa"
    assert "Kaisa" not in names and "Draven" not in names
    assert "Lucian" not in names and "Swain" not in names
    assert set(names) == {"Jinx", "Caitlyn", "Ezreal"}
    assert len(names) <= 5
    assert opgg.meta_calls == ["adc"]
    assert any("전체 티어" in n for n in sc["notes"])


async def test_similar_opgg_down(store):
    r = await _run(store, _games(SPEC), {"mode": "similar"}, opgg=DownOpgg())
    assert not r.is_error
    assert r.structured_content["status"] == "OP.GG 데이터 없음"
    assert r.structured_content["similar"]["candidates"] == []


async def test_progress_chunks_chronological(store):
    # Kaisa 12판: 앞 5판 패배, 다음 5판 승리, 마지막 2판 (시간순)
    stats = _games([("Kaisa", 5, 0), ("Jinx", 3, 1), ("Kaisa", 5, 5), ("Kaisa", 2, 1)])
    r = await _run(store, stats, {"mode": "progress", "champion": "Kaisa"})
    pr = r.structured_content["progress"]
    assert [c["games"] for c in pr] == [5, 5, 2]
    assert [c["win_rate"] for c in pr] == [0.0, 1.0, 0.5]
    assert [c["status"] for c in pr] == ["ok", "ok", "부분 구간"]
    assert pr[0]["game_creation"] < pr[1]["game_creation"]
    assert "1구간(5판)" in r.content[0].text


async def test_period_limits_to_recent_count(store):
    stats = _games([("Jinx", 10, 5), ("Kaisa", 5, 5)])
    r = await _run(store, stats, {"count": 5})
    sc = r.structured_content
    assert sc["games"] == 5 and [x["champion"] for x in sc["rows"]] == ["Kaisa"]
    assert sc["period_desc"] == "최근 5판"


async def test_focus_text_labels_win_loss(store):
    r = await _run(store, _games([("Kaisa", 12, 6)]), {"mode": "focus", "champion": "Kaisa"})
    text = r.content[0].text
    assert "승리 판 vs 패배 판:" in text and "패배 판 " in text
