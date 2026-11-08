from conftest import PUUID_ME, load_fixture
from lol_coach_mcp import journal, review
from lol_coach_mcp.stats import extract_match_stats


def _fixture():
    m = load_fixture("match_normal")
    return m, load_fixture("timeline_normal"), extract_match_stats(m, PUUID_ME)


def test_keyword_matching_case_insensitive_multiple():
    assert journal.match_topics("갱 맞고 cs도 밀림") == ["jungle", "farming"]
    assert journal.match_topics("Cs 파밍") == ["farming"]
    assert journal.match_topics("와드 부족, 한타 지고 딜 없음") == ["vision", "teamfight", "damage"]
    assert journal.match_topics("라인전 손해") == ["lane"]
    assert journal.match_topics("그냥 피곤") == []


def test_keyword_false_positives_excluded_and_cs_standalone():
    for t in ("딜레이가 심함", "딜러 포지션", "갱신된 패치", "ics 하나", "abcs", "csv 파일"):
        assert journal.match_topics(t) == [], t
    assert journal.match_topics("CS 부족") == ["farming"]
    assert journal.match_topics("cs, 놓침") == ["farming"]
    assert journal.match_topics("cs10 부족, cs20개") == ["farming"]
    assert journal.match_topics("딜이 낮음") == ["damage"]
    assert journal.match_topics("갱 당함") == ["jungle"]


def test_jungle_bundle_counts_early_deaths_and_highlights_jungle_zones():
    m, tl, s = _fixture()
    b = journal.build_bundle("jungle", s, m, tl, PUUID_ME)
    assert b["early_deaths"] == 1
    assert b["deaths"][0]["time"] == "11:44" and b["deaths"][0]["killer_champion"] == "Ezreal"
    assert b["deaths"][0]["jungle_zone"] is False and b["jungle_zone_deaths"] == 0
    assert journal.is_jungle_zone("위쪽 정글") and journal.is_jungle_zone("아래쪽 정글")
    assert not journal.is_jungle_zone("바텀")


def test_farming_has_cs_per_min_and_ten_minute_cs():
    m, tl, s = _fixture()
    b = journal.build_bundle("farming", s, m, tl, PUUID_ME)
    assert b["cs_at_10"] == review.lane_checkpoints(m, tl, PUUID_ME, (10,))["points"][0]["my_cs"]
    assert b["cs_per_min"] > 0


def test_teamfight_late_deaths_from_25_minutes():
    m, tl, s = _fixture()
    b = journal.build_bundle("teamfight", s, m, tl, PUUID_ME)
    assert b["late_deaths"] == 3 and "kill_participation" in b


def test_no_timeline_leaves_timeline_fields_none():
    m, tl, s = _fixture()
    b = journal.build_bundle("jungle", s, m, None, PUUID_ME)
    assert b["early_deaths"] is None and b["deaths"] is None
    assert journal.build_bundle("damage", s, m, None, PUUID_ME)["damage_share"] == s.damage_share
