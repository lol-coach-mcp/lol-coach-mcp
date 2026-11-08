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


class RankOut(BaseModel):
    queue: str
    tier: str
    division: str
    lp: int
    wins: int
    losses: int
    win_rate: float


class MasteryOut(BaseModel):
    champion_id: int
    level: int
    points: int


class PlayerProfileOut(BaseModel):
    riot_id: str
    level: int | None = None
    ranks: list[RankOut]
    unranked: bool
    top_mastery: list[MasteryOut]
    recent_form: dict
    queue_used: int = 420


class MatchRowOut(BaseModel):
    match_id: str
    played_at: str
    champion: str
    position: str
    result: str
    kills: int
    deaths: int
    assists: int
    kda: float
    cs: int
    cs_per_min: float
    tags: list[str]


class MatchHistoryOut(BaseModel):
    queue_used: int | None = None
    matches: list[MatchRowOut]


class DetailPlayerOut(BaseModel):
    riot_id: str
    champion: str
    position: str
    k: int
    d: int
    a: int
    damage_to_champions: int
    cs: int
    is_me: bool


class DetailTeamOut(BaseModel):
    team_id: int
    win: bool
    players: list[DetailPlayerOut]


class MatchDetailOut(BaseModel):
    match_id: str
    played_at: str
    queue_id: int
    champion: str
    position: str
    result: str
    kills: int
    deaths: int
    assists: int
    kda: float
    cs: int
    cs_per_min: float
    tags: list[str]
    teams: list[DetailTeamOut]
    my_damage_rank_in_team: int
    my_damage_rank_in_game: int
    damage_share: float
    kill_participation: float
