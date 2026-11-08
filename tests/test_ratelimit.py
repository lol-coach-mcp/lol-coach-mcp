from lol_coach_mcp.ratelimit import HostLimiters, RateLimiter, parse_limit_header


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept = 0.0
        self.calls = 0

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.calls += 1
        self.slept += seconds
        self.now += seconds


def make(limits):
    t = FakeTime()
    return t, RateLimiter(limits, clock=t.clock, sleep=t.sleep)


def test_parse_header():
    assert parse_limit_header("100:120,20:1") == [(100, 120), (20, 1)]


async def test_allows_within_short_window():
    t, rl = make([(20, 1)])
    for _ in range(20):
        await rl.acquire()
    assert t.calls == 0


async def test_waits_when_window_full():
    t, rl = make([(2, 1)])
    for _ in range(3):
        await rl.acquire()
    assert t.slept >= 1.0


async def test_long_window_enforced():
    t, rl = make([(3, 120), (20, 1)])
    for _ in range(3):
        await rl.acquire()
    elapsed = t.now
    await rl.acquire()
    assert t.slept >= 120.0 - elapsed
    assert t.now >= 120.0


async def test_pause_delays_next_acquire():
    t, rl = make([(20, 1)])
    await rl.pause(5)
    await rl.acquire()
    assert t.slept >= 5


async def test_host_limiters_are_separate():
    hl = HostLimiters([(20, 1)])
    assert hl.for_host("kr") is not hl.for_host("asia")
    assert hl.for_host("kr") is hl.for_host("kr")
