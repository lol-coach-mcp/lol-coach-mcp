"""SQLite 저장소 (동기 sqlite3)."""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from lol_coach_mcp.stats import MatchStats

SCHEMA_VERSION = 6

TONES = ("직설", "격려", "데이터만")
DETAIL_LEVELS = ("초보", "보통", "숙련")

_SCHEMA_V1 = """
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

# champion은 '' 이면 "챔피언 무관"(PK가 NULL을 허용하지 않도록)
_SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS baselines (
    tier TEXT NOT NULL,
    position TEXT NOT NULL,
    champion TEXT NOT NULL DEFAULT '',
    metric TEXT NOT NULL,
    mean REAL NOT NULL,
    std REAL,
    n INTEGER,
    patch TEXT,
    collected_at TEXT,
    source TEXT,
    PRIMARY KEY (tier, position, champion, metric)
);
"""

# 외부 정적·통계 데이터 캐시(개인 데이터 아님 → forget_all 대상 아님)
_SCHEMA_V3 = """
CREATE TABLE IF NOT EXISTS opgg_cache (
    key TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ddragon_cache (
    key TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    version TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);
"""

# 코치 기억(사용자 데이터 → forget_all 대상). created_at은 KST ISO 문자열
_SCHEMA_V4 = """
CREATE TABLE IF NOT EXISTS coach_findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    kind TEXT NOT NULL,
    metric TEXT NOT NULL,
    my_value REAL,
    ref_value REAL,
    ref_source TEXT,
    games INTEGER,
    period_desc TEXT,
    season_major TEXT,
    position TEXT
);
CREATE TABLE IF NOT EXISTS goals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    metric TEXT NOT NULL,
    direction TEXT NOT NULL,
    target REAL NOT NULL,
    baseline_value REAL,
    start_after_ms INTEGER NOT NULL,
    games_to_track INTEGER NOT NULL,
    position TEXT,
    status TEXT NOT NULL DEFAULT '진행 중',
    closed_at TEXT
);
CREATE TABLE IF NOT EXISTS journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    match_id TEXT,
    text TEXT NOT NULL
);
"""

# 기준선 수집 진행·표본(개인 데이터 아님 → forget_all 대상 아님).
# 표본에는 식별자를 넣지 않음. baseline_seen은 시드·매치 중복 제외용 솔트 해시만 보관하며 export 대상이 아님.
BASELINE_METRIC_COLUMNS = (
    "kda", "cs_per_min", "gold_per_min", "vision_per_min",
    "damage_share", "kill_participation", "deaths", "win",
)
_SCHEMA_V5 = """
CREATE TABLE IF NOT EXISTS baseline_jobs (
    tier TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS baseline_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tier TEXT NOT NULL,
    position TEXT NOT NULL,
    champion TEXT,
    kda REAL, cs_per_min REAL, gold_per_min REAL, vision_per_min REAL,
    damage_share REAL, kill_participation REAL, deaths REAL, win REAL,
    patch TEXT
);
CREATE INDEX IF NOT EXISTS idx_baseline_samples_group ON baseline_samples (tier, position);
CREATE TABLE IF NOT EXISTS baseline_seen (
    kind TEXT NOT NULL,
    key_hash TEXT NOT NULL,
    tier TEXT NOT NULL,
    PRIMARY KEY (kind, key_hash)
);
"""

# 출처별로 같은 키의 기준선을 함께 보관(PK에 source 포함). 조회 시 사용자 수집이 우선
_SCHEMA_V6 = """
CREATE TABLE baselines_v6 (
    tier TEXT NOT NULL,
    position TEXT NOT NULL,
    champion TEXT NOT NULL DEFAULT '',
    metric TEXT NOT NULL,
    mean REAL NOT NULL,
    std REAL,
    n INTEGER,
    patch TEXT,
    collected_at TEXT,
    source TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (tier, position, champion, metric, source)
);
INSERT INTO baselines_v6 (tier, position, champion, metric, mean, std, n, patch, collected_at, source)
    SELECT tier, position, champion, metric, mean, std, n, patch, collected_at, COALESCE(source, '')
    FROM baselines;
DROP TABLE baselines;
ALTER TABLE baselines_v6 RENAME TO baselines;
"""

SOURCE_USER = "사용자 수집"  # 기준선 출처: 직접 수집(배포보다 우선)
SOURCE_DIST = "배포"  # 기준선 출처: 패키지에 포함된 배포 파일

GOAL_STATUSES = ("진행 중", "달성", "미달", "종료")
_GOAL_UPDATABLE = ("status", "closed_at", "target", "games_to_track")
MEMORY_TABLES = ("coach_findings", "goals", "journal")

# (도달 버전, 스크립트) — 낮은 버전부터 차례로 적용
_MIGRATIONS = (
    (1, _SCHEMA_V1), (2, _SCHEMA_V2), (3, _SCHEMA_V3), (4, _SCHEMA_V4), (5, _SCHEMA_V5),
    (6, _SCHEMA_V6),
)


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
        for target, script in _MIGRATIONS:
            if version < target:
                with self._conn:
                    self._conn.executescript(script)
                    self._conn.execute(f"PRAGMA user_version = {target}")
                version = target

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

    def list_rank_snapshots(self, limit: int | None = None) -> list[tuple[str, list[dict]]]:
        """랭크 스냅샷을 시간순(오래된 것 먼저)으로 반환. limit이면 가장 최근 limit개."""
        rows = self._conn.execute(
            "SELECT taken_at, data FROM rank_snapshots ORDER BY taken_at DESC, id DESC"
            + (" LIMIT ?" if limit is not None else ""),
            (limit,) if limit is not None else (),
        ).fetchall()
        return [(r["taken_at"], json.loads(r["data"])) for r in reversed(rows)]

    # 기준선
    def get_baseline(
        self, tier: str, position: str, metric: str, champion: str | None = None
    ) -> dict | None:
        row = self._conn.execute(
            "SELECT mean, std, n, patch, collected_at, source FROM baselines "
            "WHERE tier = ? AND position = ? AND champion = ? AND metric = ? "
            "ORDER BY (source = ?) DESC, collected_at DESC LIMIT 1",
            (tier, position, champion or "", metric, SOURCE_USER),
        ).fetchone()
        return dict(row) if row else None

    def baseline_summary(self) -> dict | None:
        """저장된 기준선 요약 (티어·포지션별 지표 수, 출처, 패치, 수집일 범위). 없으면 None."""
        groups = self._conn.execute(
            "SELECT tier, position, COUNT(DISTINCT metric) AS metrics, MIN(n) AS min_n FROM baselines "
            "GROUP BY tier, position ORDER BY tier, position"
        ).fetchall()
        if not groups:
            return None

        def distinct(col: str) -> list[str]:
            rows = self._conn.execute(
                f"SELECT DISTINCT {col} FROM baselines WHERE {col} IS NOT NULL ORDER BY {col}"
            ).fetchall()
            return [r[0] for r in rows]

        rng = self._conn.execute(
            "SELECT MIN(collected_at) AS lo, MAX(collected_at) AS hi, COUNT(*) AS total "
            "FROM baselines"
        ).fetchone()
        return {
            "total_rows": rng["total"],
            "groups": [dict(g) for g in groups],
            "sources": distinct("source"),
            "patches": distinct("patch"),
            "collected_at": {"min": rng["lo"], "max": rng["hi"]},
        }

    def upsert_baselines(self, rows: list[dict]) -> None:
        with self._conn:
            self._conn.executemany(
                "INSERT OR REPLACE INTO baselines (tier, position, champion, metric, mean, std, "
                "n, patch, collected_at, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (r["tier"], r["position"], r.get("champion") or "", r["metric"], r["mean"],
                     r.get("std"), r.get("n"), r.get("patch"), r.get("collected_at"),
                     r.get("source") or "")
                    for r in rows
                ],
            )

    def list_baselines(self, source: str | None = None) -> list[dict]:
        sql, params = "SELECT * FROM baselines", []
        if source is not None:
            sql += " WHERE source = ?"
            params.append(source)
        sql += " ORDER BY tier, position, champion, metric"
        return [dict(r) for r in self._conn.execute(sql, params)]

    def replace_baselines_by_source(self, source: str, rows: list[dict]) -> None:
        """해당 출처의 기준선 행을 모두 지우고 rows로 교체(한 트랜잭션)."""
        with self._conn:
            self._conn.execute("DELETE FROM baselines WHERE source = ?", (source,))
        self.upsert_baselines(rows)

    # 기준선 수집 진행
    def _baseline_salt(self) -> str:
        salt = self.get_meta("baseline_salt")
        if salt is None:
            salt = secrets.token_hex(16)
            self.set_meta("baseline_salt", salt)
        return salt

    def baseline_key_hash(self, kind: str, value: str) -> str:
        return hashlib.sha256(f"{self._baseline_salt()}:{kind}:{value}".encode()).hexdigest()

    def baseline_seen(self, kind: str, value: str) -> bool:
        return self._conn.execute(
            "SELECT 1 FROM baseline_seen WHERE kind = ? AND key_hash = ?",
            (kind, self.baseline_key_hash(kind, value)),
        ).fetchone() is not None

    def baseline_mark_seen(self, kind: str, value: str, tier: str) -> None:
        h = self.baseline_key_hash(kind, value)
        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO baseline_seen (kind, key_hash, tier) VALUES (?, ?, ?)",
                (kind, h, tier),
            )

    def add_baseline_match(self, tier: str, match_id: str, samples: list[dict]) -> bool:
        """매치 1건의 표본을 저장하고 매치를 처리됨으로 표시(원자적). 이미 처리된 매치면 False."""
        h = self.baseline_key_hash("match", match_id)
        cols = ("position", "champion", "patch", *BASELINE_METRIC_COLUMNS)
        with self._conn:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO baseline_seen (kind, key_hash, tier) VALUES ('match', ?, ?)",
                (h, tier),
            )
            if cur.rowcount == 0:
                return False
            self._conn.executemany(
                f"INSERT INTO baseline_samples (tier, {', '.join(cols)}) "
                f"VALUES (?, {', '.join('?' for _ in cols)})",
                [(tier, *(sm.get(c) for c in cols)) for sm in samples],
            )
        return True

    def baseline_match_count(self, tier: str) -> int:
        return int(self._conn.execute(
            "SELECT COUNT(*) FROM baseline_seen WHERE kind = 'match' AND tier = ?", (tier,)
        ).fetchone()[0])

    def baseline_sample_count(self, tier: str | None = None) -> int:
        sql, params = "SELECT COUNT(*) FROM baseline_samples", []
        if tier is not None:
            sql += " WHERE tier = ?"
            params.append(tier)
        return int(self._conn.execute(sql, params).fetchone()[0])

    def list_baseline_samples(self) -> list[dict]:
        return [dict(r) for r in self._conn.execute("SELECT * FROM baseline_samples ORDER BY id")]

    def get_baseline_job(self, tier: str) -> dict | None:
        row = self._conn.execute(
            "SELECT state FROM baseline_jobs WHERE tier = ?", (tier,)
        ).fetchone()
        return json.loads(row["state"]) if row else None

    def set_baseline_job(self, tier: str, state: dict, updated_at: str) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO baseline_jobs (tier, state, updated_at) VALUES (?, ?, ?)",
                (tier, _dumps(state), updated_at),
            )

    def reset_baseline_tier(self, tier: str) -> None:
        with self._conn:
            for t in ("baseline_jobs", "baseline_samples", "baseline_seen"):
                self._conn.execute(f"DELETE FROM {t} WHERE tier = ?", (tier,))  # 고정 테이블명

    # 메타
    def get_meta(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value)
            )

    # OP.GG 캐시
    def get_opgg_cache(self, key: str) -> tuple[str, str] | None:
        row = self._conn.execute(
            "SELECT data, fetched_at FROM opgg_cache WHERE key = ?", (key,)
        ).fetchone()
        return (row["data"], row["fetched_at"]) if row else None

    def put_opgg_cache(self, key: str, data: str, fetched_at: str) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO opgg_cache (key, data, fetched_at) VALUES (?, ?, ?)",
                (key, data, fetched_at),
            )

    # Data Dragon 캐시
    def get_ddragon_cache(self, key: str) -> tuple[str, str, str] | None:
        row = self._conn.execute(
            "SELECT data, version, fetched_at FROM ddragon_cache WHERE key = ?", (key,)
        ).fetchone()
        return (row["data"], row["version"], row["fetched_at"]) if row else None

    def put_ddragon_cache(self, key: str, data: str, version: str, fetched_at: str) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO ddragon_cache (key, data, version, fetched_at) "
                "VALUES (?, ?, ?, ?)",
                (key, data, version, fetched_at),
            )

    def latest_ddragon_cache(self, prefix: str) -> tuple[str, str, str] | None:
        """key가 prefix로 시작하는 캐시 중 가장 최근 저장된 행(data, version, fetched_at)."""
        row = self._conn.execute(
            "SELECT data, version, fetched_at FROM ddragon_cache "
            "WHERE substr(key, 1, ?) = ? ORDER BY fetched_at DESC LIMIT 1",
            (len(prefix), prefix),
        ).fetchone()
        return (row["data"], row["version"], row["fetched_at"]) if row else None

    def prune_ddragon_cache(self, keep_version: str, prefix: str | None = None) -> None:
        """버전 목록('versions')을 제외하고 keep_version이 아닌 캐시를 지움.

        prefix가 있으면 key가 그 접두어로 시작하는 행만 대상으로 한다.
        """
        sql = "DELETE FROM ddragon_cache WHERE key <> 'versions' AND version <> ?"
        params: list[Any] = [keep_version]
        if prefix is not None:
            sql += " AND substr(key, 1, ?) = ?"
            params += [len(prefix), prefix]
        with self._conn:
            self._conn.execute(sql, params)

    # 코치 판정 기록
    def add_findings(self, rows: list[dict]) -> int:
        """저장한 행 수를 반환. 같은 KST 날짜(created_at 앞 10자)에 같은 kind+metric은 건너뜀."""
        added = 0
        with self._conn:
            for r in rows:
                dup = self._conn.execute(
                    "SELECT 1 FROM coach_findings WHERE kind = ? AND metric = ? "
                    "AND substr(created_at, 1, 10) = ?",
                    (r["kind"], r["metric"], r["created_at"][:10]),
                ).fetchone()
                if dup:
                    continue
                self._conn.execute(
                    "INSERT INTO coach_findings (created_at, kind, metric, my_value, ref_value, "
                    "ref_source, games, period_desc, season_major, position) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (r["created_at"], r["kind"], r["metric"], r.get("my_value"),
                     r.get("ref_value"), r.get("ref_source"), r.get("games"),
                     r.get("period_desc"), r.get("season_major"), r.get("position")),
                )
                added += 1
        return added

    def list_findings(self, limit: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM coach_findings ORDER BY created_at DESC, id DESC LIMIT ?", (limit,)
        )
        return [dict(r) for r in rows]

    # 집중 과제
    def add_goal(self, g: dict) -> int:
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO goals (created_at, metric, direction, target, baseline_value, "
                "start_after_ms, games_to_track, position) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (g["created_at"], g["metric"], g["direction"], g["target"],
                 g.get("baseline_value"), g["start_after_ms"], g["games_to_track"],
                 g.get("position")),
            )
        return int(cur.lastrowid)

    def get_goal(self, goal_id: int) -> dict | None:
        row = self._conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
        return dict(row) if row else None

    def list_goals(self, status: str | None = None) -> list[dict]:
        sql, params = "SELECT * FROM goals", []
        if status is not None:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY id DESC"
        return [dict(r) for r in self._conn.execute(sql, params)]

    def update_goal(self, goal_id: int, **fields: Any) -> None:
        if not fields or set(fields) - set(_GOAL_UPDATABLE):
            raise ValueError(f"수정할 수 없는 필드: {sorted(set(fields) - set(_GOAL_UPDATABLE))}")
        if "status" in fields and fields["status"] not in GOAL_STATUSES:
            raise ValueError(f"알 수 없는 상태: {fields['status']}")
        assignments = ", ".join(f"{k} = ?" for k in fields)  # 키는 허용 목록으로 검증됨
        with self._conn:
            self._conn.execute(
                f"UPDATE goals SET {assignments} WHERE id = ?", [*fields.values(), goal_id]
            )

    # 일지
    def add_journal(self, created_at: str, match_id: str | None, text: str) -> int:
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO journal (created_at, match_id, text) VALUES (?, ?, ?)",
                (created_at, match_id, text),
            )
        return int(cur.lastrowid)

    def get_journal(self, entry_id: int) -> dict | None:
        row = self._conn.execute("SELECT * FROM journal WHERE id = ?", (entry_id,)).fetchone()
        return dict(row) if row else None

    def list_journal(self, limit: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM journal ORDER BY created_at DESC, id DESC LIMIT ?", (limit,)
        )
        return [dict(r) for r in rows]

    def forget_all(self, include_matches: bool = False) -> None:
        tables = ["profile", "rank_snapshots", *MEMORY_TABLES]
        if include_matches:
            tables += ["matches", "timelines", "my_match_stats"]
        with self._conn:
            for t in tables:
                self._conn.execute(f"DELETE FROM {t}")  # 고정 테이블명, 사용자 입력 아님
            # 기준선 솔트는 항상 유지(재개 시 중복 제외 해시가 깨지지 않도록).
            # 데이터 소유자 표시는 include_matches가 아닐 때 남겨 다른 계정 등록 시 오염을 막음
            keep = ("baseline_salt",) if include_matches else ("baseline_salt", "data_owner_puuid")
            self._conn.execute(
                f"DELETE FROM meta WHERE key NOT IN ({', '.join('?' for _ in keep)})", keep)
