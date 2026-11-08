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
    last_sync_at: str | None = None
    notes: list[str] = []
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


class LanePointOut(BaseModel):
    minute: int
    my_cs: int
    opp_cs: int
    cs_diff: int
    my_gold: int
    opp_gold: int
    gold_diff: int
    my_level: int
    opp_level: int
    xp_diff: int


class LaneOut(BaseModel):
    opponent_champion: str | None = None
    status: str
    points: list[LanePointOut]


class DeathOut(BaseModel):
    time: str
    ms: int
    phase: str
    zone: str
    killer_champion: str | None = None
    assists: int
    solo: bool
    tags: list[str]


class ObjectiveEventOut(BaseModel):
    time: str
    type: str
    sub_type: str | None = None
    team: str
    participated: bool
    dead_at_time: bool


class ObjectivesOut(BaseModel):
    events: list[ObjectiveEventOut]
    my_team_epic_count: int
    my_participation: int
    participation_rate: float


class VisionOut(BaseModel):
    vision_score: int
    per_min: float
    wards_placed: dict[str, int]
    control_wards: int
    wards_killed: int


class MomentOut(BaseModel):
    time: str
    ms: int
    reason: str


class ReviewSummaryOut(BaseModel):
    kills: int
    deaths: int
    assists: int
    kda: float
    death_counts_by_phase: dict[str, int] | None = None
    objective_participation_rate: float | None = None
    objective_epic_count: int | None = None


class ReviewOut(BaseModel):
    match_id: str
    champion: str
    position: str
    result: str
    duration: str
    queue_id: int
    focus: str
    status: str
    summary: ReviewSummaryOut | None = None
    lane: LaneOut | None = None
    deaths: list[DeathOut] | None = None
    objectives: ObjectivesOut | None = None
    vision: VisionOut | None = None
    key_moments: list[MomentOut] | None = None
    notes: list[str] = []


class FormOut(BaseModel):
    period_desc: str
    games: int
    queue: int | None = None
    position: str
    excluded_other_positions: int
    compare_to: str
    compare_used: str
    ref_source: str
    rows: list[dict]
    weaknesses: list[dict]
    strengths: list[dict]
    best_self_rows: list[dict] | None = None
    best_self_ref_source: str | None = None
    best_self_weaknesses: list[dict] | None = None
    best_self_strengths: list[dict] | None = None
    tilt_signals: list[str]
    style: list[str]
    records: dict
    notes: list[str] = []


class SplitGroupOut(BaseModel):
    label: str
    games: int
    win_rate: float | None = None
    metrics: dict[str, float | None]
    status: str


class DuoPartnerOut(BaseModel):
    riot_id: str
    games_together: int


class SplitsOut(BaseModel):
    split_by: str
    period_desc: str
    games: int
    queue: int | None = None
    groups: list[SplitGroupOut]
    rows: list[dict] = []
    duo_partners: list[DuoPartnerOut] = []
    notes: list[str] = []


class HabitsOut(BaseModel):
    period_desc: str
    queue: int | None = None
    games_total: int
    games_with_timeline: int
    deaths_total: int
    death_time_buckets: list[dict]
    top_death_zones: list[dict]
    pre_objective_deaths: int
    pre_objective_death_rate: float | None = None
    consecutive_death_games: int
    consecutive_death_game_rate: float | None = None
    first_back_avg: dict | None = None
    solo_deaths: int
    solo_death_rate: float | None = None
    win_compare: dict
    status: str
    notes: list[str] = []


class ChampionReportOut(BaseModel):
    mode: str
    period_desc: str
    queue: int | None = None
    games: int
    tier_basis: str
    champion: str | None = None
    champion_name: str | None = None
    status: str = "ok"
    rows: list[dict] = []
    main_candidates: list[dict] = []
    summary: dict | None = None
    opgg: dict | None = None
    counters: dict | None = None
    pool: dict | None = None
    similar: dict | None = None
    progress: list[dict] = []
    notes: list[str] = []


class MatchupReportOut(BaseModel):
    mode: str  # "my_champion" | "pick"
    period_desc: str
    queue: int | None = None
    games: int
    tier_basis: str
    my_champion: str | None = None
    my_champion_name: str | None = None
    position: str | None = None
    status: str = "ok"
    candidates: list[dict] = []
    rows: list[dict] = []
    notes: list[str] = []


class BuildReportOut(BaseModel):
    period_desc: str
    queue: int | None = None
    games: int
    tier_basis: str
    champion: str
    champion_name: str
    position: str | None = None
    status: str = "ok"
    core_items: list[dict] = []
    core_buckets: list[dict] = []
    boots: list[dict] = []
    runes: list[dict] = []
    spells: list[dict] = []
    opgg: dict | None = None
    notes: list[str] = []
