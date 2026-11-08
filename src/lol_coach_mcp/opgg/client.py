"""OP.GG MCP 클라이언트(SQLite 캐시).

OP.GG 연결·응답 실패는 예외 대신 None으로 돌려준다. 호출 측은 해당 항목을
"OP.GG 데이터 없음"으로 표시하고 나머지(내 데이터)는 계속 진행한다.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable, Iterable
from contextlib import AbstractAsyncContextManager
from datetime import datetime, timedelta, timezone
from typing import Any

from mcp import Client

from ..store import Store
from .parser import OpggParseError, parse_opgg

logger = logging.getLogger(__name__)

OPGG_MCP_URL = "https://mcp-api.op.gg/mcp"
OPGG_LANG = "ko_KR"
CACHE_TTL = timedelta(hours=24)
CALL_TIMEOUT = 30.0  # 초. 접속 + 도구 호출 전체

POSITIONS = ("top", "jungle", "mid", "adc", "support")
TEAM_POSITION_MAP = {
    "TOP": "top",
    "JUNGLE": "jungle",
    "MIDDLE": "mid",
    "BOTTOM": "adc",
    "UTILITY": "support",
}
# 정규화된 대문자 키 → OP.GG champion 인자 예외 표. 2026-11-08 실측(Data Dragon 16.22.1 전
# 챔피언 vs lol_list_champions)에서 불일치 없음, 여러 단어 챔피언(TWISTEDFATE, MONKEYKING,
# MASTERYI)도 붙여 쓴 대문자로 정상 응답 — 대소문자 차이(FiddleSticks 등)는 키 비교로 흡수.
CHAMPION_KEY_EXCEPTIONS: dict[str, str] = {}

# desired_output_fields는 OP.GG가 정한 CLOSED SET — 문자열을 임의로 바꾸지 않음
CHAMPION_ANALYSIS_FIELDS = (
    "data.summary.average_stats.{win_rate,pick_rate,ban_rate,kda,tier,rank,play}",
    "data.core_items.{ids[],ids_names[],pick_rate,play,win}",
    "data.boots.{ids[],ids_names[],pick_rate,play,win}",
    "data.runes.{primary_page_name,primary_rune_names[],secondary_page_name,"
    "secondary_rune_names[],pick_rate,play,win}",
    "data.strong_counters[].{champion_id,champion_name,win_rate,play}",
    "data.weak_counters[].{champion_id,champion_name,win_rate,play}",
    "data.summoner_spells.{ids[],ids_names[],pick_rate,play,win}",
)
LANE_META_FIELDS = "data.positions.{pos}[].{{champion,win_rate,pick_rate,ban_rate,kda,tier,rank,play}}"
CHAMPIONS_FIELDS = ("data.champions[].{champion_id,key,name}",)

# lane_matchup 응답(약 46KB JSON)에서 남길 항목과 개수
MATCHUP_AVG_KEYS = ("play", "win_rate", "pick_rate", "ban_rate", "kda", "tier", "rank")
MATCHUP_BUILD_KEYS = ("ids", "ids_names", "play", "win", "pick_rate")
MATCHUP_RUNE_KEYS = ("primary_page_name", "primary_rune_names", "secondary_page_name",
                     "secondary_rune_names", "play", "win", "pick_rate")
MATCHUP_TEXT_KEYS = ("opponent_champion_tip", "lane_advantage_champion",
                     "lane_solo_kill_advantage_champion", "recommended_play_style")
MATCHUP_TOP_N = {"core_items": 3, "boots": 2, "summoner_spells": 2, "runes": 2}

Connect = Callable[[], AbstractAsyncContextManager[Any]]


def default_connect() -> AbstractAsyncContextManager[Any]:
    return Client(OPGG_MCP_URL)


def opgg_position(team_position: str | None) -> str | None:
    """Match-V5 teamPosition → OP.GG position. 알 수 없으면 None."""
    if not team_position:
        return None
    return TEAM_POSITION_MAP.get(team_position.upper())


def opgg_champion_key(match_champion_name: str, champions: Iterable[dict] | None) -> str:
    """Match-V5 championName → OP.GG 챔피언 인자(키를 대문자로, 예 MonkeyKing → MONKEYKING).

    champions는 `OpggClient.champions()` 결과. 목록에 없거나 목록이 없으면
    영숫자만 남겨 대문자로 바꾼 값을 돌려준다. 두 경로 모두 마지막에 예외 표를 적용한다.
    """
    wanted = match_champion_name.lower()
    result = None
    for c in champions or ():
        key = c.get("key")
        if isinstance(key, str) and key.lower() == wanted:
            result = key.upper()
            break
    if result is None:
        result = re.sub(r"[^A-Za-z0-9]", "", match_champion_name).upper()
    return CHAMPION_KEY_EXCEPTIONS.get(result, result)


def _check_position(position: str) -> None:
    if position not in POSITIONS:
        raise ValueError(f"OP.GG position은 {', '.join(POSITIONS)} 중 하나여야 함: {position}")


def _pick(d: dict, keys: Iterable[str]) -> dict:
    return {k: d[k] for k in keys if k in d}


def _extract_matchup(raw: dict) -> dict:
    data = raw.get("data") or {}
    opponent = raw.get("opponent_champion")
    avg = (data.get("summary") or {}).get("average_stats") or {}
    # counters[].win은 내 챔피언이 해당 상대를 이긴 횟수(실측: 카이사 vs 이즈리얼 0.5485,
    # champion_analysis strong_counters의 내 챔피언 승률 0.54와 일치)
    row = next((c for c in data.get("counters") or []
                if c.get("champion_name") == opponent), None)
    matchup = None
    if row and row.get("play"):
        matchup = {"play": row["play"], "win": row["win"],
                   "win_rate": round(row["win"] / row["play"], 4)}
    out: dict[str, Any] = {
        "position": raw.get("position"),
        "my_champion": raw.get("my_champion"),
        "opponent_champion": opponent,
        "average_stats": _pick(avg, MATCHUP_AVG_KEYS),
        "matchup": matchup,
    }
    for k in MATCHUP_TEXT_KEYS:
        out[k] = data.get(k)
    for k, n in MATCHUP_TOP_N.items():
        keys = MATCHUP_RUNE_KEYS if k == "runes" else MATCHUP_BUILD_KEYS
        out[k] = [_pick(x, keys) for x in (data.get(k) or [])[:n]]
    return out


class OpggClient:
    def __init__(
        self,
        store: Store,
        connect: Connect = default_connect,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        timeout: float = CALL_TIMEOUT,
        lang: str = OPGG_LANG,
    ) -> None:
        self._store = store
        self._connect = connect
        self._now = now
        self._timeout = timeout
        self._lang = lang

    async def champion_analysis(
        self, champion_key: str, position: str, tier: str | None
    ) -> dict | None:
        _check_position(position)
        args: dict[str, Any] = {
            "game_mode": "ranked",
            "champion": champion_key,
            "position": position,
            "lang": self._lang,
            "desired_output_fields": list(CHAMPION_ANALYSIS_FIELDS),
        }
        if tier:
            args["tier"] = tier.lower()
        return await self._cached("lol_get_champion_analysis", args, lambda r: r["data"])

    async def lane_matchup(self, position: str, my_key: str, opp_key: str) -> dict | None:
        _check_position(position)
        args = {"position": position, "my_champion": my_key,
                "opponent_champion": opp_key, "lang": self._lang}
        return await self._cached("lol_get_lane_matchup_guide", args, _extract_matchup)

    async def lane_meta(self, position: str) -> list[dict] | None:
        _check_position(position)
        args = {"position": position, "lang": self._lang,
                "desired_output_fields": [LANE_META_FIELDS.format(pos=position)]}
        return await self._cached("lol_list_lane_meta_champions", args,
                                  lambda r: r["data"]["positions"][position])

    async def champions(self) -> list[dict] | None:
        args = {"lang": self._lang, "desired_output_fields": list(CHAMPIONS_FIELDS)}
        return await self._cached("lol_list_champions", args,
                                  lambda r: r["data"]["champions"])

    async def _cached(self, tool: str, args: dict, extract: Callable[[Any], Any]) -> Any:
        key = f"{tool}:{json.dumps(args, sort_keys=True, ensure_ascii=False)}"
        now = self._now()
        hit = self._store.get_opgg_cache(key)
        if hit is not None:
            data, fetched_at = hit
            try:
                fresh = now - datetime.fromisoformat(fetched_at) < CACHE_TTL
            except ValueError:
                fresh = False
            if fresh:
                try:
                    return json.loads(data)
                except ValueError:
                    logger.warning("OP.GG %s 캐시 손상, 다시 요청함", tool)
        try:
            text = await asyncio.wait_for(self._call(tool, args), self._timeout)
        except Exception as e:  # noqa: BLE001 — 전송 계층 실패는 모두 "데이터 없음"
            # mcp Client는 접속·도구 오류를 anyio ExceptionGroup으로 감싸므로 넓게 잡음
            logger.warning("OP.GG %s 호출 실패: %s: %s", tool, type(e).__name__, e)
            return None
        if text is None:
            return None
        try:
            value = extract(parse_opgg(text))
        except (OpggParseError, KeyError, TypeError, IndexError, ValueError, RecursionError) as e:
            logger.warning("OP.GG %s 응답 해석 실패: %s: %s", tool, type(e).__name__, e)
            return None
        if value:  # 빈 결과([], {})는 일시적 상태일 수 있어 캐시하지 않음
            self._store.put_opgg_cache(
                key, json.dumps(value, ensure_ascii=False), now.isoformat()
            )
        return value

    async def _call(self, tool: str, args: dict) -> str | None:
        async with self._connect() as client:
            result = await client.call_tool(tool, args)
        if result.is_error or not result.content:
            logger.warning("OP.GG %s 오류 응답", tool)
            return None
        return result.content[0].text
