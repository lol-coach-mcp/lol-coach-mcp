"""SQLite 저장소 (동기 sqlite3)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from lol_coach_mcp.stats import MatchStats

SCHEMA_VERSION = 1

TONES = ("직설", "격려", "데이터만")
DETAIL_LEVELS = ("초보", "보통", "숙련")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS profile (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    puuid TEXT NOT NULL,
    game_name TEXT NOT NULL,
    tag_line TEXT NOT NULL,
    main_position TEXT,
    goal_tier TEXT,
    tone TEXT NOT NULL,
    detail_level TEXT NOT NULL,
    registered_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS matches (
    match_id TEXT PRIMARY KEY,
    queue_id INTEGER,
    game_creation INTEGER,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS timelines (
    match_id TEXT PRIMARY KEY,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS my_match_stats (
    match_id TEXT PRIMARY KEY,
    queue_id INTEGER NOT NULL,
    game_creation INTEGER NOT NULL,
    is_remake INTEGER NOT NULL,
    json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_stats_creation ON my_match_stats (game_creation);
CREATE TABLE IF NOT EXISTS rank_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    taken_at TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@dataclass
class Profile:
    puuid: str
    game_name: str
    tag_line: str
    main_position: str | None = None
    goal_tier: str | None = None
    tone: str = "격려"
    detail_level: str = "보통"
    registered_at: str = ""


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


class Store:
    def __init__(self, db_path: Path) -> None:
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        if version < SCHEMA_VERSION:
            with self._conn:
                self._conn.executescript(_SCHEMA)
                self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def close(self) -> None:
        self._conn.close()

    # 프로필
    def get_profile(self) -> Profile | None:
        row = self._conn.execute(
            "SELECT puuid, game_name, tag_line, main_position, goal_tier, tone, "
            "detail_level, registered_at FROM profile WHERE id = 1"
        ).fetchone()
        return Profile(**dict(row)) if row else None

    def save_profile(self, p: Profile) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO profile (id, puuid, game_name, tag_line, "
                "main_position, goal_tier, tone, detail_level, registered_at) "
                "VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)",
                (p.puuid, p.game_name, p.tag_line, p.main_position, p.goal_tier,
                 p.tone, p.detail_level, p.registered_at),
            )

    def delete_profile(self) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM profile")

    # 경기
    def has_match(self, match_id: str) -> bool:
        return self._conn.execute(
            "SELECT 1 FROM matches WHERE match_id = ?", (match_id,)
        ).fetchone() is not None

    def save_match(self, match_id: str, data: dict, stats: MatchStats) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO matches (match_id, queue_id, game_creation, data) "
                "VALUES (?, ?, ?, ?)",
                (match_id, stats.queue_id, stats.game_creation, _dumps(data)),
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO my_match_stats "
                "(match_id, queue_id, game_creation, is_remake, json) VALUES (?, ?, ?, ?, ?)",
                (match_id, stats.queue_id, stats.game_creation, int(stats.is_remake),
                 _dumps(asdict(stats))),
            )

    def get_match(self, match_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT data FROM matches WHERE match_id = ?", (match_id,)
        ).fetchone()
        return json.loads(row["data"]) if row else None

    def save_timeline(self, match_id: str, data: dict) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO timelines (match_id, data) VALUES (?, ?)",
                (match_id, _dumps(data)),
            )

    def get_timeline(self, match_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT data FROM timelines WHERE match_id = ?", (match_id,)
        ).fetchone()
        return json.loads(row["data"]) if row else None

    # 내 지표
    def list_my_stats(
        self,
        *,
        queue: int | None = 420,
        limit: int | None = None,
        include_remakes: bool = False,
    ) -> list[MatchStats]:
        sql = "SELECT json FROM my_match_stats WHERE 1 = 1"
        params: list[Any] = []
        if queue is not None:
            sql += " AND queue_id = ?"
            params.append(queue)
        if not include_remakes:
            sql += " AND is_remake = 0"
        sql += " ORDER BY game_creation DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        out = []
        for row in self._conn.execute(sql, params):
            d = json.loads(row["json"])
            d["teammates"] = tuple(d["teammates"])
            out.append(MatchStats(**d))
        return out

    # 랭크
    def add_rank_snapshot(self, entries: list[dict], taken_at: str) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO rank_snapshots (taken_at, data) VALUES (?, ?)",
                (taken_at, _dumps(entries)),
            )

    def latest_rank_snapshot(self) -> tuple[str, list[dict]] | None:
        row = self._conn.execute(
            "SELECT taken_at, data FROM rank_snapshots ORDER BY taken_at DESC, id DESC LIMIT 1"
        ).fetchone()
        return (row["taken_at"], json.loads(row["data"])) if row else None

    # 메타
    def get_meta(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value)
            )

    def forget_all(self, include_matches: bool = False) -> None:
        tables = ["profile", "rank_snapshots", "meta"]
        if include_matches:
            tables += ["matches", "timelines", "my_match_stats"]
        with self._conn:
            for t in tables:
                self._conn.execute(f"DELETE FROM {t}")  # 고정 테이블명, 사용자 입력 아님
