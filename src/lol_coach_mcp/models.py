"""tool 출력 모델."""
from __future__ import annotations

from pydantic import BaseModel


class ProfileOut(BaseModel):
    registered: bool
    riot_id: str | None = None
    game_name: str | None = None
    tag_line: str | None = None
    main_position: str | None = None
    goal_tier: str | None = None
    tone: str | None = None
    detail_level: str | None = None


class CoachContextOut(BaseModel):
    profile: ProfileOut
    last_sync_at: str | None = None
    stored_matches: int = 0


class SyncOut(BaseModel):
    new_matches: int
    total_stored: int
    queue: int | None = None
    stopped_reason: str
