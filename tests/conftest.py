import json
from pathlib import Path
from typing import Any

PUUID_ME = "puuid-me"
FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES_DIR / f"{name}.json").read_text(encoding="utf-8"))
