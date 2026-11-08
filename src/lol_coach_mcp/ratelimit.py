import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable

Limits = list[tuple[int, int]]


def parse_limit_header(value: str) -> Limits:
    """'100:120,20:1' -> [(100, 120), (20, 1)] (횟수, 초)"""
    result: Limits = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        count, seconds = part.split(":")[:2]
        result.append((int(count), int(seconds)))
    return result


class RateLimiter:
    def __init__(
        self,
        limits: Limits,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._clock = clock
        self._sleep = sleep
        self._lock = asyncio.Lock()
        self._paused_until = 0.0
        self._history: dict[int, deque[float]] = {}
        self._limits: Limits = []
        self.update_limits(limits)

    def update_limits(self, limits: Limits) -> None:
        self._limits = list(limits)
        self._history = {
            seconds: self._history.get(seconds, deque()) for _, seconds in self._limits
        }

    async def pause(self, seconds: float) -> None:
        self._paused_until = max(self._paused_until, self._clock() + seconds)

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = self._clock()
                wait = max(0.0, self._paused_until - now)
                for count, seconds in self._limits:
                    q = self._history[seconds]
                    while q and q[0] <= now - seconds:
                        q.popleft()
                    if len(q) >= count:
                        wait = max(wait, q[0] + seconds - now)
                if wait <= 0:
                    break
                await self._sleep(wait)
            now = self._clock()
            for q in self._history.values():
                q.append(now)


class HostLimiters:
    def __init__(self, default_limits: Limits) -> None:
        self._default = list(default_limits)
        self._limiters: dict[str, RateLimiter] = {}

    def for_host(self, host: str) -> RateLimiter:
        if host not in self._limiters:
            self._limiters[host] = RateLimiter(self._default)
        return self._limiters[host]
