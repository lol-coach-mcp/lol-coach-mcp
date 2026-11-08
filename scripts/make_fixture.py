"""실제 Riot 응답을 익명화하여 테스트 픽스처로 저장한다.

사용 예:
  uv run python scripts/make_fixture.py --riot-id "이름#태그" --kind normal \
      --out tests/fixtures/match_normal.json --with-timeline
  uv run python scripts/make_fixture.py --riot-id "이름#태그" --kind league \
      --out tests/fixtures/league_ranked.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any

from lol_coach_mcp.config import load_settings
from lol_coach_mcp.riot.client import RiotClient

PUUID_ME = "puuid-me"
PUUID_LIKE = re.compile(r"^[A-Za-z0-9_-]{70,}$")
BLANK_KEYS = {"summonerId", "summonerName"}


def build_mapping(participants: list[str], me: str) -> dict[str, str]:
    mapping = {me: PUUID_ME}
    n = 0
    for p in participants:
        if p not in mapping:
            n += 1
            mapping[p] = f"puuid-p{n:02d}"
    return mapping


def _names(mapping: dict[str, str]) -> dict[str, str]:
    return {
        real: ("테스트유저" if anon == PUUID_ME else f"플레이어{anon[-2:]}")
        for real, anon in mapping.items()
    }


def anonymize(node: Any, mapping: dict[str, str], *, _parent_puuid: str | None = None) -> Any:
    """키 기반 익명화. 참가자 dict는 puuid를 기준으로 Riot ID를 치환한다."""
    names = _names(mapping)
    if isinstance(node, dict):
        real = node.get("puuid") if isinstance(node.get("puuid"), str) else None
        out: dict[str, Any] = {}
        for k, v in node.items():
            if k == "puuid" and isinstance(v, str):
                out[k] = mapping[v]
            elif k in BLANK_KEYS:
                out[k] = ""
            elif k == "riotIdGameName":
                out[k] = names.get(real, "") if real else ""
            elif k == "riotIdTagline":
                out[k] = "KR1"
            elif k == "participants" and isinstance(v, list) and all(isinstance(x, str) for x in v):
                out[k] = [mapping[x] for x in v]
            else:
                out[k] = anonymize(v, mapping)
        return out
    if isinstance(node, list):
        return [anonymize(x, mapping) for x in node]
    if isinstance(node, str) and node in mapping:
        return mapping[node]
    if isinstance(node, str) and PUUID_LIKE.match(node):
        raise ValueError("매핑에 없는 PUUID 형태 문자열 발견")
    return node


DAY_MS = 86_400_000


def scrub_ids(node: Any, fake_id: int) -> Any:
    """실제 매치 식별자를 가짜 값으로 바꾸고 절대 시각(epoch ms)을 일정하게 앞당긴다."""
    offset = -(400 + fake_id) * DAY_MS
    fake_game = f"teambuilder-match-{fake_id}"
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for k, v in node.items():
            if k == "matchId" and isinstance(v, str):
                out[k] = f"KR_{fake_id:010d}"
            elif k == "gameId" and isinstance(v, int):
                out[k] = fake_id
            elif k == "gameName" and isinstance(v, str):
                out[k] = fake_game
            elif isinstance(v, int) and not isinstance(v, bool) and v > 10**11:
                out[k] = v + offset
            else:
                out[k] = scrub_ids(v, fake_id)
        return out
    if isinstance(node, list):
        return [scrub_ids(x, fake_id) for x in node]
    return node


def find_me(match: dict, puuid: str) -> dict | None:
    for p in match["info"]["participants"]:
        if p["puuid"] == puuid:
            return p
    return None


def matches_kind(match: dict, puuid: str, kind: str) -> bool:
    me = find_me(match, puuid)
    if me is None:
        return False
    if kind == "remake":
        return bool(me.get("gameEndedInEarlySurrender"))
    if kind == "support":
        return me.get("teamPosition") == "UTILITY" and not me.get("gameEndedInEarlySurrender")
    if kind == "normal":
        return match["info"]["gameDuration"] >= 900 and not me.get("gameEndedInEarlySurrender")
    return False


def dump(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=2)
    if len(text.encode("utf-8")) > 1_000_000:
        text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    path.write_text(text + "\n", encoding="utf-8")


async def run(args: argparse.Namespace) -> int:
    name, _, tag = args.riot_id.partition("#")
    out = Path(args.out)
    async with RiotClient(load_settings()) as c:
        account = await c.get_account_by_riot_id(name, tag)
        puuid = account["puuid"]
        if args.kind == "league":
            entries = await c.get_league_entries(puuid)
            mapping = {puuid: PUUID_ME}
            dump(out, [anonymize(e, mapping) for e in entries])
            print(f"league entries: {len(entries)} -> {out}")
            return 0
        ids = await c.get_match_ids(puuid, queue=420, start=0, count=100)
        for mid in ids:
            match = await c.get_match(mid)
            if not matches_kind(match, puuid, args.kind):
                continue
            mapping = build_mapping(match["metadata"]["participants"], puuid)
            dump(out, scrub_ids(anonymize(match, mapping), args.fake_id))
            print(f"{args.kind}: match found (duration={match['info']['gameDuration']}s) -> {out}")
            if args.with_timeline:
                tl = await c.get_timeline(mid)
                tl_out = out.with_name(out.name.replace("match_", "timeline_"))
                dump(tl_out, scrub_ids(anonymize(tl, mapping), args.fake_id))
                print(f"timeline -> {tl_out}")
            return 0
    print(f"조건에 맞는 경기를 찾지 못함: {args.kind}", file=sys.stderr)
    return 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--riot-id", required=True)
    ap.add_argument("--kind", required=True, choices=["normal", "remake", "support", "league"])
    ap.add_argument("--out", required=True)
    ap.add_argument("--fake-id", type=int, default=1, help="가짜 매치 번호(픽스처마다 다르게)")
    ap.add_argument("--with-timeline", action="store_true")
    return asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
