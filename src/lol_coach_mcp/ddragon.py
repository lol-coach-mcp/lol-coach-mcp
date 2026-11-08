"""Data Dragon 정적 데이터(챔피언·아이템·룬), 버전별 SQLite 캐시."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from . import __version__
from .store import Store

DDRAGON_HOST = "https://ddragon.leagueoflegends.com"
DDRAGON_LANG = "ko_KR"
VERSION_TTL = timedelta(hours=24)
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
MAP_FILE = "map11"  # 소환사의 협곡 미니맵
DEFAULT_USER_AGENT = f"lol-coach-mcp/{__version__}"
_VERSIONS_KEY = "versions"

logger = logging.getLogger(__name__)


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
        # 데이터 종류("champion", "item", "runesReforged") → 실제 사용한 버전 / 직전 버전 대체 여부
        self.used_versions: dict[str, str] = {}
        self.fallbacks: set[str] = set()

    async def _get_json(self, path: str) -> Any:
        resp = await self._http.get(DDRAGON_HOST + path, headers=self._headers)
        resp.raise_for_status()
        return resp.json()

    async def version(self) -> str:
        """최신 버전. 24시간 캐시, 갱신 실패 시 이전 값 사용(없으면 httpx.HTTPError).

        이전 값으로 대체하면 fetched_at을 갱신해 TTL 동안 다시 요청하지 않는다.
        """
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
            latest = versions[0]
            if not isinstance(latest, str) or not latest:
                raise ValueError(f"버전 형식 오류: {latest!r}")
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as e:
            if hit is None:
                if isinstance(e, httpx.HTTPError):
                    raise
                raise httpx.DecodingError(f"Data Dragon versions.json 형식 오류: {e}") from e
            logger.warning("Data Dragon 버전 갱신 실패, 이전 버전 %s 사용: %s", hit[1], e)
            self._store.put_ddragon_cache(_VERSIONS_KEY, hit[0], hit[1], now.isoformat())
            return hit[1]
        self._store.put_ddragon_cache(
            _VERSIONS_KEY, json.dumps(versions[:5]), latest, now.isoformat()
        )
        return latest

    async def _data(
        self, name: str, transform: Callable[[Any], Any], cache_name: str | None = None
    ) -> Any:
        """버전별 데이터. 새 버전 CDN이 아직 없으면(지연·오류) 직전 버전 캐시로 대체.

        실제 사용한 버전은 used_versions[name], 대체 여부는 name in fallbacks.
        이전 버전 캐시는 새 버전 데이터를 처음 저장한 뒤에만 지운다.
        """
        v = await self.version()
        cname = cache_name or name  # 응답 형식이 바뀌면 캐시 이름만 바꿔 이전 캐시를 무시
        key = f"{cname}:{v}"
        hit = self._store.get_ddragon_cache(key)
        if hit is not None:
            try:
                value = json.loads(hit[0])
            except ValueError:
                value = None
            if value is not None:
                self._mark(name, v, fallback=False)
                return value
        try:
            raw = await self._get_json(f"/cdn/{v}/data/{self._lang}/{name}.json")
            value = transform(raw)
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as e:
            prev = self._store.latest_ddragon_cache(f"{cname}:")
            if prev is None:
                if isinstance(e, httpx.HTTPError):
                    raise
                raise httpx.DecodingError(f"Data Dragon {name}.json 형식 오류: {e}") from e
            logger.warning("Data Dragon %s %s 사용 불가, %s 데이터로 대체: %s",
                           name, v, prev[1], e)
            self._mark(name, prev[1], fallback=True)
            return json.loads(prev[0])
        self._store.put_ddragon_cache(
            key, json.dumps(value, ensure_ascii=False), v, self._now().isoformat()
        )
        self._store.prune_ddragon_cache(v, prefix=f"{cname}:")
        self._mark(name, v, fallback=False)
        return value

    def _mark(self, name: str, version: str, *, fallback: bool) -> None:
        self.used_versions[name] = version
        if fallback:
            self.fallbacks.add(name)
        else:
            self.fallbacks.discard(name)

    async def champions(self) -> dict[str, dict]:
        """챔피언 키(예 "MonkeyKing") → {id(숫자), name(한국어), tags}."""
        def transform(raw: dict) -> dict:
            return {
                c["id"]: {"id": int(c["key"]), "name": c["name"], "tags": list(c.get("tags", []))}
                for c in raw["data"].values()
            }
        return await self._data("champion", transform)

    async def items(self) -> dict[int, dict]:
        """아이템 id → {name, gold(총액), tags, into(상위 아이템 id), from(재료 id)}."""
        def transform(raw: dict) -> dict:
            return {
                item_id: {"name": it.get("name", ""),
                          "gold": (it.get("gold") or {}).get("total"),
                          "tags": list(it.get("tags", [])),
                          "into": [int(i) for i in it.get("into", [])],
                          "from": [int(i) for i in it.get("from", [])]}
                for item_id, it in raw["data"].items()
            }
        got = await self._data("item", transform, cache_name="item3")
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

    async def map_image(self, cache_dir: Path) -> bytes:
        """소환사의 협곡 미니맵 PNG. 버전별로 cache_dir에 저장해 재사용함.

        다운로드 실패나 PNG가 아닌 응답이면 httpx.HTTPError.
        """
        v = await self.version()
        path = Path(cache_dir) / f"{MAP_FILE}-{v}.png"
        try:
            cached = path.read_bytes()
        except OSError:
            cached = b""
        if cached.startswith(PNG_MAGIC):
            return cached
        data = (await self._get_bytes(f"/cdn/{v}/img/map/{MAP_FILE}.png"))
        if not data.startswith(PNG_MAGIC):
            raise httpx.DecodingError("Data Dragon 미니맵이 PNG 형식이 아님")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        for old in path.parent.glob(f"{MAP_FILE}-*.png"):
            if old != path:
                try:
                    old.unlink()
                except OSError:
                    pass
        return data

    async def _get_bytes(self, path: str) -> bytes:
        resp = await self._http.get(DDRAGON_HOST + path, headers=self._headers)
        resp.raise_for_status()
        return resp.content
