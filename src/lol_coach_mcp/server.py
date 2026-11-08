"""MCP 서버 정의와 계정 관련 tool."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Literal

import httpx
from mcp.server.mcpserver import Context, MCPServer
from mcp_types import CallToolResult

from lol_coach_mcp.config import Settings, load_settings
from lol_coach_mcp import (
    analysis, builds, champions, form, habits, matchups, memory, periods, review, splits,
)
from lol_coach_mcp.ddragon import DataDragon
from lol_coach_mcp.opgg import OpggClient, opgg_champion_key, opgg_position
from lol_coach_mcp.models import (
    BuildReportOut,
    ChampionReportOut,
    CoachContextOut,
    DeathOut,
    DetailPlayerOut,
    DetailTeamOut,
    DuoPartnerOut,
    FormOut,
    HabitsOut,
    LaneOut,
    MasteryOut,
    MatchDetailOut,
    MatchHistoryOut,
    MatchRowOut,
    MatchupReportOut,
    MomentOut,
    ObjectivesOut,
    PlayerProfileOut,
    ProfileOut,
    RankOut,
    ReviewOut,
    ReviewSummaryOut,
    SplitGroupOut,
    SplitsOut,
    SyncOut,
    VisionOut,
)
from lol_coach_mcp.results import tool_error, tool_result
from lol_coach_mcp.riot.client import RiotClient
from lol_coach_mcp.riot.errors import RiotError, RiotNotFound
from lol_coach_mcp.stats import MatchStats, extract_match_stats, kda, match_tags, recent_form
from lol_coach_mcp.store import Profile, Store
from lol_coach_mcp.sync import sync_matches as run_sync

NO_PROFILE_MESSAGE = (
    '등록된 계정이 없음. my_profile(action="set", riot_id="이름#태그")로 먼저 등록해야 함.'
)
NETWORK_ERROR_MESSAGE = "Riot 서버에 연결하지 못함. 네트워크 상태를 확인한 뒤 다시 시도해야 함."

NO_PROFILE_GUIDE_TEXT = (
    '등록된 계정이 없음. my_profile(action="set", riot_id="이름#태그")로 등록할 수 있음.'
)

RIOT_ID_FORMAT_ERROR = (
    'Riot ID 형식이 올바르지 않음. "이름#태그" 형태로 "#" 하나와 이름, 태그가 모두 필요함.'
)

KST = timezone(timedelta(hours=9))
QUEUE_NAMES = {"RANKED_SOLO_5x5": "솔로랭크", "RANKED_FLEX_SR": "자유랭크"}
QUEUE_LABELS = {420: "솔로랭크", 440: "자유랭크"}
PROFILE_QUEUE = 420
MAX_HISTORY_COUNT = 50
SYNC_FAILED_NOTE = "동기화 실패, 저장된 데이터 기준"
REVIEW_QUEUE = 420
REVIEW_SYNC_LIMIT = 5
OVERVIEW_MOMENT_LIMIT = 3
OVERVIEW_LANE_MINUTES = (15,)
NO_TIMELINE_NOTE = "타임라인 데이터 없음"
NO_TIMELINE_ME_NOTE = "타임라인에 내 계정이 없음"
NO_REVIEW_MATCH_MESSAGE = (
    "복기할 솔로랭크 경기가 없음. 경기를 진행한 뒤 sync_matches로 동기화하거나 match_id를 지정해야 함."
)

FORM_DEFAULT_COUNT = 20
ANALYSIS_MAX_COUNT = 100
SPLITS_DEFAULT_COUNT = 30
HABITS_DEFAULT_COUNT = 30
HABITS_BACKFILL_LIMIT = 10
NOTE_NO_RANK_INFO = "현재 티어를 확인하지 못함"
SOLO_QUEUE_TYPE = "RANKED_SOLO_5x5"

RIOT_DISCLAIMER = (
    "lol-coach-mcp is not endorsed by Riot Games and does not reflect the views or opinions "
    "of Riot Games or anyone officially involved in producing or managing Riot Games "
    "properties. Riot Games and all associated properties are trademarks or registered "
    "trademarks of Riot Games, Inc."
)
INSTRUCTIONS = f"롤 개인 코칭 서버. 대화 시작 시 get_coach_context 호출\n\n{RIOT_DISCLAIMER}"
NOT_FOUND_MESSAGE = "해당 Riot ID를 찾을 수 없음. 이름과 태그를 확인해야 함."


@dataclass
class AppState:
    settings: Settings
    riot: Any  # RiotClient 또는 테스트용 가짜 객체(덕 타이핑)
    store: Store
    opgg: Any = None  # OpggClient 또는 가짜 객체. None이면 OP.GG 데이터 없음으로 처리
    ddragon: Any = None  # DataDragon 또는 가짜 객체. None이면 Data Dragon 데이터 없음
    http: Any = None  # 기본 팩토리가 만든 httpx.AsyncClient(종료 시 닫음)
    now: Callable[[], datetime] | None = None  # 시계 주입(테스트용). None이면 현재 시각


def _default_state() -> AppState:
    settings = load_settings()
    store = Store(settings.data_dir / "lol-coach.sqlite3")
    http = httpx.AsyncClient(timeout=10.0)
    return AppState(
        settings=settings,
        riot=RiotClient(settings),
        store=store,
        opgg=OpggClient(store),
        ddragon=DataDragon(store, http, user_agent=settings.user_agent),
        http=http,
    )


def _profile_out(p: Profile | None) -> ProfileOut:
    if p is None:
        return ProfileOut(registered=False)
    return ProfileOut(
        registered=True,
        riot_id=f"{p.game_name}#{p.tag_line}",
        game_name=p.game_name,
        tag_line=p.tag_line,
        main_position=p.main_position,
        goal_tier=p.goal_tier,
        tone=p.tone,
        detail_level=p.detail_level,
    )


def _context_text(p: Profile, out: CoachContextOut) -> str:
    sync = f"마지막 동기화: {kst_text(out.last_sync_at)}." if out.last_sync_at else "아직 동기화한 적 없음."
    parts = [f"{_profile_text(p)} 저장된 경기 {out.stored_matches}개. {sync}"]
    if out.goals:
        goals = ", ".join(
            f"#{g['id']} {g['metric']} {'이상' if g['direction'] == 'up' else '이하'} "
            f"{g['target']:g} ({g['games_to_track']}판)"
            for g in out.goals
        )
        parts.append(f"진행 중 집중 과제: {goals}.")
    else:
        parts.append("집중 과제 없음.")
    if out.findings:
        items = ", ".join(
            f"{f['kind']} {f['metric']}({f['created_at'][:10]}"
            + (", 오래된 기록" if f["stale"] else "") + ")"
            for f in out.findings
        )
        parts.append(f"최근 판정 기록: {items}.")
    else:
        parts.append("저장된 판정 기록 없음.")
    if out.journal:
        notes = " / ".join(f"{j['created_at'][:10]} {j['text']}" for j in out.journal)
        parts.append(f"최근 일지: {notes}.")
    return " ".join(parts)


def _profile_text(p: Profile) -> str:
    return f"계정 {p.game_name}#{p.tag_line} 등록됨. 말투: {p.tone}."


def queue_label(queue_id: int | None) -> str:
    if queue_id is None:
        return "전체 큐"
    return QUEUE_LABELS.get(queue_id, f"큐 {queue_id}")


def kst_text(iso: str) -> str:
    """ISO 시각(타임존 없으면 UTC로 간주)을 `YYYY-MM-DD HH:MM KST` 표시 문자열로 변환함."""
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(KST).strftime("%Y-%m-%d %H:%M KST")


def _played_at(game_creation_ms: int) -> str:
    return datetime.fromtimestamp(game_creation_ms / 1000, tz=KST).isoformat(timespec="seconds")


def _result(s: MatchStats) -> str:
    if s.is_remake:
        return "다시하기"
    return "승" if s.win else "패"


def _win_rate(wins: int, losses: int) -> float:
    total = wins + losses
    return round(wins / total * 100, 1) if total else 0.0


def _damage_rank(participants: list[dict], me: dict) -> int:
    # 딜량 내림차순, 동률은 참가자 순서 우선(안정 정렬)
    ordered = sorted(participants, key=lambda p: -p.get("totalDamageDealtToChampions", 0))
    return next(i for i, p in enumerate(ordered, start=1) if p["puuid"] == me["puuid"])


def _now_kst(st: AppState) -> datetime:
    return (st.now() if st.now else datetime.now(timezone.utc)).astimezone(KST)


def _state(ctx: Context) -> AppState:
    return ctx.request_context.lifespan_context


async def _load_match(
    st: AppState, p: Profile, match_id: str
) -> tuple[dict, MatchStats] | CallToolResult:
    """저장소에서 경기를 읽고, 없으면 Riot에서 받아 저장함. 실패 시 tool 오류 결과를 반환함."""
    match = st.store.get_match(match_id)
    fetched = False
    if match is None:
        try:
            match = await st.riot.get_match(match_id)
        except RiotError as e:
            return tool_error(str(e))
        except httpx.HTTPError:
            return tool_error(NETWORK_ERROR_MESSAGE)
        fetched = True
    try:
        stats = extract_match_stats(match, p.puuid)
    except ValueError:
        return tool_error("이 경기에 내 계정이 참가하지 않았음. match_id를 확인해야 함.")
    if fetched:
        st.store.save_match(match_id, match, stats)
        st.store.set_meta("data_owner_puuid", p.puuid)
    return match, stats


def _review_text(out: ReviewOut) -> str:
    head = f"{out.champion} {out.position} {out.result} ({out.duration}, {queue_label(out.queue_id)}) [{out.focus}]."
    if out.status == "다시하기":
        return f"{head} 다시하기 경기라 복기 대상이 아님."
    lines = [head]
    if out.status == "판단 보류":
        lines.append("판단 보류: " + ", ".join(out.notes) + ".")
    if out.summary:
        sm = out.summary
        lines.append(f"KDA {sm.kills}/{sm.deaths}/{sm.assists} ({sm.kda}).")
        if sm.death_counts_by_phase is not None:
            phases = ", ".join(f"{k} {v}" for k, v in sm.death_counts_by_phase.items())
            lines.append(f"단계별 데스: {phases}.")
        if sm.objective_participation_rate is not None:
            lines.append(
                f"오브젝트 참여율 {round(sm.objective_participation_rate * 100)}% "
                f"(우리 팀 에픽 {sm.objective_epic_count}개)."
            )
    if out.lane:
        if out.lane.status != "ok":
            lines.append(f"라인전: {out.lane.status}.")
        for pt in out.lane.points:
            lines.append(
                f"라인전 vs {out.lane.opponent_champion} {pt.minute}분: CS {pt.cs_diff:+d}, "
                f"골드 {pt.gold_diff:+d}, 경험치 {pt.xp_diff:+d}."
            )
    if out.deaths is not None:
        lines.append(f"데스 {len(out.deaths)}회.")
        for d in out.deaths:
            lines.append(
                f"{d.time} {d.phase} {d.zone} 킬러 {d.killer_champion or '알 수 없음'} "
                f"(어시스트 {d.assists})" + (f" [{', '.join(d.tags)}]" if d.tags else "")
            )
    if out.objectives:
        ob = out.objectives
        lines.append(
            f"우리 팀 에픽 {ob.my_team_epic_count}개 중 참여 {ob.my_participation} "
            f"({round(ob.participation_rate * 100)}%)."
        )
        for e in ob.events:
            sub = f"/{e.sub_type}" if e.sub_type else ""
            lines.append(
                f"{e.time} {e.type}{sub} {e.team} 참여 {'O' if e.participated else 'X'}"
                + (" 사망 상태" if e.dead_at_time else "")
            )
    if out.vision:
        v = out.vision
        placed = ", ".join(f"{k} {n}" for k, n in v.wards_placed.items())
        lines.append(
            f"시야 점수 {v.vision_score} ({v.per_min}/분), 와드 설치 {placed}, "
            f"제어 와드 {v.control_wards}, 와드 제거 {v.wards_killed}."
        )
    if out.key_moments is not None:
        lines.append(f"다시 볼 장면 {len(out.key_moments)}개.")
        for m in out.key_moments:
            lines.append(f"{m.time} {m.reason}")
    return "\n".join(lines)


async def _solo_tier(st: AppState, puuid: str) -> tuple[str | None, str | None]:
    """현재 솔로랭크 티어와, 티어가 없을 때의 안내 문구를 반환함."""
    snap = st.store.latest_rank_snapshot()
    entries = snap[1] if snap else []
    solo = next((e for e in entries if e.get("queueType") == SOLO_QUEUE_TYPE), None)
    if solo is None:
        try:
            fetched = await st.riot.get_league_entries(puuid)
        except (RiotError, httpx.HTTPError):
            return None, form.NOTE_TIER_UNKNOWN
        entries = [e for e in fetched if e.get("queueType") in QUEUE_NAMES]
        st.store.add_rank_snapshot(entries, datetime.now(timezone.utc).isoformat())
        solo = next((e for e in entries if e.get("queueType") == SOLO_QUEUE_TYPE), None)
    if solo is None:
        return None, form.NOTE_UNRANKED
    return str(solo["tier"]), None


def _fmt(metric: str, v: float | None) -> str:
    if v is None:
        return "없음"
    return f"{v:.3f}" if metric in analysis.RATE_METRICS else f"{v:.2f}"


def _row_text(row: dict) -> str:
    head = f"{row['label']}: 내 {_fmt(row['metric'], row['my_value'])}({row['my_sample']}판)"
    if row["status"] != analysis.STATUS_OK:
        ref = "" if row["ref_value"] is None else f", 기준 {_fmt(row['metric'], row['ref_value'])}"
        return f"{head}{ref} [{row['status']}]"
    gap = "큰 차이" if row["strong_gap"] else "뚜렷한 차이" if row["clear_gap"] else "뚜렷하지 않은 차이"
    return (
        f"{head}, 기준 {_fmt(row['metric'], row['ref_value'])}({row['ref_sample']}판), "
        f"차이 {row['difference']:+g}, 오차폭 ±{_fmt(row['metric'], row['margin'])} [{gap}]"
    )


def _finding_text(title: str, items: list[dict]) -> str:
    if not items:
        return f"{title}: 없음."
    return f"{title}: " + ", ".join(f"{r['label']}({r['difference']:+g})" for r in items) + "."


def _form_text(out: FormOut) -> str:
    lines = [
        f"{out.period_desc}, {queue_label(out.queue)}, 포지션 {out.position}, {out.games}판 분석"
        + (f" (다른 포지션 {out.excluded_other_positions}판 제외)." if out.excluded_other_positions else ".")
        + f" 비교 기준: {out.ref_source}.",
        *[n + "." for n in out.notes],
    ]
    lines += [_row_text(r) for r in out.rows]
    lines.append(_finding_text("약점", out.weaknesses))
    lines.append(_finding_text("강점", out.strengths))
    if out.best_self_rows is not None:
        lines.append(f"참고 비교({out.best_self_ref_source}):")
        lines += [_row_text(r) for r in out.best_self_rows]
        lines.append(_finding_text("참고 약점", out.best_self_weaknesses or []))
        lines.append(_finding_text("참고 강점", out.best_self_strengths or []))
    lines.append("틸트 신호: " + (", ".join(out.tilt_signals) if out.tilt_signals else "없음") + ".")
    lines.append("스타일: " + ", ".join(out.style) + ".")
    recs = [(k, v) for k, v in out.records.items() if k != "updated_by_latest" and v]
    if recs:
        lines.append("개인 최고 기록(저장된 솔로랭크 전체):")
        for k, v in recs:
            mark = " [최신 경기가 갱신]" if k in out.records.get("updated_by_latest", []) else ""
            lines.append(f"{v['label']} {v['value']} ({v['champion']}){mark}")
    return "\n".join(lines)


async def _sync_for_analysis(
    st: AppState, p: Profile, queue: int | None, count: int
) -> list[str] | CallToolResult:
    """분석 전 동기화. 실패해도 저장된 경기가 있으면 안내 문구만 반환하고, 없으면 오류 결과를 반환함."""
    try:
        await run_sync(st.riot, st.store, p.puuid, queue=queue, limit=count, full=False)
    except (RiotError, httpx.HTTPError) as e:
        if not st.store.list_my_stats(queue=queue, limit=1, include_remakes=True):
            if isinstance(e, RiotError):
                return tool_error(str(e))
            return tool_error(NETWORK_ERROR_MESSAGE)
        return [SYNC_FAILED_NOTE]
    return []


CHAMPION_DEFAULT_COUNT = 50
CHAMPION_QUEUE = 420
NOTE_TIER_ALL = "솔로랭크 티어가 없어 OP.GG는 전체 티어 기준"
NOTE_OPGG_LIMIT = (
    f"OP.GG 조회와 주력 후보 계산은 판수 상위 {champions.OPGG_TOP_N}개 챔피언만 대상으로 함"
)
NOTE_NO_DDRAGON = "Data Dragon 데이터를 불러오지 못해 챔피언 이름·태그 정보가 제한됨"
NOTE_LANE_META_ALL = "OP.GG 레인 메타는 전체 티어 기준"
UNKNOWN_CHAMPION_MESSAGE = "알 수 없는 챔피언: {name}. 한국어 이름이나 영문 키를 확인해야 함."
UNKNOWN_CHAMPION_NO_DDRAGON = (
    " Data Dragon 데이터를 불러오지 못해 한국어 이름은 해석할 수 없고, 내 경기에 있는 영문 키만 인식함."
)
DASH = "-"
NEED_CHAMPION_MESSAGE = "이 모드는 champion 인자가 필요함. 한국어 이름이나 영문 키를 지정해야 함."


async def _ddragon_champions(st: AppState) -> dict | None:
    if st.ddragon is None:
        return None
    try:
        return await st.ddragon.champions()
    except httpx.HTTPError:
        return None


async def _opgg_analysis(
    st: AppState, key: str, team_position: str, tier: str | None, dd_keys: list[dict]
) -> dict | None:
    pos = opgg_position(team_position)
    if st.opgg is None or pos is None:
        return None
    return await st.opgg.champion_analysis(
        opgg_champion_key(key, dd_keys), pos, tier.lower() if tier else None
    )


def _v(v: Any) -> str:
    """None은 '-'로 표시(텍스트에 'None'이 나오지 않게 함)."""
    return DASH if v is None else str(v)


def _pct1(v: float | None) -> str:
    return DASH if v is None else f"{v * 100:.1f}%"


def _signed(v: float | None, fmt: str) -> str:
    return DASH if v is None else format(v, fmt)


def _unknown_champion_message(name: str, dd: dict | None) -> str:
    return UNKNOWN_CHAMPION_MESSAGE.format(name=name) + (UNKNOWN_CHAMPION_NO_DDRAGON if dd is None else "")


def _winloss_row_text(row: dict) -> str:
    """focus 모드의 승리 판 vs 패배 판 근거 행(my_value=승리 판, ref_value=패배 판)."""
    m = row["metric"]
    head = (f"{row['label']}: 승리 판 {_fmt(m, row['my_value'])}({row['my_sample']}판), "
            f"패배 판 {_fmt(m, row['ref_value'])}({_v(row['ref_sample'])}판)")
    if row["status"] != analysis.STATUS_OK:
        return f"{head} [{row['status']}]"
    gap = "큰 차이" if row["strong_gap"] else "뚜렷한 차이" if row["clear_gap"] else "뚜렷하지 않은 차이"
    return f"{head}, 차이 {row['difference']:+g}, 오차폭 ±{_fmt(m, row['margin'])} [{gap}]"


def _opgg_text(o: dict | None) -> str:
    if not o or o.get("status") != champions.STATUS_OK:
        return champions.STATUS_NO_OPGG
    head = f"OP.GG 승률 {_pct1(o['win_rate'])}, KDA {_v(o['kda'])}({_v(o['play'])}판 기준)"
    if o["win_rate_diff"] is None:
        return head
    wr = DASH if o["win_rate_diff"] is None else f"{o['win_rate_diff'] * 100:+.1f}%p"
    return f"{head}, 차이 승률 {wr}, KDA {_signed(o['kda_diff'], '+.2f')}"


def _champ_line(r: dict) -> str:
    base = (
        f"{r['name']}({r['position']}): {r['games']}판 {_pct1(r['win_rate'])}, "
        f"KDA {_v(r['kda'])}, 분당 CS {_v(r['cs_per_min'])}, 딜 비중 {_v(r['damage_share'])}"
    )
    if r["status"] != champions.STATUS_OK:
        base += f" [{r['status']}]"
    if r.get("opgg") is not None:
        base += " | " + _opgg_text(r["opgg"])
    return base


def _champion_text(out: ChampionReportOut) -> str:
    lines = [
        f"{out.period_desc}, {queue_label(out.queue)}, {out.games}판, OP.GG 비교 기준: {out.tier_basis}. "
        f"모드 {out.mode}" + (f", 챔피언 {out.champion_name}." if out.champion_name else ".")
    ]
    lines += [n + "." for n in out.notes]
    if out.mode == "overview":
        lines += [_champ_line(r) for r in out.rows]
        if out.main_candidates:
            lines.append("주력 후보 근거 행:")
            for c in out.main_candidates:
                lines.append(f"{c['name']}: {c['games']}판, " + ", ".join(c["evidence"]))
        else:
            lines.append("주력 후보 근거 행: 없음.")
    elif out.mode == "focus":
        if out.summary:
            lines.append(_champ_line({**out.summary, "name": out.champion_name,
                                      "opgg": out.opgg}))
        lines.append("승리 판 vs 패배 판:")
        for r in out.rows:
            lines.append(_winloss_row_text(r))
        if out.counters:
            for title, key in (("상대하기 어려운 챔피언(OP.GG)", "hard_against"),
                               ("상대하기 쉬운 챔피언(OP.GG)", "easy_against")):
                items = out.counters[key]
                lines.append(f"{title}: " + (", ".join(
                    f"{_v(c['champion'])}({_v(c['play'])}판)" for c in items) or "없음") + ".")
    elif out.mode == "pool" and out.pool:
        pl = out.pool
        lines.append(
            f"사용 챔피언 {pl['champion_count']}개, 상위 3챔 비중 {_pct1(pl['top3_share'])} "
            f"[{pl['label']}]."
        )
        lines += [f"{b['position_name']}: 챔피언 {b['champions']}개" for b in pl["by_position"]]
    elif out.mode == "similar" and out.similar:
        sm = out.similar
        base = sm.get("base_name") or sm["base_champion"]
        lines.append(f"기준 챔피언 {base}, 태그 {', '.join(sm['base_tags']) or DASH}.")
        for c in sm["candidates"]:
            lines.append(
                f"{c['name']}: 공통 태그 {', '.join(c['shared_tags'])}, OP.GG {_v(c['tier'])}티어, "
                f"승률 {_pct1(c['win_rate'])}, 내 기록 {c['my_games']}판"
            )
        if not sm["candidates"]:
            lines.append("조건에 맞는 챔피언 없음.")
    elif out.mode == "progress":
        for c in out.progress:
            lines.append(
                f"{c['chunk']}구간({c['games']}판): 승률 {_pct1(c['win_rate'])}, KDA {_v(c['kda'])}, "
                f"분당 CS {_v(c['cs_per_min'])}, 딜 비중 {_v(c['damage_share'])}, 데스 {_v(c['deaths'])}"
                + (f" [{c['status']}]" if c["status"] != champions.STATUS_OK else "")
            )
    if out.status != champions.STATUS_OK:
        lines.append(f"상태: {out.status}.")
    return "\n".join(lines)


MATCHUP_DEFAULT_COUNT = 50
NOTE_MATCHUP_ALL_TIERS = "OP.GG 매치업 요약은 티어 구분 없이 전체 기준"
NOTE_CANDIDATE_LIMIT = (
    f"OP.GG 조회는 후보 판수 상위 {matchups.OPGG_CANDIDATE_LIMIT}개만 수행함(나머지는 내 기록만 표시)"
)
NOTE_NO_DDRAGON_BUILD = "Data Dragon 데이터를 불러오지 못해 아이템·룬 이름과 조합을 집계하지 못함"
NOTE_NO_DETAIL = "경기 상세 데이터 없음으로 빌드 집계에서 제외한 판: {n}판"
NO_COMPARISON = "비교 없음"
NOTE_DEDUPED = "같은 챔피언으로 해석된 상대는 한 번만 표시함"
NEED_ENEMIES_MESSAGE = "enemies에 상대 챔피언을 1개 이상 지정해야 함."
TOO_MANY_ENEMIES_MESSAGE = f"enemies는 최대 {matchups.MAX_ENEMIES}개까지 지정할 수 있음."


async def _ddragon_data(st: AppState, name: str) -> dict | None:
    """Data Dragon items()/runes(). 없거나 실패하면 None."""
    if st.ddragon is None:
        return None
    try:
        return await getattr(st.ddragon, name)()
    except httpx.HTTPError:
        return None


async def _opgg_lane_matchup(
    st: AppState, team_position: str | None, my_key: str, opp_key: str, dd_keys: list[dict]
) -> dict | None:
    pos = opgg_position(team_position)
    if st.opgg is None or pos is None:
        return None
    return await st.opgg.lane_matchup(
        pos, opgg_champion_key(my_key, dd_keys), opgg_champion_key(opp_key, dd_keys)
    )


def _gold(v: float | None) -> str:
    return DASH if v is None else f"{v:+.0f}"


def _record_text(r: dict) -> str:
    base = f"내 기록 {r['games']}판 {_pct1(r['win_rate'])}"
    if r["timeline_games"]:
        base += f", 15분 골드차 평균 {_gold(r['avg_gold_diff_15'])}(타임라인 {r['timeline_games']}판)"
    else:
        base += ", 15분 골드차 -(타임라인 0판)"
    if r["status"] != champions.STATUS_OK:
        base += f" [{r['status']}]"
    return base


def _opgg_matchup_text(o: dict | None) -> str:
    if not o or o.get("status") != champions.STATUS_OK:
        return champions.STATUS_NO_OPGG
    m = o.get("matchup")
    head = (f"OP.GG 매치업 승률 {_pct1(m.get('win_rate'))}({_v(m.get('play'))}판)" if m
            else "OP.GG 매치업 표본 -")
    ov = o["my_champion_overall"]
    text = (f"{head}, 내 챔피언 전체 통계(매치업 아님) 승률 {_pct1(ov['win_rate'])} "
            f"KDA {_v(ov['kda'])}({_v(ov['play'])}판)")
    for title, key in (("라인 우세", "lane_advantage"), ("솔로킬 우세", "solo_kill_advantage"),
                       ("플레이 스타일", "recommended_play_style")):
        if o.get(key):
            text += f", {title} {o[key]}"
    return text


def _counter_text(row: dict) -> str:
    c = row.get("counter")
    if c is None:
        return row["opgg_status"]
    if c["my_win_rate"] is None:
        return f"OP.GG {c['relation']}"
    return f"OP.GG {c['relation']}(내 챔피언 승률 {_pct1(c['my_win_rate'])}, {_v(c['play'])}판)"


def _matchup_text(out: MatchupReportOut) -> str:
    head = f"{out.period_desc}, {queue_label(out.queue)}, {out.games}판, OP.GG 기준: {out.tier_basis}."
    if out.mode == "my_champion":
        head += f" 내 챔피언 {out.my_champion_name}, 같은 라인 상대와의 매치업."
    else:
        head += " 챔피언 선택용: 주 포지션 챔피언 풀 후보별 상대 기록."
    lines = [head] + [n + "." for n in out.notes]
    for r in out.rows:
        if r["status"] == matchups.STATUS_UNKNOWN:
            lines.append(f"{r['input']}: {matchups.STATUS_UNKNOWN}")
        elif out.mode == "my_champion":
            lines.append(f"{r['enemy_name']}: {_record_text(r)} | {_opgg_matchup_text(r['opgg'])}")
        else:
            lines.append(f"{r['candidate_name']} vs {r['enemy_name']}: {_record_text(r)} | "
                         f"{_counter_text(r)}")
    if out.status != champions.STATUS_OK:
        lines.append(f"상태: {out.status}.")
    return "\n".join(lines)


def _build_group_line(g: dict) -> str:
    line = f"{' + '.join(g['names'])}: {g['games']}판 {_pct1(g['win_rate'])}"
    if g["status"] != champions.STATUS_OK:
        line += f" [{g['status']}]"
    if g["overlap"] is not None:
        line += f" | OP.GG {'겹침' if g['overlap'] else '겹치지 않음'}"
    return line


def _opgg_options_text(o: dict) -> list[str]:
    if o.get("status") != champions.STATUS_OK:
        return [champions.STATUS_NO_OPGG]

    def names(x):
        return " + ".join(x.get("ids_names") or []) or DASH

    out = []
    for title, key in (("핵심 아이템", "core_items"), ("신발", "boots"), ("소환사 주문", "spells")):
        out.append(f"OP.GG 추천 {title}: " + (" / ".join(
            f"{names(x)}({_pct1(x['win_rate'])}, {_v(x['play'])}판)" for x in o[key]) or DASH))
    out.append("OP.GG 추천 룬: " + (" / ".join(
        f"{_v(x['primary_page_name'])} {', '.join(x['primary_rune_names'] or []) or DASH}"
        f"({_pct1(x['win_rate'])}, {_v(x['play'])}판)" for x in o["runes"]) or DASH))
    return out


def _build_text(out: BuildReportOut) -> str:
    lines = [f"{out.period_desc}, {queue_label(out.queue)}, {out.champion_name} {out.games}판, "
             f"OP.GG 비교 기준: {out.tier_basis}."]
    lines += [n + "." for n in out.notes]
    for title, groups in (("최종 완성 아이템별(군화·재료·소모품·장신구 제외)", out.core_items),
                          ("신발 조합", out.boots), ("핵심 룬 조합", out.runes),
                          ("소환사 주문 조합", out.spells)):
        if groups:
            lines.append(f"{title}:")
            lines += [_build_group_line(g) for g in groups]
        if groups is out.core_items and out.core_buckets:
            lines.append("OP.GG 추천 핵심 아이템 보유 개수별:")
            for b in out.core_buckets:
                line = (f"{b['of']}개 중 {b['matched']}개: {b['games']}판 {_pct1(b['win_rate'])}")
                if b["status"] != champions.STATUS_OK:
                    line += f" [{b['status']}]"
                lines.append(line)
    if out.opgg is not None:
        lines += _opgg_options_text(out.opgg)
    if out.status != champions.STATUS_OK:
        lines.append(f"상태: {out.status}.")
    return "\n".join(lines)


def _splits_text(out: SplitsOut) -> str:
    lines = [
        f"{out.period_desc}, {queue_label(out.queue)}, {out.games}판, 분할 기준 {out.split_by}.",
        *[n + "." for n in out.notes],
    ]
    for g in out.groups:
        rate = "없음" if g.win_rate is None else f"{g.win_rate * 100:.1f}%"
        mets = ", ".join(
            f"{analysis.METRICS[k][0]} {_fmt(k, v)}" for k, v in g.metrics.items()
        )
        lines.append(f"{g.label}: {g.games}판, 승률 {rate}, {mets} [{g.status}]")
    if out.rows:
        lines.append(f"{splits.WIN_LOSS_SOURCE} 비교(내 값은 승리 판, 기준은 패배 판):")
        lines += [_row_text(r) for r in out.rows]
    if out.duo_partners:
        lines.append(
            "듀오 파트너 후보: "
            + ", ".join(f"{d.riot_id}({d.games_together}판)" for d in out.duo_partners)
            + "."
        )
    return "\n".join(lines)


def _pct(v: float | None) -> str:
    return "없음" if v is None else f"{v * 100:.1f}%"


def _habits_text(out: HabitsOut) -> str:
    lines = [
        f"{out.period_desc}, {queue_label(out.queue)}, 전체 {out.games_total}판 중 타임라인 있는 "
        f"{out.games_with_timeline}판 사용, 데스 {out.deaths_total}회. 상태: {out.status}.",
        *[n + "." for n in out.notes],
    ]
    if out.games_with_timeline:
        lines.append(
            "데스 시간대: "
            + ", ".join(f"{b['bucket']} {b['deaths']}회" for b in out.death_time_buckets)
        )
        lines.append(
            "데스 구역 상위: "
            + (", ".join(f"{z['zone']} {z['deaths']}회" for z in out.top_death_zones) or "없음")
        )
        lines.append(
            f"오브젝트 직전 데스 {out.pre_objective_deaths}/{out.deaths_total}회 "
            f"({_pct(out.pre_objective_death_rate)})"
        )
        lines.append(
            f"연속 데스 발생 {out.consecutive_death_games}/{out.games_with_timeline}판 "
            f"({_pct(out.consecutive_death_game_rate)})"
        )
        fb = out.first_back_avg
        lines.append(
            "첫 귀환 평균: "
            + ("없음" if fb is None else f"{fb['time']}({fb['seconds']}초, {fb['games']}판)")
        )
        lines.append(
            f"솔로킬 당한 데스 {out.solo_deaths}/{out.deaths_total}회 ({_pct(out.solo_death_rate)})"
        )
    wc = out.win_compare
    if wc.get("status") == habits.STATUS_OK:
        d = wc["diff_win_minus_all"]
        lines.append(
            f"승리 {wc['wins']}판 vs 전체 차이(승리-전체): 판당 데스 {d['deaths_per_game']}, "
            f"오브젝트 직전 데스율 {d['pre_objective_death_rate']}, "
            f"연속 데스 판 비율 {d['consecutive_death_game_rate']}, "
            f"첫 귀환 초 {d['first_back_avg_s']}, 솔로킬 비율 {d['solo_death_rate']}"
        )
    else:
        lines.append(
            f"승리 판 비교: 판단 보류(승리 {wc.get('wins', 0)}판, 패배 {wc.get('losses', 0)}판, "
            f"각 {habits.MIN_WIN_COMPARE_GAMES}판 이상 필요)"
        )
    return "\n".join(lines)


def create_server(state_factory: Callable[[], AppState] | None = None) -> MCPServer:
    factory = state_factory or _default_state

    @asynccontextmanager
    async def lifespan(server: MCPServer) -> AsyncIterator[AppState]:
        state = factory()
        try:
            yield state
        finally:
            aclose = getattr(state.riot, "aclose", None)
            if aclose is not None:
                await aclose()
            if state.http is not None and state_factory is None:
                await state.http.aclose()
            # 테스트가 주입한 store는 호출자가 닫으므로 기본 팩토리일 때만 닫음
            if state_factory is None:
                state.store.close()

    mcp = MCPServer("lol-coach-mcp", instructions=INSTRUCTIONS, lifespan=lifespan)

    @mcp.tool()
    async def my_profile(
        action: Literal["get", "set", "forget"],
        ctx: Context,
        riot_id: str | None = None,
        main_position: str | None = None,
        goal_tier: str | None = None,
        tone: Literal["직설", "격려", "데이터만"] | None = None,
        detail_level: Literal["초보", "보통", "숙련"] | None = None,
        include_matches: bool = False,
    ) -> Annotated[CallToolResult, ProfileOut]:
        """내 계정 프로필을 조회(get)·등록/수정(set)·삭제(forget)함. riot_id는 '이름#태그' 형식."""
        st = _state(ctx)
        try:
            if action == "get":
                p = st.store.get_profile()
                if p is None:
                    return tool_result(_profile_out(None), NO_PROFILE_GUIDE_TEXT)
                return tool_result(_profile_out(p), _profile_text(p))

            if action == "forget":
                st.store.forget_all(include_matches)
                suffix = " 저장된 경기 데이터도 삭제함." if include_matches else ""
                return tool_result(_profile_out(None), f"프로필, 동기화 기록, 코치 기억(판정 기록·과제·일지)을 삭제함.{suffix}")

            # set
            p = st.store.get_profile()
            notice = ""
            if riot_id is not None:
                name, sep, tag = riot_id.partition("#")
                name, tag = name.strip(), tag.strip()
                if not sep or "#" in tag:
                    return tool_error(RIOT_ID_FORMAT_ERROR)
                if not name or not tag:
                    return tool_error(RIOT_ID_FORMAT_ERROR)
                account = await st.riot.get_account_by_riot_id(name, tag)
                new_name = account.get("gameName", name)
                new_tag = account.get("tagLine", tag)
                owner = st.store.get_meta("data_owner_puuid")
                if (p is not None and p.puuid != account["puuid"]) or (
                    owner is not None and owner != account["puuid"]
                ):
                    st.store.forget_all(include_matches=True)
                    p = None
                    notice = " 이전 계정의 저장 데이터가 삭제됨."
                if p is None:
                    p = Profile(
                        puuid=account["puuid"],
                        game_name=new_name,
                        tag_line=new_tag,
                        registered_at=datetime.now(timezone.utc).isoformat(),
                    )
                else:
                    p = replace(p, game_name=new_name, tag_line=new_tag)
            elif p is None:
                return tool_error(NO_PROFILE_MESSAGE)

            updates = {
                k: v
                for k, v in {
                    "main_position": main_position,
                    "goal_tier": goal_tier,
                    "tone": tone,
                    "detail_level": detail_level,
                }.items()
                if v is not None
            }
            p = replace(p, **updates)
            st.store.save_profile(p)
            return tool_result(_profile_out(p), _profile_text(p) + notice)
        except RiotNotFound:
            return tool_error(NOT_FOUND_MESSAGE)
        except RiotError as e:
            return tool_error(str(e))
        except httpx.HTTPError:
            return tool_error(NETWORK_ERROR_MESSAGE)

    @mcp.tool()
    async def get_coach_context(ctx: Context) -> Annotated[CallToolResult, CoachContextOut]:
        """코칭 대화 시작 시 호출. 프로필 설정, 마지막 동기화 시각, 저장된 경기 수를 반환함."""
        st = _state(ctx)
        p = st.store.get_profile()
        if p is None:
            return tool_error(NO_PROFILE_MESSAGE)
        last = st.store.get_meta("last_sync_at")
        all_stats = st.store.list_my_stats(queue=None, include_remakes=True)
        stored = len(all_stats)
        now = _now_kst(st)
        major = memory.latest_major(all_stats)
        goals = st.store.list_goals(status="진행 중")
        findings = [
            {**f, "stale": memory.is_stale(f, now=now, current_major=major)}
            for f in st.store.list_findings(memory.CONTEXT_FINDINGS)
        ]
        journal = st.store.list_journal(memory.CONTEXT_JOURNAL)
        out = CoachContextOut(
            profile=_profile_out(p), last_sync_at=last, stored_matches=stored,
            goals=goals, findings=findings, journal=journal,
        )
        text = _context_text(p, out)
        return tool_result(out, text)

    @mcp.tool()
    async def sync_matches(
        ctx: Context,
        count: int = 100,
        full: bool = False,
        queue: int | None = 420,
    ) -> Annotated[CallToolResult, SyncOut]:
        """Riot에서 새 경기를 받아 로컬에 저장함. count는 최대 신규 경기 수, full이면 전체 기간, queue 기본 420(솔로랭크)."""
        st = _state(ctx)
        p = st.store.get_profile()
        if p is None:
            return tool_error(NO_PROFILE_MESSAGE)
        count = max(1, count)

        async def progress(done: int, total: int, msg: str) -> None:
            await ctx.report_progress(done, total, msg)

        try:
            r = await run_sync(
                st.riot, st.store, p.puuid, queue=queue, limit=count, full=full, progress=progress
            )
        except RiotError as e:
            return tool_error(str(e))
        except httpx.HTTPError:
            return tool_error(NETWORK_ERROR_MESSAGE)
        out = SyncOut(
            new_matches=r.new_matches,
            total_stored=r.total_stored,
            queue=r.queue,
            stopped_reason=r.stopped_reason,
        )
        text = f"새 경기 {r.new_matches}개 저장함. 저장된 전체 경기 {r.total_stored}개."
        return tool_result(out, text)

    @mcp.tool()
    async def get_profile(ctx: Context) -> Annotated[CallToolResult, PlayerProfileOut]:
        """내 랭크(솔로/자유), 소환사 레벨, 상위 숙련도, 최근 폼을 조회함. 호출 시 랭크 스냅샷을 저장함."""
        st = _state(ctx)
        p = st.store.get_profile()
        if p is None:
            return tool_error(NO_PROFILE_MESSAGE)
        try:
            entries = await st.riot.get_league_entries(p.puuid)
            summoner = await st.riot.get_summoner(p.puuid)
            mastery = await st.riot.get_top_mastery(p.puuid, 3)
        except RiotError as e:
            return tool_error(str(e))
        except httpx.HTTPError:
            return tool_error(NETWORK_ERROR_MESSAGE)
        entries = [e for e in entries if e.get("queueType") in QUEUE_NAMES]
        st.store.add_rank_snapshot(entries, datetime.now(timezone.utc).isoformat())
        ranks = [
            RankOut(
                queue=QUEUE_NAMES[e["queueType"]],
                tier=e["tier"],
                division=e.get("rank", ""),
                lp=e["leaguePoints"],
                wins=e["wins"],
                losses=e["losses"],
                win_rate=_win_rate(e["wins"], e["losses"]),
            )
            for e in entries
            if e.get("queueType") in QUEUE_NAMES
        ]
        form = recent_form(st.store.list_my_stats(queue=PROFILE_QUEUE, limit=20))
        out = PlayerProfileOut(
            riot_id=f"{p.game_name}#{p.tag_line}",
            level=summoner.get("summonerLevel"),
            ranks=ranks,
            unranked=not ranks,
            top_mastery=[
                MasteryOut(
                    champion_id=m["championId"],
                    level=m["championLevel"],
                    points=m["championPoints"],
                )
                for m in mastery
            ],
            recent_form=form,
            queue_used=PROFILE_QUEUE,
        )
        head = f"{out.riot_id} 레벨 {out.level}." if out.level is not None else f"{out.riot_id}."
        if ranks:
            rank_text = " ".join(
                f"{r.queue} {r.tier} {r.division} {r.lp}LP ({r.wins}승 {r.losses}패, 승률 {r.win_rate}%)."
                for r in ranks
            )
        else:
            rank_text = "솔로랭크·자유랭크 모두 언랭크."
        form_text = f"최근 폼: {form['label']} (최근 {form['games']}판 {form['wins']}승)."
        return tool_result(out, f"{head} {rank_text} {form_text}")

    @mcp.tool()
    async def get_match_history(
        ctx: Context,
        count: int = 10,
        queue: int | None = 420,
    ) -> Annotated[CallToolResult, MatchHistoryOut]:
        """최근 경기 목록(다시하기 포함)을 조회함. count는 1~50, queue 기본 420(솔로랭크). 매번 새 경기를 동기화한 뒤 저장된 데이터로 응답하며, 동기화에 실패하면 저장된 데이터 기준으로 응답함."""
        st = _state(ctx)
        p = st.store.get_profile()
        if p is None:
            return tool_error(NO_PROFILE_MESSAGE)
        count = max(1, min(count, MAX_HISTORY_COUNT))
        notes: list[str] = []
        try:
            await run_sync(st.riot, st.store, p.puuid, queue=queue, limit=count, full=False)
        except (RiotError, httpx.HTTPError) as e:
            if not st.store.list_my_stats(queue=queue, limit=1, include_remakes=True):
                if isinstance(e, RiotError):
                    return tool_error(str(e))
                return tool_error(NETWORK_ERROR_MESSAGE)
            notes.append(SYNC_FAILED_NOTE)
        stats = st.store.list_my_stats(queue=queue, limit=count, include_remakes=True)
        last_sync = st.store.get_meta("last_sync_at")
        rows = [
            MatchRowOut(
                match_id=s.match_id,
                played_at=_played_at(s.game_creation),
                champion=s.champion,
                position=s.position,
                result=_result(s),
                kills=s.kills,
                deaths=s.deaths,
                assists=s.assists,
                kda=kda(s.kills, s.deaths, s.assists),
                cs=s.cs,
                cs_per_min=s.cs_per_min,
                tags=match_tags(s),
            )
            for s in stats
        ]
        out = MatchHistoryOut(queue_used=queue, last_sync_at=last_sync, notes=notes, matches=rows)
        if not rows:
            return tool_result(out, "조회된 경기가 없음.")
        valid = [r for r in rows if r.result != "다시하기"]
        wins = sum(1 for r in valid if r.result == "승")
        remakes = len(rows) - len(valid)
        notes_text = [n + "." for n in notes]
        lines = [
            f"데이터 기준: {queue_label(queue)}, 마지막 동기화 "
            + (kst_text(last_sync) if last_sync else "기록 없음")
            + ".",
            *notes_text,
            f"최근 {len(rows)}경기: {wins}승 {len(valid) - wins}패"
            + (f", 다시하기 {remakes}판." if remakes else ".")
        ]
        for r in rows:
            lines.append(
                f"{kst_text(r.played_at)} {r.champion} {r.position} {r.result} "
                f"{r.kills}/{r.deaths}/{r.assists} (KDA {r.kda}) CS {r.cs} ({r.cs_per_min}/분)"
                + (f" [{', '.join(r.tags)}]" if r.tags else "")
            )
        return tool_result(out, "\n".join(lines))

    @mcp.tool()
    async def get_match_detail(
        match_id: str, ctx: Context
    ) -> Annotated[CallToolResult, MatchDetailOut]:
        """한 경기의 양 팀 전체 참가자 지표와 내 딜량 순위를 조회함. 저장돼 있지 않으면 Riot에서 받아 저장함."""
        st = _state(ctx)
        p = st.store.get_profile()
        if p is None:
            return tool_error(NO_PROFILE_MESSAGE)
        loaded = await _load_match(st, p, match_id)
        if isinstance(loaded, CallToolResult):
            return loaded
        match, stats = loaded
        participants = match["info"]["participants"]
        me = next(x for x in participants if x["puuid"] == p.puuid)
        team_members = [x for x in participants if x["teamId"] == me["teamId"]]
        teams = []
        for tid in sorted({x["teamId"] for x in participants}):
            members = [x for x in participants if x["teamId"] == tid]
            teams.append(
                DetailTeamOut(
                    team_id=tid,
                    win=bool(members[0].get("win", False)),
                    players=[
                        DetailPlayerOut(
                            riot_id=(
                                f"{x['riotIdGameName']}#{x.get('riotIdTagline', '')}"
                                if x.get("riotIdGameName")
                                else "알 수 없음"
                            ),
                            champion=x["championName"],
                            position=x.get("teamPosition") or "UNKNOWN",
                            k=x["kills"],
                            d=x["deaths"],
                            a=x["assists"],
                            damage_to_champions=x.get("totalDamageDealtToChampions", 0),
                            cs=x.get("totalMinionsKilled", 0) + x.get("neutralMinionsKilled", 0),
                            is_me=x["puuid"] == p.puuid,
                        )
                        for x in members
                    ],
                )
            )
        rank_team = _damage_rank(team_members, me)
        rank_game = _damage_rank(participants, me)
        out = MatchDetailOut(
            match_id=match_id,
            played_at=_played_at(stats.game_creation),
            queue_id=stats.queue_id,
            champion=stats.champion,
            position=stats.position,
            result=_result(stats),
            kills=stats.kills,
            deaths=stats.deaths,
            assists=stats.assists,
            kda=kda(stats.kills, stats.deaths, stats.assists),
            cs=stats.cs,
            cs_per_min=stats.cs_per_min,
            tags=match_tags(stats),
            teams=teams,
            my_damage_rank_in_team=rank_team,
            my_damage_rank_in_game=rank_game,
            damage_share=stats.damage_share,
            kill_participation=stats.kill_participation,
        )
        text = (
            f"{kst_text(out.played_at)} {out.champion} {out.position} {out.result}. "
            f"{out.kills}/{out.deaths}/{out.assists} (KDA {out.kda}), CS {out.cs} ({out.cs_per_min}/분). "
            f"팀 내 딜량 {rank_team}위, 전체 {rank_game}위, 팀 딜 비중 {round(out.damage_share * 100, 1)}%, "
            f"킬 관여율 {round(out.kill_participation * 100, 1)}%."
        )
        return tool_result(out, text)

    @mcp.tool()
    async def review_match(
        ctx: Context,
        match_id: str | None = None,
        focus: Literal[
            "overview", "deaths", "lane", "objectives", "vision", "key_moments"
        ] = "overview",
    ) -> Annotated[CallToolResult, ReviewOut]:
        """한 판을 타임라인 기반으로 복기함. match_id 생략 시 최근 솔로랭크 경기. focus: overview/deaths/lane/objectives/vision/key_moments."""
        st = _state(ctx)
        p = st.store.get_profile()
        if p is None:
            return tool_error(NO_PROFILE_MESSAGE)
        try:
            if match_id is None:
                recent = st.store.list_my_stats(queue=REVIEW_QUEUE, limit=1)
                if not recent:
                    await run_sync(
                        st.riot,
                        st.store,
                        p.puuid,
                        queue=REVIEW_QUEUE,
                        limit=REVIEW_SYNC_LIMIT,
                        full=False,
                    )
                    recent = st.store.list_my_stats(queue=REVIEW_QUEUE, limit=1)
                if not recent:
                    return tool_error(NO_REVIEW_MATCH_MESSAGE)
                match_id = recent[0].match_id
            loaded = await _load_match(st, p, match_id)
            if isinstance(loaded, CallToolResult):
                return loaded
            match, stats = loaded

            out = ReviewOut(
                match_id=match_id,
                champion=stats.champion,
                position=stats.position,
                result=_result(stats),
                duration=review.mmss(stats.duration_s * 1000),
                queue_id=stats.queue_id,
                focus=focus,
                status="ok",
            )
            if stats.is_remake:
                out.status = "다시하기"
                out.notes = ["다시하기 경기는 복기 대상이 아님"]
                return tool_result(out, _review_text(out))

            summary = ReviewSummaryOut(
                kills=stats.kills,
                deaths=stats.deaths,
                assists=stats.assists,
                kda=kda(stats.kills, stats.deaths, stats.assists),
            )
            timeline = st.store.get_timeline(match_id)
            if timeline is None:
                try:
                    timeline = await st.riot.get_timeline(match_id)
                except RiotNotFound:
                    out.notes.append(NO_TIMELINE_NOTE)
                else:
                    st.store.save_timeline(match_id, timeline)
            if timeline is not None:
                try:
                    review.participant_id(timeline, p.puuid)
                except ValueError:
                    timeline = None
                    out.notes.append(NO_TIMELINE_ME_NOTE)
            if timeline is None:
                out.status = "판단 보류"
                if focus == "overview":
                    out.summary = summary
                return tool_result(out, _review_text(out))

            if focus == "overview":
                dcount = {ph: 0 for ph in review.PHASES}
                for d in review.deaths(match, timeline, p.puuid):
                    dcount[d["phase"]] += 1
                ob = review.objectives(match, timeline, p.puuid)
                summary.death_counts_by_phase = dcount
                summary.objective_participation_rate = ob["participation_rate"]
                summary.objective_epic_count = ob["my_team_epic_count"]
                out.summary = summary
                out.lane = LaneOut.model_validate(
                    review.lane_checkpoints(match, timeline, p.puuid, OVERVIEW_LANE_MINUTES)
                )
                out.key_moments = [
                    MomentOut.model_validate(m)
                    for m in review.key_moments(match, timeline, p.puuid, OVERVIEW_MOMENT_LIMIT)
                ]
            elif focus == "lane":
                out.lane = LaneOut.model_validate(review.lane_checkpoints(match, timeline, p.puuid))
            elif focus == "deaths":
                out.deaths = [
                    DeathOut.model_validate(d) for d in review.deaths(match, timeline, p.puuid)
                ]
            elif focus == "objectives":
                out.objectives = ObjectivesOut.model_validate(
                    review.objectives(match, timeline, p.puuid)
                )
            elif focus == "vision":
                out.vision = VisionOut.model_validate(review.vision(match, timeline, p.puuid))
            else:
                out.key_moments = [
                    MomentOut.model_validate(m)
                    for m in review.key_moments(match, timeline, p.puuid)
                ]
            return tool_result(out, _review_text(out))
        except RiotError as e:
            return tool_error(str(e))
        except httpx.HTTPError:
            return tool_error(NETWORK_ERROR_MESSAGE)

    @mcp.tool()
    async def analyze_form(
        ctx: Context,
        period: Literal["recent_n", "session", "week", "month", "season"] = "recent_n",
        count: int = FORM_DEFAULT_COUNT,
        compare_to: Literal["tier_baseline", "best_self", "previous_period"] = "tier_baseline",
        queue: int | None = 420,
    ) -> Annotated[CallToolResult, FormOut]:
        """기간별 폼을 지표별 근거(내 값, 기준, 차이, 오차폭, 판정 상태)로 분석함. period: recent_n/session/week/month/season, compare_to: tier_baseline(티어 기준선, 없으면 내 승리 판 평균 동반)/best_self(내 승리 판 평균)/previous_period(직전 동일 기간). 주 포지션 경기만 분석하며 매번 새 경기를 동기화하고, 실패하면 저장된 데이터 기준으로 응답함."""
        st = _state(ctx)
        p = st.store.get_profile()
        if p is None:
            return tool_error(NO_PROFILE_MESSAGE)
        count = max(1, min(count, ANALYSIS_MAX_COUNT))
        sync_notes = await _sync_for_analysis(st, p, queue, count)
        if isinstance(sync_notes, CallToolResult):
            return sync_notes
        stats = st.store.list_my_stats(queue=queue, include_remakes=True)
        tier = tier_note = None
        if compare_to == "tier_baseline":
            tier, tier_note = await _solo_tier(st, p.puuid)
        built = form.build_form(
            stats,
            period=period,
            count=count,
            compare_to=compare_to,
            tier=tier,
            tier_note=tier_note,
            get_baseline=st.store.get_baseline,
        )
        built["notes"] = sync_notes + built["notes"]
        solo_now = [s for s in stats if not s.is_remake]
        st.store.add_findings(
            memory.finding_rows(
                built,
                created_at=_now_kst(st).isoformat(timespec="seconds"),
                season_major=memory.latest_major(solo_now),
            )
        )
        all_solo = st.store.list_my_stats(queue=analysis.RECORD_QUEUE, include_remakes=True)
        latest = max(
            (s for s in all_solo if not s.is_remake), key=lambda s: s.game_creation, default=None
        )
        out = FormOut(
            queue=queue,
            records=analysis.personal_records(all_solo, latest),
            **built,
        )
        return tool_result(out, _form_text(out))

    @mcp.tool()
    async def compare_splits(
        ctx: Context,
        split_by: Literal[
            "win_loss", "time_of_day", "weekday", "game_length", "position", "patch", "duo"
        ],
        period: Literal["recent_n", "session", "week", "month", "season"] = "recent_n",
        count: int = SPLITS_DEFAULT_COUNT,
        queue: int | None = 420,
    ) -> Annotated[CallToolResult, SplitsOut]:
        """같은 기간의 경기를 기준별로 나눠 그룹별 판수·승률·지표 평균을 비교함. split_by: win_loss(승리 판 vs 패배 판, 근거 행 포함)/time_of_day(KST 시간대)/weekday(요일)/game_length(게임 시간)/position/patch/duo(기간 내 3판 이상 함께한 팀원을 듀오로 추정, 듀오 판 vs 혼자 한 판의 내 지표만). 그룹 판수 5 미만은 판단 보류. 상관관계이며 원인이 아님. 매번 새 경기를 동기화하고, 실패하면 저장된 데이터 기준으로 응답함."""
        st = _state(ctx)
        p = st.store.get_profile()
        if p is None:
            return tool_error(NO_PROFILE_MESSAGE)
        count = max(1, min(count, ANALYSIS_MAX_COUNT))
        sync_notes = await _sync_for_analysis(st, p, queue, count)
        if isinstance(sync_notes, CallToolResult):
            return sync_notes
        stats = st.store.list_my_stats(queue=queue, include_remakes=True)
        picked, period_desc = periods.select(stats, period, count)
        built = splits.build_splits(picked, split_by, st.store.get_match)
        out = SplitsOut(
            split_by=split_by,
            period_desc=period_desc,
            games=len(picked),
            queue=queue,
            groups=[SplitGroupOut(**g) for g in built["groups"]],
            rows=built["rows"],
            duo_partners=[DuoPartnerOut(**d) for d in built["duo_partners"]],
            notes=sync_notes + built["notes"],
        )
        return tool_result(out, _splits_text(out))

    @mcp.tool()
    async def find_habits(
        ctx: Context,
        period: Literal["recent_n", "session", "week", "month", "season"] = "recent_n",
        count: int = HABITS_DEFAULT_COUNT,
        queue: int | None = 420,
    ) -> Annotated[CallToolResult, HabitsOut]:
        """타임라인이 있는 경기에서 반복 습관을 탐지함: 데스 시간대(5분 구간)·구역 상위 3, 오브젝트 직전 데스 비율, 연속 데스 발생 판 비율, 첫 귀환 시각 평균, 솔로킬 당한 비율, 승리 판과의 차이(승·패 각 3판 이상일 때). 사용 판수·횟수를 함께 표시하며 타임라인 5판 미만이면 판단 보류. 타임라인 없는 경기는 호출당 최대 10판까지 보충 수집하고, 매번 새 경기를 동기화하며 실패하면 저장된 데이터 기준으로 응답함."""
        st = _state(ctx)
        p = st.store.get_profile()
        if p is None:
            return tool_error(NO_PROFILE_MESSAGE)
        count = max(1, min(count, ANALYSIS_MAX_COUNT))
        sync_notes = await _sync_for_analysis(st, p, queue, count)
        if isinstance(sync_notes, CallToolResult):
            return sync_notes
        stats = st.store.list_my_stats(queue=queue, include_remakes=True)
        picked, period_desc = periods.select(stats, period, count)
        notes = list(sync_notes)
        try:
            missing = [s.match_id for s in picked if st.store.get_timeline(s.match_id) is None]
            to_fetch = [m for m in missing if st.store.has_match(m)][:HABITS_BACKFILL_LIMIT]
            skipped_404 = 0
            for i, mid in enumerate(to_fetch):
                await ctx.report_progress(i, len(to_fetch), f"타임라인 수집 {i + 1}/{len(to_fetch)}")
                try:
                    tl = await st.riot.get_timeline(mid)
                except RiotNotFound:
                    skipped_404 += 1
                    continue
                st.store.save_timeline(mid, tl)
            if to_fetch:
                await ctx.report_progress(len(to_fetch), len(to_fetch), "타임라인 수집 완료")
        except RiotError as e:
            return tool_error(str(e))
        except httpx.HTTPError:
            return tool_error(NETWORK_ERROR_MESSAGE)
        if to_fetch:
            notes.append(f"타임라인 {len(to_fetch) - skipped_404}판 새로 수집")
        if skipped_404:
            notes.append(f"타임라인이 없는 경기 {skipped_404}판 제외")
        remaining = len(missing) - len(to_fetch)
        if remaining > 0:
            notes.append(
                f"타임라인 미수집 {remaining}판 남음(호출당 최대 {HABITS_BACKFILL_LIMIT}판 수집)"
            )

        games: list[tuple[dict, dict]] = []
        no_me = 0
        for s in picked:
            tl = st.store.get_timeline(s.match_id)
            match = st.store.get_match(s.match_id)
            if tl is None or match is None:
                continue
            try:
                review.participant_id(tl, p.puuid)
            except ValueError:
                no_me += 1
                continue
            games.append((match, tl))
        if no_me:
            notes.append(f"타임라인에 내 계정이 없어 제외된 경기 {no_me}판")
        built = habits.detect_habits(games, p.puuid)
        out = HabitsOut(
            period_desc=period_desc,
            queue=queue,
            games_total=len(picked),
            notes=notes,
            **built,
        )
        return tool_result(out, _habits_text(out))

    @mcp.tool()
    async def champion_report(
        ctx: Context,
        champion: str | None = None,
        mode: Literal["overview", "focus", "pool", "similar", "progress"] = "overview",
        count: int = CHAMPION_DEFAULT_COUNT,
    ) -> Annotated[CallToolResult, ChampionReportOut]:
        """내 솔로랭크 최근 N판의 챔피언 리포트. overview: 챔피언별 판수·승률·KDA·분당 CS·딜 비중과 OP.GG 같은 티어·포지션 평균 대비 차이, 주력 후보 근거 행(판수 10 이상이고 승률 또는 KDA가 OP.GG 평균 이상). focus(champion 필수): 해당 챔피언 지표, 승/패 차이, OP.GG 카운터 상위 3. pool: 챔피언 폭(좁음/보통/넓음)과 포지션별 챔피언 수. similar: 내 최다 챔피언과 태그가 겹치고 OP.GG 메타 1~2티어인 챔피언 최대 5. progress(champion 필수): 시간순 5판 단위 추이. champion은 한국어 이름 또는 영문 키. 챔피언 5판 미만은 판단 보류, OP.GG 조회는 판수 상위 5개만 하며 실패하면 해당 부분만 OP.GG 데이터 없음. 매번 새 경기를 동기화하고 실패하면 저장된 데이터 기준."""
        st = _state(ctx)
        p = st.store.get_profile()
        if p is None:
            return tool_error(NO_PROFILE_MESSAGE)
        if mode in ("focus", "progress") and not champion:
            return tool_error(NEED_CHAMPION_MESSAGE)
        count = max(1, min(count, ANALYSIS_MAX_COUNT))
        sync_notes = await _sync_for_analysis(st, p, CHAMPION_QUEUE, count)
        if isinstance(sync_notes, CallToolResult):
            return sync_notes
        all_stats = st.store.list_my_stats(queue=CHAMPION_QUEUE, include_remakes=True)
        picked, period_desc = periods.select(all_stats, "recent_n", count)
        notes = list(sync_notes)
        dd = await _ddragon_champions(st)
        if dd is None:
            notes.append(NOTE_NO_DDRAGON)
        dd_keys = [{"key": k} for k in (dd or {})]
        groups = champions.group_by_champion(picked)

        key = None
        if champion:
            key = champions.resolve_champion(champion, dd, list(groups))
            if key is None:
                return tool_error(_unknown_champion_message(champion, dd))

        tier = None
        if mode in ("overview", "focus"):
            tier, _ = await _solo_tier(st, p.puuid)
        tier_basis = f"{tier} 티어 기준" if tier else "전체 티어 기준"
        if mode in ("overview", "focus") and not tier:
            notes.append(NOTE_TIER_ALL)
        if mode == "similar":
            notes.append(NOTE_LANE_META_ALL)
        if mode in ("pool", "progress"):
            tier_basis = "해당 없음"

        out = ChampionReportOut(
            mode=mode, period_desc=period_desc, queue=CHAMPION_QUEUE, games=len(picked),
            tier_basis=tier_basis, champion=key,
            champion_name=champions.display_name(key, dd) if key else None, notes=notes,
        )
        if not picked:
            out.status = champions.STATUS_HOLD
            out.notes.append("기간 내 솔로랭크 경기가 없음")
            return tool_result(out, _champion_text(out))

        if mode == "overview":
            rows = champions.overview_rows(picked, dd)
            top = rows[: champions.OPGG_TOP_N]
            fetched = await asyncio.gather(*(
                _opgg_analysis(st, r["champion"], r["position"], tier, dd_keys) for r in top
            ))
            for r, data in zip(top, fetched):
                r["opgg"] = champions.opgg_block(r, data)
            if len(rows) > len(top):
                out.notes.append(NOTE_OPGG_LIMIT)
            out.rows = rows
            out.main_candidates = champions.main_candidates(rows)
        elif mode == "focus":
            games = groups.get(key, [])
            if not games:
                out.status = champions.STATUS_HOLD
                out.notes.append("기간 내 해당 챔피언 경기가 없음")
            else:
                row = champions.champion_row(key, games, dd)
                data = await _opgg_analysis(st, key, row["position"], tier, dd_keys)
                row["opgg"] = champions.opgg_block(row, data)
                out.summary = {k: v for k, v in row.items() if k != "opgg"}
                out.opgg = row["opgg"]
                out.counters = champions.counters(data)
                out.rows = champions.focus_rows(games)
                if row["status"] != champions.STATUS_OK:
                    out.status = champions.STATUS_HOLD
                    out.notes.append(f"해당 챔피언 {champions.MIN_GAMES}판 미만이라 판단 보류")
        elif mode == "pool":
            out.pool = champions.pool_summary(picked)
            if out.pool["label"] == champions.STATUS_HOLD:
                out.status = champions.STATUS_HOLD
        elif mode == "similar":
            base = max(groups, key=lambda k: (len(groups[k]), k))
            pos = opgg_position(champions.main_position(groups[base]))
            meta = None
            if dd is not None:
                meta = await st.opgg.lane_meta(pos) if (st.opgg is not None and pos) else None
            if dd is None or not meta:
                out.status = champions.STATUS_NO_DDRAGON if dd is None else champions.STATUS_NO_OPGG
                out.similar = {"base_champion": base, "base_name": champions.display_name(base, dd),
                               "base_tags": [], "candidates": []}
            else:
                out.similar = champions.similar_candidates(picked, dd, meta)
        else:  # progress
            games = groups.get(key, [])
            out.progress = champions.progress_chunks(games)
            if not games:
                out.status = champions.STATUS_HOLD
                out.notes.append("기간 내 해당 챔피언 경기가 없음")
            elif len(games) < champions.MIN_GAMES:
                out.status = champions.STATUS_HOLD
                out.notes.append(f"해당 챔피언 {champions.MIN_GAMES}판 미만이라 판단 보류")
        return tool_result(out, _champion_text(out))

    @mcp.tool()
    async def matchup_report(
        ctx: Context,
        enemies: list[str],
        my_champion: str | None = None,
        count: int = MATCHUP_DEFAULT_COUNT,
    ) -> Annotated[CallToolResult, MatchupReportOut]:
        """상대 챔피언(enemies, 한국어 이름 또는 영문 키, 최대 5개) 대비 내 솔로랭크 기록과 OP.GG 요약. my_champion 지정 시: 내가 그 챔피언으로 같은 라인에서 각 상대를 만난 판들의 판수·승률·15분 골드차 평균(저장된 타임라인이 있는 판만, 판수 표기)과 OP.GG 매치업 요약(매치업 승률, 내 챔피언 전체 통계는 별도 표기). my_champion 생략 시(챔피언 선택용): 내 주 포지션 챔피언 중 5판 이상 한 후보 각각이 enemies 상대와 만난 기록과 OP.GG 카운터 정보를 근거 행으로 정렬(내 판수, 승률 순, OP.GG 조회는 후보 5개까지). 상대별 3판 미만은 판단 보류, 못 찾은 챔피언은 알 수 없는 챔피언. 새 타임라인은 받지 않으며 OP.GG 실패 시 해당 부분만 OP.GG 데이터 없음. 매번 새 경기를 동기화하고 실패하면 저장된 데이터 기준."""
        st = _state(ctx)
        p = st.store.get_profile()
        if p is None:
            return tool_error(NO_PROFILE_MESSAGE)
        if not enemies:
            return tool_error(NEED_ENEMIES_MESSAGE)
        if len(enemies) > matchups.MAX_ENEMIES:
            return tool_error(TOO_MANY_ENEMIES_MESSAGE)
        count = max(1, min(count, ANALYSIS_MAX_COUNT))
        sync_notes = await _sync_for_analysis(st, p, CHAMPION_QUEUE, count)
        if isinstance(sync_notes, CallToolResult):
            return sync_notes
        all_stats = st.store.list_my_stats(queue=CHAMPION_QUEUE, include_remakes=True)
        picked, period_desc = periods.select(all_stats, "recent_n", count)
        notes = list(sync_notes)
        dd = await _ddragon_champions(st)
        if dd is None:
            notes.append(NOTE_NO_DDRAGON)
        dd_keys = [{"key": k} for k in (dd or {})]
        entries = matchups.lane_entries(picked, st.store.get_match, p.puuid)
        played = sorted({e["stats"].champion for e in entries}
                        | {e["opponent"] for e in entries if e["opponent"]})
        resolved = [(q, champions.resolve_champion(q, dd, played)) for q in enemies]
        deduped = matchups.dedupe_enemies(resolved)
        if len(deduped) < len(resolved):
            notes.append(NOTE_DEDUPED)
        resolved = deduped
        unknown_rows = [
            {"input": q, "enemy": None, "enemy_name": None, "status": matchups.STATUS_UNKNOWN,
             "games": 0, "wins": 0, "win_rate": None, "timeline_games": 0,
             "avg_gold_diff_15": None}
            for q, k in resolved if k is None
        ]
        known = [(q, k) for q, k in resolved if k is not None]

        def rec(es):
            return matchups.record(es, st.store.get_timeline, p.puuid)

        if my_champion:
            my_key = champions.resolve_champion(my_champion, dd, played)
            if my_key is None:
                return tool_error(_unknown_champion_message(my_champion, dd))
            mine = [e for e in entries if e["stats"].champion == my_key]
            base_games = [e["stats"] for e in mine] or picked
            team_pos = champions.main_position(base_games) if base_games else None
            notes.append(NOTE_MATCHUP_ALL_TIERS)
            data = await asyncio.gather(*(
                _opgg_lane_matchup(st, team_pos, my_key, k, dd_keys) for _, k in known
            ))
            rows = []
            for (q, k), d in zip(known, data):
                r = rec([e for e in mine if e["opponent"] == k])
                rows.append({"input": q, "enemy": k, "enemy_name": champions.display_name(k, dd),
                             **r, "opgg": matchups.matchup_block(d)})
            order = {q: i for i, q in enumerate(enemies)}  # 입력 순서 유지
            rows = sorted(rows + unknown_rows, key=lambda r: order[r["input"]])
            out = MatchupReportOut(
                mode="my_champion", period_desc=period_desc, queue=CHAMPION_QUEUE,
                games=len(picked), tier_basis="전체 티어 기준", my_champion=my_key,
                my_champion_name=champions.display_name(my_key, dd), position=team_pos,
                rows=rows, notes=notes,
            )
            if not picked:
                out.status = champions.STATUS_HOLD
                out.notes.append("기간 내 솔로랭크 경기가 없음")
            return tool_result(out, _matchup_text(out))

        # 챔피언 선택 모드
        main_pos = champions.main_position(picked) if picked else None
        cands = matchups.pick_candidates(picked, main_pos) if main_pos else []
        tier, _ = await _solo_tier(st, p.puuid)
        tier_basis = f"{tier} 티어 기준" if tier else "전체 티어 기준"
        if not tier:
            notes.append(NOTE_TIER_ALL)
        queried = cands[: matchups.OPGG_CANDIDATE_LIMIT]
        if len(cands) > len(queried):
            notes.append(NOTE_CANDIDATE_LIMIT)
        analyses = await asyncio.gather(*(
            _opgg_analysis(st, c["champion"], main_pos, tier, dd_keys) for c in queried
        ))
        analysis_by = {c["champion"]: d for c, d in zip(queried, analyses)}
        rows = []
        for c in cands:
            queried_c = c["champion"] in analysis_by
            data = analysis_by.get(c["champion"])
            games_c = [e for e in entries
                       if e["stats"].champion == c["champion"] and e["stats"].position == main_pos]
            for q, k in known:
                r = rec([e for e in games_c if e["opponent"] == k])
                info = (dd or {}).get(k) or {}
                counter = matchups.counter_info(data, info.get("id"), info.get("name"))
                if not queried_c:
                    opgg_status = matchups.STATUS_NOT_QUERIED
                elif data is None:
                    opgg_status = champions.STATUS_NO_OPGG
                else:
                    opgg_status = champions.STATUS_OK
                rows.append({
                    "input": q, "candidate": c["champion"],
                    "candidate_name": champions.display_name(c["champion"], dd),
                    "enemy": k, "enemy_name": champions.display_name(k, dd), **r,
                    "counter": counter, "opgg_status": opgg_status,
                })
        empty = [r for r in rows if r["games"] == 0]
        rows = matchups.sort_pick_rows([r for r in rows if r["games"] > 0]) + unknown_rows
        if empty:
            notes.append("기록 없음: " + ", ".join(
                f"{r['candidate_name']} vs {r['enemy_name']}" for r in empty))
        out = MatchupReportOut(
            mode="pick", period_desc=period_desc, queue=CHAMPION_QUEUE, games=len(picked),
            tier_basis=tier_basis, position=main_pos, rows=rows, notes=notes,
            candidates=[{
                "champion": c["champion"], "name": champions.display_name(c["champion"], dd),
                "games": c["games"], "wins": c["wins"], "win_rate": round(c["wins"] / c["games"], 3),
            } for c in cands],
        )
        if not cands:
            out.status = champions.STATUS_HOLD
            out.notes.append(
                f"주 포지션에서 {matchups.CANDIDATE_MIN_GAMES}판 이상 한 챔피언이 없어 후보를 계산하지 못함"
            )
        return tool_result(out, _matchup_text(out))

    @mcp.tool()
    async def build_report(
        ctx: Context,
        champion: str,
        count: int = MATCHUP_DEFAULT_COUNT,
    ) -> Annotated[CallToolResult, BuildReportOut]:
        """내 솔로랭크 최근 N판 중 지정 챔피언(한국어 이름 또는 영문 키) 판들의 빌드 리포트. 최종 아이템(item0~5, 신발·소모품·장신구 제외 핵심 아이템 조합), 신발, 핵심 룬(첫 번째 룬), 소환사 주문 조합별 판수·승률(조합 3판 미만은 판단 보류, 분류별 상위 5개)과, OP.GG의 같은 티어·포지션 추천 핵심 아이템·신발·룬·소환사 주문과 겹치는지(겹침 여부와 OP.GG 순위). 언랭이면 전체 티어 기준. Data Dragon이 없으면 아이템·룬 조합은 생략하고 소환사 주문만, OP.GG 실패 시 겹침 표시 없이 내 데이터만 반환. 매번 새 경기를 동기화하고 실패하면 저장된 데이터 기준."""
        st = _state(ctx)
        p = st.store.get_profile()
        if p is None:
            return tool_error(NO_PROFILE_MESSAGE)
        count = max(1, min(count, ANALYSIS_MAX_COUNT))
        sync_notes = await _sync_for_analysis(st, p, CHAMPION_QUEUE, count)
        if isinstance(sync_notes, CallToolResult):
            return sync_notes
        all_stats = st.store.list_my_stats(queue=CHAMPION_QUEUE, include_remakes=True)
        picked, period_desc = periods.select(all_stats, "recent_n", count)
        notes = list(sync_notes)
        dd = await _ddragon_champions(st)
        if dd is None:
            notes.append(NOTE_NO_DDRAGON)
        dd_keys = [{"key": k} for k in (dd or {})]
        groups = champions.group_by_champion(picked)
        key = champions.resolve_champion(champion, dd, list(groups))
        if key is None:
            return tool_error(_unknown_champion_message(champion, dd))
        games = groups.get(key, [])
        out = BuildReportOut(
            period_desc=period_desc, queue=CHAMPION_QUEUE, games=len(games),
            tier_basis=NO_COMPARISON, champion=key,
            champion_name=champions.display_name(key, dd), notes=notes,
        )
        if not games:
            out.status = champions.STATUS_HOLD
            out.notes.append("기간 내 해당 챔피언 경기가 없음")
            return tool_result(out, _build_text(out))

        team_pos = champions.main_position(games)
        out.position = team_pos
        items = await _ddragon_data(st, "items")
        rune_names = await _ddragon_data(st, "runes")
        if items is None or rune_names is None:
            out.notes.append(NOTE_NO_DDRAGON_BUILD)
        core_rows, boot_rows, rune_rows, spell_rows = [], [], [], []
        no_detail = 0
        for s in games:
            me = builds.participant(st.store.get_match(s.match_id), p.puuid)
            if me is None:
                no_detail += 1
                continue
            if items is not None:
                core, boots = builds.split_items(me, items)
                core_rows.append((core, s.win))
                boot_rows.append((boots, s.win))
            if rune_names is not None:
                ks = builds.keystone_id(me)
                rune_rows.append(((ks,) if ks else (), s.win))
            spell_rows.append((builds.spell_key(me), s.win))
        if no_detail:
            out.notes.append(NOTE_NO_DETAIL.format(n=no_detail))

        tier, _ = await _solo_tier(st, p.puuid)
        out.tier_basis = f"{tier} 티어 기준" if tier else "전체 티어 기준"
        if not tier:
            out.notes.append(NOTE_TIER_ALL)
        data = await _opgg_analysis(st, key, team_pos, tier, dd_keys)
        out.opgg = builds.opgg_summary(data)
        have = out.opgg["status"] == champions.STATUS_OK

        def finish(rows, title, namer, option_key, matcher):
            found = builds.tally(rows)
            if len(found) > builds.GROUP_LIMIT:
                out.notes.append(f"{title} 조합 {len(found)}개 중 상위 {builds.GROUP_LIMIT}개만 표시함")
                found = found[: builds.GROUP_LIMIT]
            for g in found:
                g["names"] = [namer(i) for i in g["ids"]]
                rank = builds.opgg_rank(data.get(option_key), matcher(g)) if have else None
                g["opgg_rank"] = rank
                g["overlap"] = (rank is not None) if have else None
            return found

        def item_name(i):
            return ((items or {}).get(i) or {}).get("name") or f"아이템 {i}"

        def rune_name(i):
            return ((rune_names or {}).get(i) or {}).get("name") or f"룬 {i}"

        opts = builds.options(data.get("core_items")) if have else []
        rec_ids = list(opts[0].get("ids") or []) if opts else []
        out.core_items = builds.item_stats(core_rows)
        for g in out.core_items:
            g["names"] = [item_name(g["ids"][0])]
            g["opgg_rank"] = None
            g["overlap"] = (g["ids"][0] in rec_ids) if rec_ids else None
        out.core_buckets = builds.core_buckets(core_rows, rec_ids) if rec_ids and items else []
        out.boots = finish(boot_rows, "신발", item_name, "boots",
                           lambda g: builds.core_matches(g["ids"]))
        out.runes = finish(rune_rows, "핵심 룬", rune_name, "runes",
                           lambda g: builds.keystone_matches(rune_name(g["ids"][0])))
        out.spells = finish(
            spell_rows, "소환사 주문", lambda i: builds.SPELL_NAMES.get(i, f"소환사 주문 {i}"),
            "summoner_spells", lambda g: builds.spells_match(g["ids"]))
        return tool_result(out, _build_text(out))

    return mcp
