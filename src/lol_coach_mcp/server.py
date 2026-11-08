"""MCP 서버 정의와 계정 관련 tool."""
from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Literal

import httpx
from mcp.server.mcpserver import Context, MCPServer
from mcp_types import CallToolResult

from lol_coach_mcp.config import Settings, load_settings
from lol_coach_mcp import analysis, form, periods, review, splits
from lol_coach_mcp.models import (
    CoachContextOut,
    DeathOut,
    DetailPlayerOut,
    DetailTeamOut,
    DuoPartnerOut,
    FormOut,
    LaneOut,
    MasteryOut,
    MatchDetailOut,
    MatchHistoryOut,
    MatchRowOut,
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


def _default_state() -> AppState:
    settings = load_settings()
    return AppState(
        settings=settings,
        riot=RiotClient(settings),
        store=Store(settings.data_dir / "lol-coach.sqlite3"),
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
                return tool_result(_profile_out(None), f"프로필과 동기화 기록을 삭제함.{suffix}")

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
        stored = len(st.store.list_my_stats(queue=None, include_remakes=True))
        out = CoachContextOut(profile=_profile_out(p), last_sync_at=last, stored_matches=stored)
        sync_text = f"마지막 동기화: {kst_text(last)}." if last else "아직 동기화한 적 없음."
        text = f"{_profile_text(p)} 저장된 경기 {stored}개. {sync_text}"
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

    return mcp
