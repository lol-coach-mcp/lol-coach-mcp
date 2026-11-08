"""코치 기억 조립 (순수 함수): 판정 기록 행 만들기, 오래된 기억 판정."""

from __future__ import annotations

from datetime import datetime, timedelta

from lol_coach_mcp.stats import MatchStats

STALE_DAYS = 30
CONTEXT_FINDINGS = 5
CONTEXT_JOURNAL = 3


def patch_major(patch: str | None) -> str | None:
    return patch.split(".")[0] if patch else None


def latest_major(stats: list[MatchStats]) -> str | None:
    """저장된 가장 최근 경기의 패치 메이저."""
    if not stats:
        return None
    return patch_major(max(stats, key=lambda s: s.game_creation).patch)


def finding_rows(
    form: dict, *, created_at: str, season_major: str | None
) -> list[dict]:
    """analyze_form 결과(dict)의 약점/강점(best_self 동반 포함)을 저장용 행으로 바꿈."""
    out: list[dict] = []
    pairs = (
        ("약점", form.get("weaknesses")),
        ("강점", form.get("strengths")),
        ("약점", form.get("best_self_weaknesses")),
        ("강점", form.get("best_self_strengths")),
    )
    for kind, rows in pairs:
        for row in rows or []:
            out.append({
                "created_at": created_at,
                "kind": kind,
                "metric": row["metric"],
                "my_value": row.get("my_value"),
                "ref_value": row.get("ref_value"),
                "ref_source": row.get("ref_source"),
                "games": row.get("my_sample"),
                "period_desc": form.get("period_desc"),
                "season_major": season_major,
                "position": form.get("position"),
            })
    return out


def is_stale(finding: dict, *, now: datetime, current_major: str | None) -> bool:
    """30일 초과 또는 저장 당시 패치 메이저가 현재 최신 경기와 다르면 오래된 기억."""
    created = datetime.fromisoformat(finding["created_at"])
    if created.tzinfo is None:
        created = created.replace(tzinfo=now.tzinfo)
    if now - created > timedelta(days=STALE_DAYS):
        return True
    major = finding.get("season_major")
    return bool(major and current_major and major != current_major)
