"""챔피언 빌드(최종 아이템·룬·소환사 주문) 집계 (순수 함수)."""
from __future__ import annotations

from collections.abc import Callable

from lol_coach_mcp import champions

BUILD_MIN_GAMES = 3  # 조합별 판단 보류 기준
GROUP_LIMIT = 5  # 분류별 표시할 조합 수
OPGG_OPTION_LIMIT = 3  # 응답에 담는 OP.GG 추천 수
ITEM_SLOTS = ("item0", "item1", "item2", "item3", "item4", "item5")  # item6은 장신구
EXCLUDED_TAGS = ("Consumable", "Trinket")
BOOTS_TAG = "Boots"

# 소환사 주문 id → 이름 (Data Dragon summoner.json을 쓰지 않으므로 상수)
SPELL_NAMES = {
    1: "정화", 3: "탈진", 4: "점멸", 6: "유체화", 7: "회복", 11: "강타", 12: "텔레포트",
    13: "총명", 14: "점화", 21: "방어막", 32: "표식",
}


def group_status(games: int) -> str:
    return champions.STATUS_OK if games >= BUILD_MIN_GAMES else champions.STATUS_HOLD


def participant(match: dict | None, puuid: str) -> dict | None:
    ps = ((match or {}).get("info") or {}).get("participants") or []
    return next((p for p in ps if p.get("puuid") == puuid), None)


def split_items(p: dict, items: dict[int, dict]) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """최종 아이템(item0~5) → (핵심 아이템 id, 신발 id). 소모품·장신구 제외, id 오름차순."""
    core, boots = set(), set()
    for slot in ITEM_SLOTS:
        iid = p.get(slot) or 0
        if not iid:
            continue
        tags = (items.get(iid) or {}).get("tags", [])
        if any(t in tags for t in EXCLUDED_TAGS):
            continue
        (boots if BOOTS_TAG in tags else core).add(iid)
    return tuple(sorted(core)), tuple(sorted(boots))


def keystone_id(p: dict) -> int | None:
    try:
        return p["perks"]["styles"][0]["selections"][0]["perk"]
    except (KeyError, IndexError, TypeError):
        return None


def spell_key(p: dict) -> tuple[int, ...]:
    ids = [p.get("summoner1Id"), p.get("summoner2Id")]
    return tuple(sorted(i for i in ids if i))


def tally(rows: list[tuple[tuple, bool]]) -> list[dict]:
    """(조합 키, 승리 여부) → 조합별 판수·승수·승률. 판수 내림차순."""
    acc: dict[tuple, list[int]] = {}
    for key, win in rows:
        if not key:
            continue
        a = acc.setdefault(key, [0, 0])
        a[0] += 1
        a[1] += int(win)
    out = [{"ids": list(k), "games": g, "wins": w, "win_rate": round(w / g, 3),
            "status": group_status(g)} for k, (g, w) in acc.items()]
    return sorted(out, key=lambda r: (-r["games"], r["ids"]))


def options(value: object) -> list[dict]:
    """OP.GG 추천 항목은 실측상 단일 객체(최다 픽 조합)이고, 목록으로 오면 그대로 사용."""
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [o for o in value if isinstance(o, dict)]
    return []


def spell_names(ids: list[int]) -> list[str]:
    return [SPELL_NAMES.get(i, f"소환사 주문 {i}") for i in ids]


def opgg_rank(value: object, matches: Callable[[dict], bool]) -> int | None:
    """OP.GG 추천 옵션 중 matches를 만족하는 첫 옵션의 순번(1부터)."""
    for i, opt in enumerate(options(value), start=1):
        if matches(opt):
            return i
    return None


def core_matches(my_ids: list[int]) -> Callable[[dict], bool]:
    """내 최종 아이템이 OP.GG 추천 핵심 아이템을 모두 포함."""
    return lambda opt: bool(opt.get("ids")) and set(opt["ids"]) <= set(my_ids)


def spells_match(my_ids: list[int]) -> Callable[[dict], bool]:
    return lambda opt: set(opt.get("ids") or []) == set(my_ids)


def keystone_matches(name: str | None) -> Callable[[dict], bool]:
    def f(opt: dict) -> bool:
        names = opt.get("primary_rune_names") or []
        return bool(name) and bool(names) and names[0] == name
    return f


def opgg_summary(analysis_data: dict | None) -> dict:
    """응답에 담는 OP.GG 추천(핵심 아이템·신발·룬·소환사 주문 상위 몇 개)."""
    if not analysis_data or not any(
        options(analysis_data.get(k)) for k in ("core_items", "boots", "runes", "summoner_spells")
    ):
        return {"status": champions.STATUS_NO_OPGG}

    def wr(o):
        return round(o["win"] / o["play"], 3) if o.get("play") and o.get("win") is not None else None

    def top(key, fields):
        out = []
        for o in options(analysis_data.get(key))[:OPGG_OPTION_LIMIT]:
            row = {f: o.get(f) for f in fields}
            if key == "summoner_spells":  # OP.GG ids_names가 숫자로 오는 경우가 있어 상수 표로 대체
                row["ids_names"] = spell_names(o.get("ids") or [])
            out.append({**row, "win_rate": wr(o), "play": o.get("play")})
        return out

    return {
        "status": champions.STATUS_OK,
        "core_items": top("core_items", ("ids", "ids_names")),
        "boots": top("boots", ("ids", "ids_names")),
        "runes": top("runes", ("primary_page_name", "primary_rune_names",
                               "secondary_page_name", "secondary_rune_names")),
        "spells": top("summoner_spells", ("ids", "ids_names")),
    }
