"""lol-coach-baseline CLI: collect / build / export. 진행 상황·오류는 stderr, 종료 코드로 결과 전달."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from lol_coach_mcp.baseline.builder import build_baselines, export_baselines
from lol_coach_mcp.baseline.collector import ALL_TIERS, DIVISION_TIERS, QUEUE_NAMES, collect
from lol_coach_mcp.config import ConfigError, Settings, load_settings
from lol_coach_mcp.riot.client import RiotClient
from lol_coach_mcp.riot.errors import RiotAuthError, RiotError
from lol_coach_mcp.store import Store

DB_NAME = "lol-coach.sqlite3"
DEFAULT_MATCHES_PER_TIER = 60
DEFAULT_EXPORT_PATH = "src/lol_coach_mcp/data/baselines.json"
EXIT_OK, EXIT_ERROR, EXIT_AUTH = 0, 1, 2


def _err(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="lol-coach-baseline", description="티어별 기준선 수집·빌드·내보내기")
    sub = p.add_subparsers(dest="command", required=True)
    c = sub.add_parser("collect", help="리그 시드에서 경기 표본 수집")
    c.add_argument("--tiers", default=",".join(DIVISION_TIERS),
                   help="쉼표로 구분한 티어(IRON~CHALLENGER). 기본: IRON~DIAMOND")
    c.add_argument("--matches-per-tier", type=int, default=DEFAULT_MATCHES_PER_TIER)
    c.add_argument("--queue", type=int, default=420, choices=sorted(QUEUE_NAMES))
    c.add_argument("--resume", action="store_true", help="저장된 진행 상태에서 이어서 수집")
    c.add_argument("--seed", type=int, default=None, help="시드 선택용 난수 시드(재현용)")
    sub.add_parser("build", help="수집 표본을 기준선(사용자 수집)으로 집계")
    e = sub.add_parser("export", help="기준선을 배포용 JSON으로 내보내기")
    e.add_argument("--out", default=DEFAULT_EXPORT_PATH)
    return p


async def _run_collect(args: argparse.Namespace, settings: Settings, store: Store,
                       client_factory: Callable[[Settings], Any]) -> int:
    tiers = [t.strip().upper() for t in args.tiers.split(",") if t.strip()]
    unknown = [t for t in tiers if t not in ALL_TIERS]
    if unknown or not tiers:
        _err(f"알 수 없는 티어: {', '.join(unknown) or '(없음)'}. 사용 가능: {', '.join(ALL_TIERS)}")
        return EXIT_ERROR
    client = client_factory(settings)
    try:
        await collect(client, store, tiers=tiers, matches_per_tier=args.matches_per_tier,
                      queue=args.queue, resume=args.resume, rng_seed=args.seed, progress=_err)
    except RiotAuthError as e:
        _err(f"{e} 수집 진행 상태는 저장됨. 키를 갱신한 뒤 --resume으로 이어서 수집할 수 있음.")
        return EXIT_AUTH
    except RiotError as e:
        _err(f"Riot API 오류로 중단됨: {e} 진행 상태는 저장됨. --resume으로 이어서 수집할 수 있음.")
        return EXIT_ERROR
    finally:
        aclose = getattr(client, "aclose", None)
        if aclose is not None:
            await aclose()
    _err("수집 완료")
    return EXIT_OK


def main(
    argv: Sequence[str] | None = None,
    *,
    settings: Settings | None = None,
    client_factory: Callable[[Settings], Any] = RiotClient,
) -> int:
    args = _parser().parse_args(argv)
    try:
        settings = settings or load_settings()
    except ConfigError as e:
        _err(str(e))
        return EXIT_ERROR
    store = Store(settings.data_dir / DB_NAME)
    try:
        if args.command == "collect":
            return asyncio.run(_run_collect(args, settings, store, client_factory))
        if args.command == "build":
            res = build_baselines(store)
            _err(f"기준선 {res['rows']}행 생성(표본 부족으로 제외한 그룹 {res['skipped_groups']}개)")
            return EXIT_OK
        try:
            count = export_baselines(store, Path(args.out))
        except ValueError as e:
            _err(str(e))
            return EXIT_ERROR
        _err(f"{count}행을 {args.out}에 내보냄(source: 배포)")
        return EXIT_OK
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
