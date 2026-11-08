from dataclasses import replace
from datetime import datetime

import httpx
import pytest
from mcp import Client

from lol_coach_mcp import splits
from lol_coach_mcp.splits import KST
from test_server_form import EmptyRiot, _Down, _ms
from test_server_profile import RIOT_ID, make_server, store  # noqa: F401, E402

DAY = 86_400_000


def kst_ms(y, mo, d, h, mi=0):
    return int(datetime(y, mo, d, h, mi, tzinfo=KST).timestamp() * 1000)


def _save(store, stats, matches=None):
    for s in stats:
        store.save_match(s.match_id, (matches or {}).get(s.match_id, {}), s)


async def _run(store, stats, args, matches=None, riot=None):
    async with Client(make_server(store, riot or EmptyRiot())) as c:
        await c.call_tool("my_profile", {"action": "set", "riot_id": RIOT_ID})
        _save(store, stats, matches)
        return await c.call_tool("compare_splits", args)


def _groups(r):
    return {g["label"]: g for g in r.structured_content["groups"]}


async def test_tool_listed_with_schema(store):
    async with Client(make_server(store, EmptyRiot())) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
    assert tools["compare_splits"].output_schema


async def test_requires_profile(store):
    async with Client(make_server(store, EmptyRiot())) as c:
        r = await c.call_tool("compare_splits", {"split_by": "win_loss"})
    assert r.is_error and "my_profile" in r.content[0].text


async def test_win_loss_rows_and_groups(store):
    stats = [_ms(i, win=i % 2 == 0, deaths=1 if i % 2 == 0 else 8, cs=250 if i % 2 == 0 else 150)
             for i in range(20)]
    r = await _run(store, stats, {"split_by": "win_loss"})
    sc = r.structured_content
    g = _groups(r)
    assert g["승리 판"]["games"] == 10 and g["패배 판"]["games"] == 10
    assert g["승리 판"]["win_rate"] == 1.0 and g["패배 판"]["win_rate"] == 0.0
    assert "win" not in g["승리 판"]["metrics"]
    assert g["승리 판"]["status"] == "ok"
    assert "상관관계이며 원인은 아님" in sc["notes"]
    by = {row["metric"]: row for row in sc["rows"]}
    assert by["deaths"]["my_value"] == 1.0 and by["deaths"]["ref_value"] == 8.0
    assert by["deaths"]["ref_source"] == "승리 판 vs 패배 판"
    assert by["deaths"]["status"] == "ok" and by["deaths"]["clear_gap"]
    text = r.content[0].text
    assert "승리 판 vs 패배 판" in text and "하세요" not in text and "솔로랭크" in text


@pytest.mark.parametrize("win", [True, False], ids=["all_wins", "all_losses"])
async def test_win_loss_not_comparable(store, win):
    stats = [_ms(i, win=win) for i in range(15)]
    r = await _run(store, stats, {"split_by": "win_loss"})
    sc = r.structured_content
    assert {row["status"] for row in sc["rows"]} == {"비교 불가"}
    assert not any(row["clear_gap"] for row in sc["rows"])
    assert any("비교할 수 없음" in n for n in sc["notes"])


async def test_time_of_day_kst_buckets(store):
    hours = [0, 5, 6, 11, 12, 17, 18, 23, 23]
    stats = [_ms(i, created=kst_ms(2026, 11, 2 + i, h, 30)) for i, h in enumerate(hours)]
    r = await _run(store, stats, {"split_by": "time_of_day"})
    g = _groups(r)
    assert [x["label"] for x in r.structured_content["groups"]] == ["0~6시", "6~12시", "12~18시", "18~24시"]
    assert g["0~6시"]["games"] == 2 and g["6~12시"]["games"] == 2
    assert g["12~18시"]["games"] == 2 and g["18~24시"]["games"] == 3
    assert g["0~6시"]["status"] == "판단 보류" and "판단 보류" in r.content[0].text


async def test_time_of_day_boundary_uses_kst_not_utc(store):
    # 2026-11-02 01:00 KST == 2026-11-01 16:00 UTC -> 0~6시
    stats = [_ms(i, created=kst_ms(2026, 11, 2, 1) + i * 60_000) for i in range(6)]
    r = await _run(store, stats, {"split_by": "time_of_day"})
    g = _groups(r)
    assert list(g) == ["0~6시"] and g["0~6시"]["status"] == "ok"


async def test_weekday_names(store):
    # 2026-11-02 is a Monday
    days = [0, 0, 0, 0, 0, 1, 6]
    stats = [_ms(i, created=kst_ms(2026, 11, 2 + d, 12, i)) for i, d in enumerate(days)]
    r = await _run(store, stats, {"split_by": "weekday"})
    g = _groups(r)
    assert g["월"]["games"] == 5 and g["월"]["status"] == "ok"
    assert g["화"]["games"] == 1 and g["일"]["games"] == 1
    assert list(g) == ["월", "화", "일"]


async def test_game_length_buckets(store):
    secs = [1499, 1500, 2100, 2101, 1800]
    stats = [_ms(i, duration_s=d) for i, d in enumerate(secs)]
    r = await _run(store, stats, {"split_by": "game_length"})
    g = _groups(r)
    assert g["25분 미만"]["games"] == 1
    assert g["25~35분"]["games"] == 3
    assert g["35분 초과"]["games"] == 1


async def test_position_split_and_no_mixed_note(store):
    stats = [_ms(i) for i in range(6)] + [_ms(20 + i, position="JUNGLE") for i in range(3)]
    r = await _run(store, stats, {"split_by": "position"})
    g = _groups(r)
    assert g["BOTTOM"]["games"] == 6 and g["JUNGLE"]["games"] == 3
    assert g["JUNGLE"]["status"] == "판단 보류"
    assert not any("포지션이 섞인" in n for n in r.structured_content["notes"])


async def test_mixed_positions_note_on_other_splits(store):
    stats = [_ms(i) for i in range(6)] + [_ms(20 + i, position="JUNGLE") for i in range(3)]
    r = await _run(store, stats, {"split_by": "game_length"})
    assert any("포지션이 섞인" in n for n in r.structured_content["notes"])


async def test_patch_split_newest_first(store):
    stats = ([_ms(i, patch="16.9") for i in range(5)] + [_ms(10 + i, patch="16.10") for i in range(5)]
             + [_ms(20 + i, patch="15.24") for i in range(2)])
    r = await _run(store, stats, {"split_by": "patch"})
    assert [g["label"] for g in r.structured_content["groups"]] == ["16.10", "16.9", "15.24"]


def _match(stat, partner_ids):
    """stat.teammates 중 partner_ids의 Riot ID를 포함하는 저장용 경기 JSON."""
    parts = [{"puuid": "puuid-me", "riotIdGameName": "나", "riotIdTagline": "KR1", "teamId": 100}]
    for t in stat.teammates:
        name = partner_ids.get(t, f"팀원{t}")
        parts.append({"puuid": t, "riotIdGameName": name, "riotIdTagline": "KR1", "teamId": 100})
    return {"info": {"participants": parts}}


async def test_duo_detection_three_games(store):
    stats = []
    for i in range(12):
        s = _ms(i, win=i < 3)
        # 처음 3판만 같은 팀원 "duo-p"와 함께
        mates = ("duo-p", f"x{i}a", f"x{i}b", f"x{i}c") if i < 3 else (f"y{i}a", f"y{i}b", f"y{i}c", f"y{i}d")
        stats.append(replace(s, teammates=mates))
    matches = {s.match_id: _match(s, {"duo-p": "듀오친구"}) for s in stats}
    r = await _run(store, stats, {"split_by": "duo"}, matches=matches)
    sc = r.structured_content
    assert sc["duo_partners"] == [{"riot_id": "듀오친구#KR1", "games_together": 3}]
    g = _groups(r)
    assert g["듀오(듀오친구#KR1)"]["games"] == 3 and g["듀오(듀오친구#KR1)"]["status"] == "판단 보류"
    assert g["혼자"]["games"] == 9 and g["혼자"]["status"] == "ok"
    assert g["듀오(듀오친구#KR1)"]["win_rate"] == 1.0
    text = r.content[0].text
    assert "듀오친구#KR1" in text and "puuid" not in text.lower()
    # 파트너 본인 지표는 없음, 저장도 하지 않음
    assert set(sc["duo_partners"][0]) == {"riot_id", "games_together"}
    assert store.get_meta("duo_partners") is None


async def test_duo_two_games_is_not_partner(store):
    stats = []
    for i in range(10):
        first = "p" if i < 2 else f"y{i}"
        stats.append(replace(_ms(i), teammates=(first, f"a{i}", f"b{i}", f"c{i}")))
    r = await _run(store, stats, {"split_by": "duo"})
    sc = r.structured_content
    assert sc["duo_partners"] == []
    assert [g["label"] for g in sc["groups"]] == ["혼자"]
    assert any("듀오 후보가 없음" in n for n in sc["notes"])


async def test_duo_all_games_duo_shows_empty_solo_group_on_hold(store):
    stats = [replace(_ms(i), teammates=("p", f"a{i}", f"b{i}", f"c{i}")) for i in range(8)]
    matches = {s.match_id: _match(s, {"p": "짝꿍"}) for s in stats}
    r = await _run(store, stats, {"split_by": "duo"}, matches=matches)
    g = _groups(r)
    assert list(g) == ["듀오(짝꿍#KR1)", "혼자"]
    assert g["듀오(짝꿍#KR1)"]["games"] == 8
    assert g["혼자"]["games"] == 0 and g["혼자"]["status"] == "판단 보류"
    assert g["혼자"]["win_rate"] is None
    assert "혼자: 0판" in r.content[0].text


async def test_duo_unknown_riot_id_is_labeled(store):
    stats = [replace(_ms(i), teammates=("pppppppp-1", f"a{i}", f"b{i}", f"c{i}")) for i in range(5)]
    r = await _run(store, stats, {"split_by": "duo"})
    assert r.structured_content["duo_partners"][0]["riot_id"].startswith("Riot ID 확인 불가")


def test_duo_multiple_partners_no_double_count():
    stats = [replace(_ms(i), teammates=("p", "q", f"a{i}", f"b{i}")) for i in range(4)]
    stats += [replace(_ms(10 + i), teammates=("q", f"c{i}", f"d{i}", f"e{i}")) for i in range(2)]
    groups, partners, _ = splits.duo_groups(stats, lambda mid: None)
    assert sum(g["games"] for g in groups) == len(stats)
    assert partners[0]["games_together"] == 6  # q


async def test_period_and_count_applied(store):
    stats = [_ms(i) for i in range(20)]
    r = await _run(store, stats, {"split_by": "win_loss", "count": 8})
    assert r.structured_content["games"] == 8 and "8판" in r.structured_content["period_desc"]


async def test_remakes_excluded(store):
    stats = [_ms(i) for i in range(6)] + [replace(_ms(50 + i), is_remake=True) for i in range(3)]
    r = await _run(store, stats, {"split_by": "game_length"})
    assert r.structured_content["games"] == 6


async def test_no_games_is_not_error(store):
    r = await _run(store, [], {"split_by": "weekday"})
    assert not r.is_error and r.structured_content["groups"] == []


async def test_sync_failure_note(store):
    r = await _run(store, [_ms(i) for i in range(6)], {"split_by": "win_loss"},
                   riot=_Down(httpx.ConnectTimeout("t")))
    assert not r.is_error
    assert "동기화 실패, 저장된 데이터 기준" in r.structured_content["notes"]


async def test_sync_failure_without_data_is_error(store):
    r = await _run(store, [], {"split_by": "win_loss"}, riot=_Down(httpx.ConnectTimeout("t")))
    assert r.is_error
