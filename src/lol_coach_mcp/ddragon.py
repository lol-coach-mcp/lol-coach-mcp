"""Data Dragon 정적 데이터(챔피언·아이템·룬), 버전별 SQLite 캐시."""
from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from . import __version__
from .store import Store

DDRAGON_HOST = "https://ddragon.leagueoflegends.com"
DDRAGON_LANG = "ko_KR"
VERSION_TTL = timedelta(hours=24)
DEFAULT_USER_AGENT = f"lol-coach-mcp/{__version__}"
_VERSIONS_KEY = "versions"


class DataDragon:
    def __init__(
        self,
        store: Store,
        http: httpx.AsyncClient,
        *,
        user_agent: str = DEFAULT_USER_AGENT,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        lang: str = DDRAGON_LANG,
    ) -> None:
        self._store = store
        self._http = http
        self._headers = {"User-Agent": user_agent}
        self._now = now
        self._lang = lang

    async def _get_json(self, path: str) -> Any:
        resp = await self._http.get(DDRAGON_HOST + path, headers=self._headers)
        resp.raise_for_status()
        return resp.json()

    async def version(self) -> str:
        """최신 버전. 24시간 캐시, 갱신 실패 시 이전 값 사용(없으면 httpx.HTTPError)."""
        now = self._now()
        hit = self._store.get_ddragon_cache(_VERSIONS_KEY)
        if hit is not None:
            _, cached_version, fetched_at = hit
            try:
                if now - datetime.fromisoformat(fetched_at) < VERSION_TTL:
                    return cached_version
            except ValueError:
                pass
        try:
            versions = await self._get_json("/api/versions.json")
        except httpx.HTTPError:
            if hit is not None:
                return hit[1]
            raise
        latest = str(versions[0])
        self._store.put_ddragon_cache(
            _VERSIONS_KEY, json.dumps(versions[:5]), latest, now.isoformat()
        )
        if hit is None or hit[1] != latest:
            self._store.prune_ddragon_cache(latest)
        return latest

    async def _data(self, name: str, transform: Callable[[Any], Any]) -> Any:
        v = await self.version()
        key = f"{name}:{v}"
        hit = self._store.get_ddragon_cache(key)
        if hit is not None:
            return json.loads(hit[0])
        raw = await self._get_json(f"/cdn/{v}/data/{self._lang}/{name}.json")
        value = transform(raw)
        self._store.put_ddragon_cache(
            key, json.dumps(value, ensure_ascii=False), v, self._now().isoformat()
        )
        return value

    async def champions(self) -> dict[str, dict]:
        """챔피언 키(예 "MonkeyKing") → {id(숫자), name(한국어), tags}."""
        def transform(raw: dict) -> dict:
            return {
                c["id"]: {"id": int(c["key"]), "name": c["name"], "tags": list(c.get("tags", []))}
                for c in raw["data"].values()
            }
        return await self._data("champion", transform)

    async def items(self) -> dict[int, dict]:
        """아이템 id → {name, gold(총액), tags}."""
        def transform(raw: dict) -> dict:
            return {
                item_id: {"name": it.get("name", ""),
                          "gold": (it.get("gold") or {}).get("total"),
                          "tags": list(it.get("tags", []))}
                for item_id, it in raw["data"].items()
            }
        got = await self._data("item", transform)
        return {int(k): v for k, v in got.items()}

    async def runes(self) -> dict[int, dict]:
        """룬·룬 트리 id → {name, key, tree(소속 트리 이름)}."""
        def transform(raw: list) -> dict:
            out: dict[str, dict] = {}
            for tree in raw:
                out[str(tree["id"])] = {"name": tree["name"], "key": tree["key"],
                                        "tree": tree["name"]}
                for slot in tree.get("slots", []):
                    for r in slot.get("runes", []):
                        out[str(r["id"])] = {"name": r["name"], "key": r["key"],
                                             "tree": tree["name"]}
            return out
        got = await self._data("runesReforged", transform)
        return {int(k): v for k, v in got.items()}
