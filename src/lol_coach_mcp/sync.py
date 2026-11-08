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
    # 1) 새 경기 ID 수집 (최신순, 이미 저장된 ID를 만나면 중단)
    new_ids: list[str] = []
    reason = "no_more"
    start = 0
    done_paging = False
    while not done_paging:
        page = await client.get_match_ids(puuid, queue=queue, start=start, count=PAGE_SIZE)
        for mid in page:
            if store.has_match(mid):
                reason = "caught_up"
                done_paging = True
                break
            if not full and len(new_ids) >= limit:
                reason = "limit_reached"
                done_paging = True
                break
            new_ids.append(mid)
        else:
            if len(page) < PAGE_SIZE:
                done_paging = True
            else:
                start += PAGE_SIZE

    # 2) 경기별 저장
    total = len(new_ids)
    saved = 0
    for i, mid in enumerate(new_ids, start=1):
        try:
            match = await client.get_match(mid)
            stats = extract_match_stats(match, puuid)
            store.save_match(mid, match, stats)
            saved += 1
            if with_timeline:
                try:
                    store.save_timeline(mid, await client.get_timeline(mid))
                except RiotNotFound:
                    pass
        except RiotNotFound:
            pass  # Riot이 정리한 경기는 건너뜀
        if progress is not None:
            await progress(i, total, f"경기 {i}/{total} 저장")

    store.set_meta("last_sync_at", datetime.now(timezone.utc).isoformat())
    return SyncResult(
        new_matches=saved,
        total_stored=len(store.list_my_stats(queue=queue, include_remakes=True)),
        queue=queue,
        stopped_reason=reason,
    )
