"""매치 증분 동기화."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from lol_coach_mcp.riot.client import RiotClient
from lol_coach_mcp.riot.errors import RiotNotFound
from lol_coach_mcp.stats import extract_match_stats
from lol_coach_mcp.store import Store

PAGE_SIZE = 100

ProgressCallback = Callable[[int, int, str], Awaitable[None]]


@dataclass
class SyncResult:
    """동기화 결과.

    new_matches: 이번에 새로 저장한 경기 수.
    total_stored: 동기화 후 해당 queue로 저장된 전체 경기 수(리메이크 포함).
        queue가 None이면 큐 구분 없이 전부.
    stopped_reason: "caught_up" | "limit_reached" | "no_more"
    """

    new_matches: int
    total_stored: int
    queue: int | None
    stopped_reason: str


async def sync_matches(
    client: RiotClient,
    store: Store,
    puuid: str,
    *,
    queue: int | None = 420,
    limit: int = 100,
    full: bool = False,
    with_timeline: bool = True,
    progress: ProgressCallback | None = None,
) -> SyncResult:
    # 1) 경기 ID 수집 (최신순). 증분이면 최신 limit개 창을 훑어 저장 안 된 것만 취함.
    new_ids: list[str] = []
    window = 0
    start = 0
    while True:
        page = await client.get_match_ids(puuid, queue=queue, start=start, count=PAGE_SIZE)
        for mid in page:
            if not full and window >= limit:
                break
            window += 1
            if not store.has_match(mid):
                new_ids.append(mid)
        if len(page) < PAGE_SIZE or (not full and window >= limit):
            break
        start += PAGE_SIZE
    if full:
        reason = "no_more"
    elif not new_ids:
        reason = "caught_up"
    elif window >= limit:
        reason = "limit_reached"
    else:
        reason = "no_more"

    # 2) 오래된 것부터 저장: 도중에 실패해도 다음 동기화가 빈 구간을 이어받음
    total = len(new_ids)
    saved = 0
    for i, mid in enumerate(reversed(new_ids), start=1):
        try:
            match = await client.get_match(mid)
            stats = extract_match_stats(match, puuid)
            timeline = None
            if with_timeline:
                try:
                    timeline = await client.get_timeline(mid)
                except RiotNotFound:
                    pass  # 타임라인 없이 경기만 저장
            store.save_match(mid, match, stats)
            if timeline is not None:
                store.save_timeline(mid, timeline)
            saved += 1
        except RiotNotFound:
            pass  # Riot이 정리한 경기는 건너뜀
        if progress is not None:
            await progress(i, total, f"경기 {i}/{total} 저장")

    store.set_meta("data_owner_puuid", puuid)
    store.set_meta("last_sync_at", datetime.now(timezone.utc).isoformat())
    return SyncResult(
        new_matches=saved,
        total_stored=len(store.list_my_stats(queue=queue, include_remakes=True)),
        queue=queue,
        stopped_reason=reason,
    )
