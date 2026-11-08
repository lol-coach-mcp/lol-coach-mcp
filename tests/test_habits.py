from lol_coach_mcp import habits

ME = "puuid-me"


def _match(win=True):
    return {
        "info": {
            "participants": [
                {"puuid": ME, "participantId": 1, "teamId": 100, "championName": "Jinx", "win": win},
                {"puuid": "e", "participantId": 2, "teamId": 200, "championName": "Ashe", "win": not win},
                {"puuid": "m", "participantId": 3, "teamId": 100, "championName": "Lux", "win": win},
            ]
        }
    }


def _death(ms, pos=(7000, 7000), killer=2, assists=()):
    return {
        "type": "CHAMPION_KILL", "timestamp": ms, "victimId": 1, "killerId": killer,
        "assistingParticipantIds": list(assists), "position": {"x": pos[0], "y": pos[1]},
    }


def _buy(ms, pid=1):
    return {"type": "ITEM_PURCHASED", "timestamp": ms, "participantId": pid, "itemId": 1055}


def _tl(*events):
    return {
        "info": {
            "participants": [{"puuid": ME, "participantId": 1}, {"puuid": "e", "participantId": 2}],
            "frames": [{"events": list(events)}],
        }
    }


def test_bucket_labels():
    assert habits.bucket_labels() == [
        "0-5분", "5-10분", "10-15분", "15-20분", "20-25분", "25-30분", "30-35분", "35분+",
    ]


def test_death_buckets_boundaries_and_last_bucket():
    tl = _tl(_death(299_999, killer=0), _death(300_000, killer=0), _death(2_100_000, killer=0),
             _death(3_500_000, killer=0))
    out = habits.detect_habits([(_match(), tl)], ME)
    counts = {b["bucket"]: b["deaths"] for b in out["death_time_buckets"]}
    assert counts["0-5분"] == 1 and counts["5-10분"] == 1 and counts["35분+"] == 2
    assert sum(counts.values()) == out["deaths_total"] == 4


def test_top_zones_three_with_counts_and_deterministic_tie():
    top, mid, bot = (1000, 14000), (7000, 7000), (14000, 1000)
    tl = _tl(_death(10_000, top), _death(500_000, mid), _death(900_000, mid),
             _death(1_500_000, bot), _death(2_000_000, (2000, 2000)), _death(2_500_000, mid))
    zones = habits.detect_habits([(_match(), tl)], ME)["top_death_zones"]
    assert zones[0] == {"zone": "미드", "deaths": 3}
    assert len(zones) == 3
    assert [z["deaths"] for z in zones] == [3, 1, 1]


def test_pre_objective_consecutive_and_solo_flags():
    epic = {"type": "ELITE_MONSTER_KILL", "timestamp": 650_000, "killerTeamId": 200,
            "monsterType": "DRAGON"}
    tl = _tl(
        _death(600_000, killer=2),                      # 솔로 + 오브젝트 직전
        _death(700_000, killer=2, assists=(3,)),        # 연속 데스, 어시스트 있음
        _death(1_200_000, killer=2),                    # 솔로
        _death(1_800_000, killer=0),                    # 처형 등 킬러 없음 -> 솔로 아님
        epic,
    )
    out = habits.detect_habits([(_match(), tl)], ME)
    assert out["deaths_total"] == 4
    assert out["pre_objective_deaths"] == 1 and out["pre_objective_death_rate"] == 0.25
    assert out["solo_deaths"] == 2 and out["solo_death_rate"] == 0.5
    assert out["consecutive_death_games"] == 1
    assert out["consecutive_death_game_rate"] == 1.0


def test_consecutive_rate_is_per_game():
    a = _tl(_death(600_000), _death(650_000))
    b = _tl(_death(600_000))
    out = habits.detect_habits([(_match(), a), (_match(), b), (_match(), b), (_match(), b)], ME)
    assert out["consecutive_death_games"] == 1
    assert out["consecutive_death_game_rate"] == 0.25


def test_first_back_ignores_start_items_and_other_players():
    tl = _tl(_buy(0), _buy(60_000), _buy(90_000), _buy(120_000, pid=2), _buy(200_000), _buy(500_000))
    assert habits.first_back_ms(tl, ME) == 200_000
    assert habits.first_back_ms(_tl(_buy(0)), ME) is None


def test_first_back_average_and_games_used():
    games = [(_match(), _tl(_buy(200_000))), (_match(), _tl(_buy(260_000))),
             (_match(), _tl(_buy(0))), (_match(), _tl()), (_match(), _tl(_buy(100_000)))]
    fb = habits.detect_habits(games, ME)["first_back_avg"]
    assert fb == {"time": "03:07", "seconds": 187, "games": 3}


def test_status_no_timeline_and_few_games():
    none = habits.detect_habits([], ME)
    assert none["status"] == "판단 보류(타임라인 없음)"
    assert none["games_with_timeline"] == 0 and none["win_compare"]["status"] == "판단 보류"
    few = habits.detect_habits([(_match(), _tl(_death(1000)))] * 4, ME)
    assert few["status"] == "판단 보류(표본 부족)" and few["games_with_timeline"] == 4
    ok = habits.detect_habits([(_match(), _tl(_death(1000)))] * 5, ME)
    assert ok["status"] == "ok"


def test_win_compare_requires_three_wins_and_three_losses():
    d1, d4 = _tl(_death(1000)), _tl(*[_death(1000 + i * 300_000) for i in range(4)])
    two_wins = [(_match(True), d1)] * 2 + [(_match(False), d4)] * 4
    assert habits.detect_habits(two_wins, ME)["win_compare"] == {
        "wins": 2, "losses": 4, "status": "판단 보류"}
    ok = [(_match(True), d1)] * 3 + [(_match(False), d4)] * 3
    wc = habits.detect_habits(ok, ME)["win_compare"]
    assert wc["status"] == "ok" and wc["wins"] == 3 and wc["losses"] == 3
    assert wc["win_games"]["deaths_per_game"] == 1.0
    assert wc["all_games"]["deaths_per_game"] == 2.5
    assert wc["diff_win_minus_all"]["deaths_per_game"] == -1.5
    assert wc["diff_win_minus_all"]["first_back_avg_s"] is None
