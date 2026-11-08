from dataclasses import replace

import httpx
from mcp import Client

from lol_coach_mcp import builds, matchups
from test_server_champions import ANALYSIS, DD, FakeDD, FakeOpgg, make
from test_server_form import EmptyRiot, _ms
from test_server_profile import PUUID, RIOT_ID, store  # noqa: F401

DD_FULL = {
    **DD,
    "Lulu": {"id": 117, "name": "룰루", "tags": ["Support", "Mage"]},
    "Nami": {"id": 267, "name": "나미", "tags": ["Support", "Mage"]},
}
ITEMS = {
    3031: {"name": "무한의 대검", "gold": 3450, "tags": ["Damage"]},
    3036: {"name": "도미닉 경의 인사", "gold": 3000, "tags": ["Damage"]},
    3094: {"name": "속사포", "gold": 2600, "tags": ["Damage"]},
    3085: {"name": "루난의 허리케인", "gold": 2600, "tags": ["Damage"]},
    3046: {"name": "유령 무희", "gold": 2600, "tags": ["Damage"]},
    3006: {"name": "광전사의 군화", "gold": 1100, "tags": ["Boots"]},
    3009: {"name": "신속의 장화", "gold": 900, "tags": ["Boots"]},
    2003: {"name": "체력 물약", "gold": 50, "tags": ["Consumable"]},
    3340: {"name": "수호자의 와드", "gold": 0, "tags": ["Trinket"]},
}
RUNES = {
    8008: {"name": "치명적 속도", "key": "LethalTempo", "tree": "정밀"},
    8021: {"name": "기민한 발놀림", "key": "FleetFootwork", "tree": "정밀"},
    8112: {"name": "감전", "key": "Electrocute", "tree": "지배"},
}
OPGG_ANALYSIS = {
    **ANALYSIS,
    # 실측: champion_analysis의 추천 항목은 단일 객체(최다 픽 조합)
    "core_items": {"ids": [3031, 3036, 3094], "ids_names": ["무한의 대검", "도미닉 경의 인사", "속사포"],
                   "play": 1000, "win": 540, "pick_rate": 0.4},
    "boots": {"ids": [3006], "ids_names": ["광전사의 군화"], "play": 900, "win": 480, "pick_rate": 0.8},
    "runes": {"primary_page_name": "정밀",
              "primary_rune_names": ["치명적 속도", "승전", "전설: 민첩함", "최후의 일격"],
              "secondary_page_name": "영감", "secondary_rune_names": ["마법공학 점멸기", "비스킷 배달"],
              "play": 700, "win": 380, "pick_rate": 0.5},
    "summoner_spells": {"ids": [4, 7], "ids_names": [4, 7], "play": 800, "win": 420, "pick_rate": 0.7},
}
MATCHUP = {
    "position": "adc", "my_champion": "카이사", "opponent_champion": "이즈리얼",
    "average_stats": {"play": 190863, "win_rate": 0.4934, "kda": 2.41, "tier": 1},
    "matchup": {"play": 13613, "win": 7467, "win_rate": 0.5485},
    "opponent_champion_tip": "견제하세요!",
    "lane_advantage_champion": "EVEN", "lane_solo_kill_advantage_champion": "EVEN",
    "recommended_play_style": "even",
    "core_items": [], "boots": [], "summoner_spells": [], "runes": [],
}


class MatchupOpgg(FakeOpgg):
    def __init__(self, matchup=MATCHUP, analysis=OPGG_ANALYSIS, **kw):
        super().__init__(analysis=analysis, **kw)
        self.matchup = matchup
        self.matchup_calls: list[tuple] = []

    async def lane_matchup(self, position, my_key, opp_key):
        self.matchup_calls.append((position, my_key, opp_key))
        return self.matchup


class DownOpgg(MatchupOpgg):
    async def champion_analysis(self, key, position, tier):
        return None

    async def lane_matchup(self, position, my_key, opp_key):
        return None


class FakeDD2(FakeDD):
    def __init__(self, data=DD_FULL, items=ITEMS, runes=RUNES, error=None):
        super().__init__(data=data, error=error)
        self._items, self._runes = items, runes

    async def items(self):
        if self.error:
            raise self.error
        return self._items

    async def runes(self):
        if self.error:
            raise self.error
        return self._runes


def _participant(pid, puuid, team, champ, pos, *, items=(), keystone=8008, spells=(4, 7), win=True):
    slots = list(items) + [0] * (6 - len(items))
    d = {"participantId": pid, "puuid": puuid, "teamId": team, "championName": champ,
         "teamPosition": pos, "win": win, "summoner1Id": spells[0], "summoner2Id": spells[1],
         "item6": 3340,
         "perks": {"styles": [{"selections": [{"perk": keystone}]}, {"selections": [{"perk": 8112}]}]}}
    for i, it in enumerate(slots):
        d[f"item{i}"] = it
    return d


def _match(i, champ="Kaisa", enemy="Ezreal", *, win=True, pos="BOTTOM", items=(), keystone=8008,
           spells=(4, 7), enemy_pos=None):
    mid = f"KR_{i:010d}"
    ps = [_participant(1, PUUID, 100, champ, pos, items=items, keystone=keystone, spells=spells, win=win),
          _participant(2, f"foe-{i}", 200, enemy, enemy_pos or pos, win=not win)]
    if enemy_pos and enemy_pos != pos:  # 같은 라인 상대는 다른 챔피언
        ps.append(_participant(3, f"lane-{i}", 200, "Jinx", pos, win=not win))
    return {"metadata": {"matchId": mid}, "info": {"participants": ps}}


def _timeline(i, my_gold=6000, opp_gold=5500, frames=16):
    def pf(g):
        return {"totalGold": g, "level": 11, "xp": 5000, "minionsKilled": 100,
                "jungleMinionsKilled": 0}
    fr = [{"participantFrames": {"1": pf(my_gold), "2": pf(opp_gold)}} for _ in range(frames)]
    return {"info": {"participants": [{"participantId": 1, "puuid": PUUID},
                                      {"participantId": 2, "puuid": f"foe-{i}"}], "frames": fr}}


class Seed:
    """경기·지표·타임라인을 store에 넣는 도우미(시간순)."""

    def __init__(self, store):
        self.store, self.n = store, 0

    def add(self, champ="Kaisa", enemy="Ezreal", *, win=True, pos="BOTTOM", timeline=None, **kw):
        m = _match(self.n, champ, enemy, win=win, pos=pos, **kw)
        s = replace(_ms(self.n, win=win, position=pos), champion=champ)
        self.store.save_match(s.match_id, m, s)
        if timeline is not None:
            self.store.save_timeline(s.match_id, timeline)
        self.n += 1

    def many(self, n, wins, champ="Kaisa", enemy="Ezreal", **kw):
        for k in range(n):
            self.add(champ, enemy, win=k < wins, **kw)


async def _call(store, tool, args, *, opgg=None, dd=None, riot=None):
    opgg = MatchupOpgg() if opgg is None else opgg
    dd = FakeDD2() if dd is None else dd
    async with Client(make(store, riot or EmptyRiot(), opgg, dd)) as c:
        await c.call_tool("my_profile", {"action": "set", "riot_id": RIOT_ID})
        return await c.call_tool(tool, args)


CORE_A = (3031, 3036, 3094, 3006, 2003)  # 군화·소모품이 섞인 최종 아이템
CORE_B = (3031, 3085, 3094, 3009)


def _seed_builds(store):
    sd = Seed(store)
    for k in range(6):  # A: 6판 4승, 치명적 속도, 점멸+회복
        sd.add("Kaisa", win=k < 4, items=CORE_A, keystone=8008, spells=(4, 7))
    for k in range(3):  # B: 3판 1승, 기민한 발놀림, 점화+점멸 (순서 반대)
        sd.add("Kaisa", win=k < 1, items=CORE_B, keystone=8021, spells=(14, 4))
    for k in range(2):  # C: 2판 → 판단 보류
        sd.add("Kaisa", win=True, items=(3031, 3046, 3006), keystone=8008, spells=(4, 7))
    sd.many(4, 2, "Jinx")
    return sd


async def test_new_tools_listed_with_schema_and_require_profile(store):
    async with Client(make(store, EmptyRiot(), MatchupOpgg(), FakeDD2())) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
        r1 = await c.call_tool("matchup_report", {"enemies": ["이즈리얼"]})
        r2 = await c.call_tool("build_report", {"champion": "카이사"})
    assert tools["matchup_report"].output_schema and tools["build_report"].output_schema
    assert r1.is_error and "my_profile" in r1.content[0].text
    assert r2.is_error and "my_profile" in r2.content[0].text


# ---------- build_report ----------

async def test_build_groups_overlap_and_hold(store):
    _seed_builds(store)
    opgg = MatchupOpgg()
    r = await _call(store, "build_report", {"champion": "카이사"}, opgg=opgg)
    assert not r.is_error
    sc = r.structured_content
    assert sc["champion"] == "Kaisa" and sc["champion_name"] == "카이사" and sc["games"] == 11
    assert opgg.analysis_calls == [("KAISA", "adc", "gold")]
    core = {tuple(g["ids"]): g for g in sc["core_items"]}
    a = core[(3031, 3036, 3094)]  # 군화·소모품·와드 제외
    assert a["games"] == 6 and a["wins"] == 4 and a["win_rate"] == 0.667
    assert a["names"] == ["무한의 대검", "도미닉 경의 인사", "속사포"]
    assert a["status"] == "ok" and a["opgg_rank"] == 1 and a["overlap"] is True
    b = core[(3031, 3085, 3094)]
    assert b["games"] == 3 and b["status"] == "ok" and b["opgg_rank"] is None and b["overlap"] is False
    c = core[(3031, 3046)]
    assert c["games"] == 2 and c["status"] == "판단 보류" and c["overlap"] is False
    assert [g["games"] for g in sc["core_items"]] == [6, 3, 2]
    boots = {tuple(g["ids"]): g for g in sc["boots"]}
    assert boots[(3006,)]["games"] == 8 and boots[(3006,)]["overlap"] is True
    assert boots[(3009,)]["games"] == 3 and boots[(3009,)]["overlap"] is False
    runes = {g["names"][0]: g for g in sc["runes"]}
    assert runes["치명적 속도"]["games"] == 8 and runes["치명적 속도"]["overlap"] is True
    assert runes["기민한 발놀림"]["games"] == 3 and runes["기민한 발놀림"]["overlap"] is False
    sp = {tuple(g["ids"]): g for g in sc["spells"]}
    assert sp[(4, 7)]["games"] == 8 and sp[(4, 7)]["overlap"] is True
    assert sp[(4, 7)]["names"] == ["점멸", "회복"]
    assert sp[(4, 14)]["games"] == 3 and sp[(4, 14)]["overlap"] is False  # 순서 무관 묶음
    assert sc["opgg"]["status"] == "ok"
    assert sc["opgg"]["spells"][0]["ids_names"] == ["점멸", "회복"]  # OP.GG ids_names가 숫자여도 이름 표시
    text = r.content[0].text
    assert "솔로랭크" in text and "GOLD 티어 기준" in text and "판단 보류" in text
    assert "겹침" in text and "None" not in text
    assert "하세요" not in text and "하라" not in text


async def test_build_opgg_down_keeps_my_data(store):
    _seed_builds(store)
    r = await _call(store, "build_report", {"champion": "Kaisa"}, opgg=DownOpgg())
    sc = r.structured_content
    assert not r.is_error and sc["core_items"][0]["games"] == 6
    assert sc["opgg"] == {"status": "OP.GG 데이터 없음"}
    assert all(g["overlap"] is None for g in sc["core_items"] + sc["runes"] + sc["spells"])
    assert "OP.GG 데이터 없음" in r.content[0].text


async def test_build_ddragon_down_english_key(store):
    _seed_builds(store)
    dd = FakeDD2(error=httpx.ConnectTimeout("t"))
    r = await _call(store, "build_report", {"champion": "Kaisa"}, dd=dd)
    sc = r.structured_content
    assert not r.is_error and sc["games"] == 11
    assert sc["core_items"] == [] and sc["runes"] == []
    assert sc["spells"][0]["games"] == 8  # 소환사 주문은 Data Dragon 없이도 가능
    assert any("Data Dragon" in n for n in sc["notes"])
    r = await _call(store, "build_report", {"champion": "카이사"}, dd=dd)
    assert r.is_error and "Data Dragon" in r.content[0].text


async def test_build_unknown_and_unplayed(store):
    _seed_builds(store)
    r = await _call(store, "build_report", {"champion": "없는챔프"})
    assert r.is_error and "알 수 없는 챔피언" in r.content[0].text
    r = await _call(store, "build_report", {"champion": "나미"})
    assert not r.is_error and r.structured_content["status"] == "판단 보류"
    assert r.structured_content["games"] == 0


async def test_build_skips_games_without_detail(store):
    sd = Seed(store)
    sd.many(3, 2, "Kaisa", items=CORE_A)
    s = replace(_ms(50, win=True), champion="Kaisa")
    store.save_match(s.match_id, {}, s)  # 원본 경기 상세가 비어 있음
    r = await _call(store, "build_report", {"champion": "Kaisa"})
    sc = r.structured_content
    assert sc["games"] == 4 and sc["core_items"][0]["games"] == 3
    assert any("상세 데이터 없음" in n for n in sc["notes"])


# ---------- matchup_report: my_champion ----------

def _seed_matchups(store):
    sd = Seed(store)
    # 이즈리얼 4판 3승(타임라인 2판: +500, +1000), 같은 포지션이 아닌 이즈리얼 1판(제외 대상)
    sd.add("Kaisa", "Ezreal", win=True, timeline=_timeline(0, 6000, 5500))
    sd.add("Kaisa", "Ezreal", win=True, timeline=_timeline(1, 7000, 6000))
    sd.add("Kaisa", "Ezreal", win=True)
    sd.add("Kaisa", "Ezreal", win=False)
    sd.add("Kaisa", "Ezreal", win=False, enemy_pos="UTILITY")
    # 룰루 2판(<3) + 타임라인 1판(15분 미만 → 제외)
    sd.add("Kaisa", "Lulu", win=True, timeline=_timeline(5, frames=10))
    sd.add("Kaisa", "Lulu", win=False)
    sd.many(2, 1, "Jinx", "Ezreal")
    return sd


async def test_matchup_with_my_champion(store):
    _seed_matchups(store)
    opgg = MatchupOpgg()
    r = await _call(store, "matchup_report",
                    {"enemies": ["이즈리얼", "Lulu", "없는챔프"], "my_champion": "카이사"}, opgg=opgg)
    assert not r.is_error
    sc = r.structured_content
    assert sc["mode"] == "my_champion" and sc["my_champion"] == "Kaisa"
    rows = {x["input"]: x for x in sc["rows"]}
    ez = rows["이즈리얼"]
    assert ez["enemy"] == "Ezreal" and ez["enemy_name"] == "이즈리얼"
    assert ez["games"] == 4 and ez["wins"] == 3 and ez["win_rate"] == 0.75
    assert ez["status"] == "ok"
    assert ez["timeline_games"] == 2 and ez["avg_gold_diff_15"] == 750.0
    assert ez["opgg"]["status"] == "ok"
    assert ez["opgg"]["matchup"]["win_rate"] == 0.5485
    assert ez["opgg"]["my_champion_overall"]["win_rate"] == 0.4934
    lulu = rows["Lulu"]
    assert lulu["games"] == 2 and lulu["status"] == "판단 보류"
    assert lulu["timeline_games"] == 0 and lulu["avg_gold_diff_15"] is None
    unk = rows["없는챔프"]
    assert unk["status"] == "알 수 없는 챔피언" and unk["enemy"] is None
    # OP.GG는 알려진 상대만, 포지션 adc, 키 대문자
    assert opgg.matchup_calls == [("adc", "KAISA", "EZREAL"), ("adc", "KAISA", "LULU")]
    text = r.content[0].text
    assert "솔로랭크" in text and "알 수 없는 챔피언" in text and "판단 보류" in text
    assert "내 챔피언 전체" in text  # average_stats는 매치업이 아니라 내 챔피언 전체 통계
    assert "None" not in text and "하세요" not in text and "하라" not in text


async def test_matchup_my_champion_opgg_down(store):
    _seed_matchups(store)
    r = await _call(store, "matchup_report", {"enemies": ["이즈리얼"], "my_champion": "카이사"},
                    opgg=DownOpgg())
    sc = r.structured_content
    assert not r.is_error and sc["rows"][0]["games"] == 4
    assert sc["rows"][0]["opgg"] == {"status": "OP.GG 데이터 없음"}
    assert "OP.GG 데이터 없음" in r.content[0].text


async def test_matchup_unknown_my_champion_errors(store):
    _seed_matchups(store)
    r = await _call(store, "matchup_report", {"enemies": ["이즈리얼"], "my_champion": "없는챔프"})
    assert r.is_error and "알 수 없는 챔피언" in r.content[0].text


async def test_matchup_ddragon_down(store):
    _seed_matchups(store)
    dd = FakeDD2(error=httpx.ConnectTimeout("t"))
    r = await _call(store, "matchup_report", {"enemies": ["Ezreal", "이즈리얼"],
                                              "my_champion": "Kaisa"}, dd=dd)
    sc = r.structured_content
    assert not r.is_error
    assert sc["rows"][0]["games"] == 4  # 영문 키는 내 경기 기록으로 해석
    assert sc["rows"][1]["status"] == "알 수 없는 챔피언"
    assert any("Data Dragon" in n for n in sc["notes"])


# ---------- matchup_report: 챔피언 선택(my_champion 없음) ----------

async def test_matchup_pick_mode_rows_sorted_and_counters(store):
    sd = Seed(store)
    sd.many(5, 4, "Kaisa", "Ezreal")        # 카이사 vs 이즈리얼 5판 4승
    sd.many(5, 1, "Kaisa", "Swain")         # 카이사 vs 스웨인 5판 1승
    sd.many(6, 2, "Jinx", "Ezreal")         # 징크스 vs 이즈리얼 6판 2승
    sd.many(2, 2, "Draven", "Ezreal")       # 5판 미만 챔피언은 후보 아님
    sd.many(5, 5, "Lulu", "Nami", pos="UTILITY")  # 다른 포지션(주 포지션 아님)
    opgg = MatchupOpgg()
    r = await _call(store, "matchup_report", {"enemies": ["이즈리얼", "스웨인", "없는챔프"]}, opgg=opgg)
    assert not r.is_error
    sc = r.structured_content
    assert sc["mode"] == "pick"
    assert [c["champion"] for c in sc["candidates"]] == ["Kaisa", "Jinx"]  # 판수 순
    known = [x for x in sc["rows"] if x.get("candidate")]
    # 정렬: 내 판수 내림차순, 같으면 승률 내림차순
    order = [(x["candidate"], x["enemy"]) for x in known]
    assert order == [("Jinx", "Ezreal"), ("Kaisa", "Ezreal"), ("Kaisa", "Swain"),
                     ("Jinx", "Swain")]
    k_ez = next(x for x in known if x["candidate"] == "Kaisa" and x["enemy"] == "Ezreal")
    assert k_ez["games"] == 5 and k_ez["win_rate"] == 0.8 and k_ez["status"] == "ok"
    assert k_ez["counter"]["relation"] == "상대하기 쉬움" and k_ez["counter"]["my_win_rate"] == 0.54
    k_sw = next(x for x in known if x["candidate"] == "Kaisa" and x["enemy"] == "Swain")
    assert k_sw["counter"]["relation"] == "상대하기 어려움"
    assert k_sw["counter"]["opponent_win_rate"] == 0.69 and k_sw["counter"]["my_win_rate"] == 0.31
    j_sw = next(x for x in known if x["candidate"] == "Jinx" and x["enemy"] == "Swain")
    assert j_sw["games"] == 0 and j_sw["status"] == "판단 보류"
    assert any(x["status"] == "알 수 없는 챔피언" for x in sc["rows"])
    assert sorted(c[0] for c in opgg.analysis_calls) == ["JINX", "KAISA"]
    assert {c[1:] for c in opgg.analysis_calls} == {("adc", "gold")}
    text = r.content[0].text
    assert "솔로랭크" in text and "판단 보류" in text and "None" not in text
    assert "하세요" not in text and "하라" not in text


async def test_matchup_pick_mode_limits_opgg_calls_to_five(store):
    sd = Seed(store)
    for i in range(7):
        sd.many(5, 3, f"C{i}", "Ezreal")
    dd = FakeDD2(data={**DD_FULL, **{f"C{i}": {"id": 900 + i, "name": f"챔{i}", "tags": []}
                                      for i in range(7)}})
    opgg = MatchupOpgg()
    r = await _call(store, "matchup_report", {"enemies": ["이즈리얼"]}, opgg=opgg, dd=dd)
    sc = r.structured_content
    assert len(opgg.analysis_calls) == 5
    assert len(sc["candidates"]) == 7
    without = [x for x in sc["rows"] if x.get("candidate") and x["counter"] is None]
    assert len(without) == 2 and all(x["opgg_status"] == "OP.GG 조회 제외" for x in without)
    assert any("5개" in n for n in sc["notes"])


async def test_matchup_pick_mode_opgg_down(store):
    sd = Seed(store)
    sd.many(5, 3, "Kaisa", "Ezreal")
    r = await _call(store, "matchup_report", {"enemies": ["이즈리얼"]}, opgg=DownOpgg())
    sc = r.structured_content
    assert not r.is_error and sc["rows"][0]["games"] == 5
    assert sc["rows"][0]["counter"] is None and sc["rows"][0]["opgg_status"] == "OP.GG 데이터 없음"


async def test_matchup_pick_mode_no_candidates(store):
    Seed(store).many(3, 1, "Kaisa", "Ezreal")
    r = await _call(store, "matchup_report", {"enemies": ["이즈리얼"]})
    sc = r.structured_content
    assert not r.is_error and sc["candidates"] == [] and sc["status"] == "판단 보류"


def test_pure_helpers():
    assert matchups.MATCHUP_MIN_GAMES == 3 and builds.BUILD_MIN_GAMES == 3
    assert builds.group_status(2) == "판단 보류" and builds.group_status(3) == "ok"
    assert matchups.record_status(0) == "판단 보류" and matchups.record_status(3) == "ok"
    assert builds.SPELL_NAMES[4] == "점멸"
    blk = matchups.matchup_block({"average_stats": {"kda": 2.411125}, "matchup": None})
    assert blk["my_champion_overall"]["kda"] == 2.41
    opts = [{"ids": [1]}, {"ids": [2]}]
    assert builds.options({"ids": [1]}) == [{"ids": [1]}] and builds.options(None) == []
    assert builds.opgg_rank(opts, builds.core_matches([2, 9])) == 2  # 목록 형태도 허용
    assert builds.opgg_rank({"ids": [1]}, builds.core_matches([2])) is None
