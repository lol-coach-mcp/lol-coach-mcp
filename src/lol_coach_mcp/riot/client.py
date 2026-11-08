"""Riot API 비동기 클라이언트."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import quote

import httpx

from ..config import Settings
from ..ratelimit import HostLimiters, parse_limit_header
from .errors import (
    RiotAuthError,
    RiotError,
    RiotNotFound,
    RiotRateLimited,
    RiotServerError,
)

DEFAULT_LIMITS = [(20, 1), (100, 120)]


def _q(part: str) -> str:
    return quote(part, safe="")


class RiotClient:
    def __init__(
        self,
        settings: Settings,
        http: httpx.AsyncClient | None = None,
        limiters: HostLimiters | None = None,
        max_retries: int = 3,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._settings = settings
        self._owns_http = http is None
        self._http = http or httpx.AsyncClient(timeout=30.0)
        self._limiters = limiters or HostLimiters(DEFAULT_LIMITS)
        self._max_retries = max_retries
        self._sleep = sleep

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def __aenter__(self) -> RiotClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def _get(self, host: str, path: str, params: dict | None = None) -> Any:
        limiter = self._limiters.for_host(host)
        headers = {
            "X-Riot-Token": self._settings.riot_api_key,
            "User-Agent": self._settings.user_agent,
        }
        for attempt in range(self._max_retries + 1):
            await limiter.acquire()
            resp = await self._http.get(host + path, params=params, headers=headers)
            self._update_limits(host, resp)
            status = resp.status_code
            if status == 200:
                return resp.json()
            if status in (401, 403):
                raise RiotAuthError()
            if status == 404:
                raise RiotNotFound(f"Riot API에서 대상을 찾을 수 없음: {path}")
            last = attempt >= self._max_retries
            if status == 429:
                if last:
                    raise RiotRateLimited("Riot API 호출 한도 초과. 재시도 횟수를 모두 소진함.")
                try:
                    wait = float(resp.headers["Retry-After"])
                except (KeyError, ValueError):
                    wait = float(2**attempt)
                await limiter.pause(wait)
                continue
            if status >= 500:
                if last:
                    raise RiotServerError(f"Riot API 서버 오류({status}). 재시도 횟수를 모두 소진함.")
                await self._sleep(float(2**attempt))
                continue
            raise RiotError(f"Riot API 예상치 못한 응답 코드: {status}")
        raise RiotError("Riot API 요청 실패")  # pragma: no cover

    def _update_limits(self, host: str, resp: httpx.Response) -> None:
        value = resp.headers.get("X-App-Rate-Limit")
        if not value:
            return
        try:
            limits = parse_limit_header(value)
        except ValueError:
            return
        if limits:
            self._limiters.for_host(host).update_limits(limits)

    @property
    def _kr(self) -> str:
        return self._settings.platform_host

    @property
    def _asia(self) -> str:
        return self._settings.regional_host

    async def get_account_by_riot_id(self, game_name: str, tag_line: str) -> dict:
        return await self._get(
            self._asia,
            f"/riot/account/v1/accounts/by-riot-id/{_q(game_name)}/{_q(tag_line)}",
        )

    async def get_account_by_puuid(self, puuid: str) -> dict:
        return await self._get(self._asia, f"/riot/account/v1/accounts/by-puuid/{_q(puuid)}")

    async def get_league_entries(self, puuid: str) -> list[dict]:
        return await self._get(self._kr, f"/lol/league/v4/entries/by-puuid/{_q(puuid)}")

    async def get_summoner(self, puuid: str) -> dict:
        return await self._get(self._kr, f"/lol/summoner/v4/summoners/by-puuid/{_q(puuid)}")

    async def get_match_ids(
        self,
        puuid: str,
        *,
        queue: int | None = None,
        start: int = 0,
        count: int = 100,
        start_time: int | None = None,
    ) -> list[str]:
        params: dict[str, int] = {"start": start, "count": max(1, min(100, count))}
        if queue is not None:
            params["queue"] = queue
        if start_time is not None:
            params["startTime"] = start_time
        return await self._get(
            self._asia, f"/lol/match/v5/matches/by-puuid/{_q(puuid)}/ids", params
        )

    async def get_match(self, match_id: str) -> dict:
        return await self._get(self._asia, f"/lol/match/v5/matches/{_q(match_id)}")

    async def get_timeline(self, match_id: str) -> dict:
        return await self._get(self._asia, f"/lol/match/v5/matches/{_q(match_id)}/timeline")

    async def get_top_mastery(self, puuid: str, count: int = 5) -> list[dict]:
        return await self._get(
            self._kr,
            f"/lol/champion-mastery/v4/champion-masteries/by-puuid/{_q(puuid)}/top",
            {"count": count},
        )
