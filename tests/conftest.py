import json
from pathlib import Path
from typing import Any

PUUID_ME = "puuid-me"
FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES_DIR / f"{name}.json").read_text(encoding="utf-8"))


def make_synthetic_match(match_id: str, puuid: str = PUUID_ME, *, queue: int = 420) -> dict:
    """extract_match_stats가 읽는 최소 필드만 가진 가벼운 합성 경기(동기화 테스트용)."""
    creation = 1_700_000_000_000 + int(match_id.split("_")[1])

    def player(pid: str, team: int) -> dict:
        return {
            "puuid": pid, "teamId": team, "win": team == 100, "championName": "Jayce",
            "teamPosition": "TOP", "kills": 3, "deaths": 2, "assists": 4,
            "totalMinionsKilled": 150, "neutralMinionsKilled": 10, "goldEarned": 9000,
            "visionScore": 20, "totalDamageDealtToChampions": 15000,
        }

    return {
        "metadata": {"matchId": match_id},
        "info": {
            "gameCreation": creation, "gameDuration": 1800, "gameVersion": "16.21.1",
            "queueId": queue,
            "participants": [player(puuid, 100), player(f"{puuid}-mate", 100), player(f"{puuid}-foe", 200)],
        },
    }


def make_synthetic_timeline() -> dict:
    return {"metadata": {}, "info": {"frameInterval": 60000, "frames": []}}
