"""MCP 서버 정의와 계정 관련 tool."""
from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Annotated, Any, Literal

import httpx
from mcp.server.mcpserver import Context, MCPServer
from mcp_types import CallToolResult

from lol_coach_mcp.config import Settings, load_settings
from lol_coach_mcp.models import CoachContextOut, ProfileOut, SyncOut
from lol_coach_mcp.results import tool_error, tool_result
from lol_coach_mcp.riot.client import RiotClient
from lol_coach_mcp.riot.errors import RiotError
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

INSTRUCTIONS = "롤 개인 코칭 서버. 대화 시작 시 get_coach_context 호출"


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


def _state(ctx: Context) -> AppState:
    return ctx.request_context.lifespan_context


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
                if p is not None and p.puuid != account["puuid"]:
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
        sync_text = f"마지막 동기화: {last}." if last else "아직 동기화한 적 없음."
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

    return mcp
