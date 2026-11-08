from pathlib import Path

import pytest

from lol_coach_mcp.opgg.parser import OpggParseError, parse_opgg

OPGG_DIR = Path(__file__).parent / "fixtures" / "opgg"


def _sample(name: str) -> str:
    return (OPGG_DIR / f"{name}.txt").read_text(encoding="utf-8")


def test_champion_analysis_sample():
    d = parse_opgg(_sample("champion_analysis_kaisa_adc_gold"))["data"]
    assert d["summary"]["average_stats"] == {
        "play": 132812, "win_rate": 0.49, "pick_rate": 0.22, "ban_rate": 0.04,
        "kda": 2.38, "tier": 1, "rank": 16,
    }
    assert d["strong_counters"][0] == {
        "champion_id": 81, "champion_name": "이즈리얼", "play": 5755, "win_rate": 0.54,
    }
    # 같은 클래스(StrongCounter)가 weak_counters에도 재사용됨
    assert [c["champion_name"] for c in d["weak_counters"]] == ["스웨인", "코그모", "제리"]
    assert d["core_items"]["ids"] == [6672, 3124, 3302]
    assert d["core_items"]["ids_names"][1] == "구인수의 격노검"
    assert d["core_items"]["win"] == 17201
    assert d["boots"]["ids_names"] == ["광전사의 군화"]
    assert d["summoner_spells"]["ids"] == [4, 21]
    assert d["runes"]["primary_page_name"] == "정밀"
    assert d["runes"]["secondary_rune_names"] == ["마법의 신발", "비스킷 배달"]
    assert d["runes"]["pick_rate"] == 0.53


def test_lane_meta_sample():
    rows = parse_opgg(_sample("lane_meta_adc"))["data"]["positions"]["adc"]
    assert len(rows) == 37
    assert rows[0] == {
        "champion": "자야", "play": 63756, "win_rate": 0.52, "pick_rate": 0.08,
        "ban_rate": 0.04, "kda": 2.36, "tier": 1, "rank": 1,
    }
    assert rows[-1]["champion"] == "멜"


def test_champions_list_sample():
    champs = parse_opgg(_sample("champions_list"))["data"]["champions"]
    by_key = {c["key"]: c for c in champs}
    assert by_key["MonkeyKing"]["name"] == "오공"
    assert by_key["Nunu"] == {"champion_id": 20, "key": "Nunu", "name": "누누와 윌럼프"}
    assert by_key["Kaisa"]["champion_id"] == 145


def test_lane_matchup_json_sample():
    d = parse_opgg(_sample("lane_matchup_kaisa_ezreal"))
    assert d["opponent_champion"] == "이즈리얼"
    assert d["data"]["summary"]["average_stats"]["play"] == 190863


def test_strings_with_special_characters():
    text = (
        'class Root: a,b,c\n'
        'class Item: name,n\n'
        '\n'
        'Root("쉼표, 괄호() [대괄호] \\"따옴표\\" 백슬래시\\\\", '
        '[Item("x,y)", -1.5e-3), Item("", 0)], null)'
    )
    got = parse_opgg(text)
    assert got["a"] == '쉼표, 괄호() [대괄호] "따옴표" 백슬래시\\'
    assert got["b"] == [{"name": "x,y)", "n": -0.0015}, {"name": "", "n": 0}]
    assert got["c"] is None


def test_literals_and_empty_list():
    text = "class R: t,f,n,e,i,neg\n\nR(true, false, null, [], 42, -7)"
    assert parse_opgg(text) == {"t": True, "f": False, "n": None, "e": [], "i": 42, "neg": -7}
    assert isinstance(parse_opgg(text)["i"], int)


@pytest.mark.parametrize("text", [
    "",
    "class R: a\n\nR(1, 2)",           # 필드 수 불일치
    "class R: a\n\nQ(1)",              # 정의되지 않은 클래스
    'class R: a\n\nR("unterminated)',  # 닫히지 않은 문자열
    "class R: a\n\nR(1) trailing",     # 남는 토큰
    "{not json",
])
def test_malformed_raises(text):
    with pytest.raises(OpggParseError):
        parse_opgg(text)
