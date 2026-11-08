"""baseline_samples -> baselines 빌드, 배포용 JSON 내보내기."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from lol_coach_mcp.analysis import METRICS, summary
from lol_coach_mcp.store import Store

MIN_SAMPLES = 30  # 티어×포지션 표본이 이보다 적으면 기준선 행을 만들지 않음
SOURCE_USER = "사용자 수집"
SOURCE_DIST = "배포"
EXPORT_NOTE = "티어 라벨은 시드 기준 근사"
EXPORT_NOTES = [
    "참가자 10명을 표본으로 사용(같은 경기 내 상관 있음)",
    "승률 기준선은 정의상 약 0.5",
]
EXPORT_DECIMALS = 4


def _mode(values: list[str]) -> str | None:
    if not values:
        return None
    counts = Counter(values)
    return max(counts, key=lambda v: (counts[v], v))


def build_baselines(store: Store, *, now: str | None = None) -> dict[str, int]:
    """표본을 (tier, position, champion=없음, metric)별 평균·표준편차로 집계해 사용자 수집 행으로 교체."""
    collected_at = now or datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in store.list_baseline_samples():
        groups[(row["tier"], row["position"])].append(row)

    out: list[dict] = []
    skipped = 0
    for (tier, position), rows in sorted(groups.items()):
        if len(rows) < MIN_SAMPLES:
            skipped += 1
            continue
        patch = _mode([r["patch"] for r in rows if r["patch"]])
        for metric in METRICS:
            s = summary([r[metric] for r in rows])
            out.append({"tier": tier, "position": position, "champion": None, "metric": metric,
                        "mean": s["mean"], "std": s["std"], "n": s["n"], "patch": patch,
                        "collected_at": collected_at, "source": SOURCE_USER})
    store.replace_baselines_by_source(SOURCE_USER, out)
    return {"rows": len(out), "skipped_groups": skipped}


def export_baselines(store: Store, out_path: Path) -> int:
    """사용자 수집 기준선을 source "배포"로 JSON에 기록. 지표 통계만 담고 식별 정보는 없음."""
    rows = store.list_baselines(SOURCE_USER)
    if not rows:
        raise ValueError("내보낼 기준선이 없음. collect 후 build를 먼저 실행해야 함.")
    exported = [{
        "tier": r["tier"], "position": r["position"], "champion": r["champion"] or None,
        "metric": r["metric"], "mean": round(r["mean"], EXPORT_DECIMALS),
        "std": None if r["std"] is None else round(r["std"], EXPORT_DECIMALS),
        "n": r["n"], "patch": r["patch"], "collected_at": r["collected_at"],
        "source": SOURCE_DIST,
    } for r in rows]
    data = {
        "metadata": {
            "source": SOURCE_DIST,
            "collected_at": max(r["collected_at"] for r in rows if r["collected_at"]),
            "patch": _mode([r["patch"] for r in rows if r["patch"]]),
            "note": EXPORT_NOTE,
            "notes": EXPORT_NOTES,
        },
        "rows": exported,
    }
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return len(exported)
