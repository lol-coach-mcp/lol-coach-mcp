"""기준선 표본 수집기.

시드(티어별 리그 항목의 무작위 PUUID)마다 최근 솔로랭크 경기를 받아, 경기 참가자 10명 각자의
지표 값을 시드 티어 라벨로 저장한다(같은 매칭 구간이라는 근사). 식별자는 저장하지 않으며,
재개 시 중복 제외는 솔트 해시(store.baseline_seen)로만 한다.
"""

from __future__ import annotations

import random
from collections.abc import AsyncIterator, Callable
from datetime import datetime, timezone
from typing import Any

import httpx

from lol_coach_mcp.analysis import metric_series
from lol_coach_mcp.riot.errors import RiotAuthError, RiotError
from lol_coach_mcp.stats import extract_match_stats
from lol_coach_mcp.store import Store

DIVISION_TIERS = ("IRON", "BRONZE", "SILVER", "GOLD", "PLATINUM", "EMERALD", "DIAMOND")
APEX_TIERS = ("MASTER", "GRANDMASTER", "CHALLENGER")
ALL_TIERS = DIVISION_TIERS + APEX_TIERS
DIVISIONS = ("I", "II", "III", "IV")
SEED_PAGE_MAX = 50  # division당 무작위로 훑는 페이지 상한(빈 페이지를 만나면 그 이상은 건너뜀)
MATCHES_PER_SEED = 3
QUEUE_NAMES = {420: "RANKED_SOLO_5x5"}

STATUS_DONE = "done"
STATUS_EXHAUSTED = "exhausted"
STATUS_INTERRUPTED = "interrupted"
STATUS_ABORTED = "aborted"
MAX_CONSECUTIVE_FAILURES = 20  # 연속 이만큼 실패하면 해당 티어 수집을 중단

Progress = Callable[[str], None]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def samples_from_match(match: dict, queue: int) -> list[dict] | None:
    """참가자별 지표 표본. 큐 불일치·다시하기 경기는 None, 포지션이 빈 참가자는 제외."""
    info = match["info"]
    if info.get("queueId") != queue:
        return None
    participants = info["participants"]
    if any(p.get("gameEndedInEarlySurrender") for p in participants):
        return None
    out = []
    for p in participants:
        if not p.get("teamPosition"):
            continue
        stats = extract_match_stats(match, p["puuid"])
        values = {m: v[0] for m, v in metric_series([stats]).items()}
        out.append({"position": stats.position, "champion": stats.champion,
                    "patch": stats.patch, **values})
    return out


async def _seed_stream(
    client: Any, tier: str, queue_name: str, rng: random.Random
) -> AsyncIterator[str]:
    """티어의 시드 PUUID를 무작위 순서로 내보냄(division·page 라운드로빈)."""
    if tier in APEX_TIERS:
        data = await client.get_apex_league(tier, queue_name)
        puuids = [e["puuid"] for e in data.get("entries", []) if e.get("puuid")]
        rng.shuffle(puuids)
        for puuid in puuids:
            yield puuid
        return
    divisions = list(DIVISIONS)
    rng.shuffle(divisions)
    pages = {d: rng.sample(range(1, SEED_PAGE_MAX + 1), SEED_PAGE_MAX) for d in divisions}
    while any(pages.values()):
        for d in divisions:
            if not pages[d]:
                continue
            page = pages[d].pop()
            entries = await client.get_league_entries_page(queue_name, tier, d, page)
            if not entries:
                pages[d] = [q for q in pages[d] if q < page]
                continue
            puuids = [e["puuid"] for e in entries if e.get("puuid")]
            rng.shuffle(puuids)
            for puuid in puuids:
                yield puuid


async def _collect_tier(
    client: Any, store: Store, tier: str, *, target: int, queue: int, rng_seed: int | None,
    progress: Progress,
) -> None:
    rng = random.Random(rng_seed)
    queue_name = QUEUE_NAMES[queue]
    state = store.get_baseline_job(tier) or {}
    state.update({"target": target, "queue": queue, "rng_seed": rng_seed,
                  "status": STATUS_INTERRUPTED})
    matches = store.baseline_match_count(tier)
    seeds = state.get("seeds", 0)

    def save() -> None:
        state.update({"matches": matches, "seeds": seeds})
        store.set_baseline_job(tier, state, _now())

    save()
    status = STATUS_INTERRUPTED
    failures = 0  # 연속 RiotError/HTTP 실패 수(성공하면 0으로 돌아감)

    def too_many_failures() -> bool:
        if failures < MAX_CONSECUTIVE_FAILURES:
            return False
        progress(f"[{tier}] 연속 {MAX_CONSECUTIVE_FAILURES}회 호출 실패로 티어 수집을 중단함. "
                 "진행 상태는 저장됨(--resume으로 재시도 가능)")
        return True
    try:
        if matches >= target:
            status = STATUS_DONE
            return
        stream = _seed_stream(client, tier, queue_name, rng)
        async for puuid in stream:
            if store.baseline_seen("seed", puuid):
                continue
            try:
                ids = await client.get_match_ids(puuid, queue=queue, count=MATCHES_PER_SEED)
            except RiotAuthError:
                raise
            except (RiotError, httpx.HTTPError):
                failures += 1
                if too_many_failures():
                    status = STATUS_ABORTED
                    return
                continue
            complete = True
            for match_id in ids:
                if matches >= target:
                    complete = False
                    break
                if store.baseline_seen("match", match_id) or store.baseline_seen("skip", match_id):
                    continue
                try:
                    match = await client.get_match(match_id)
                except RiotAuthError:
                    raise
                except (RiotError, httpx.HTTPError):
                    complete = False  # 시드를 완료 처리하지 않아 재개 시 다시 시도
                    failures += 1
                    if too_many_failures():
                        status = STATUS_ABORTED
                        return
                    continue
                failures = 0
                samples = samples_from_match(match, queue)
                if not samples:
                    store.baseline_mark_seen("skip", match_id, tier)
                    continue
                if store.add_baseline_match(tier, match_id, samples):
                    matches += 1
                    progress(f"[{tier}] 경기 {matches}/{target} 수집 (표본 {len(samples)}명)")
            if complete:
                store.baseline_mark_seen("seed", puuid, tier)
                seeds += 1
            if matches >= target:
                status = STATUS_DONE
                return
        status = STATUS_DONE if matches >= target else STATUS_EXHAUSTED
        if status == STATUS_EXHAUSTED:
            progress(f"[{tier}] 시드 소진: {matches}/{target}경기에서 중단")
    finally:
        state["status"] = status
        save()


async def collect(
    client: Any,
    store: Store,
    *,
    tiers: list[str],
    matches_per_tier: int,
    queue: int = 420,
    resume: bool = False,
    rng_seed: int | None = None,
    progress: Progress = lambda msg: None,
) -> dict[str, int]:
    """티어별 수집. RiotAuthError는 진행 상태를 저장한 뒤 그대로 전파함.

    resume이 아니면 대상 티어의 기존 진행·표본을 지우고 처음부터 수집함.
    """
    unknown = [t for t in tiers if t not in ALL_TIERS]
    if unknown:
        raise ValueError(f"알 수 없는 티어: {', '.join(unknown)}")
    if queue not in QUEUE_NAMES:
        raise ValueError(f"지원하지 않는 큐: {queue}")
    result: dict[str, int] = {}
    for tier in tiers:
        if not resume:
            old_m, old_s = store.baseline_match_count(tier), store.baseline_sample_count(tier)
            if old_m or old_s:
                progress(f"[{tier}] --resume 없이 시작해 기존 진행({old_m}경기, 표본 {old_s}개)을 폐기함")
            store.reset_baseline_tier(tier)
        progress(f"[{tier}] 수집 시작(목표 {matches_per_tier}경기)")
        await _collect_tier(client, store, tier, target=matches_per_tier, queue=queue,
                            rng_seed=rng_seed, progress=progress)
        result[tier] = store.baseline_match_count(tier)
    return result
