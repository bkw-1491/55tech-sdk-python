from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from fiftyfivetech.resources import BaseModel, Response
from fiftyfivetech.utils import from_timestamp


@dataclass(kw_only=True, slots=True)
class FixtureStatus(BaseModel):
    """
    Where a fixture is in its lifecycle.
    """
    live: bool
    status_id: int | None = None
    status_name: str | None = None


@dataclass(kw_only=True, slots=True)
class Sport(BaseModel):
    """
    A sport, e.g. ``10`` Soccer.
    """
    sport_id: int
    sport_name: str


@dataclass(kw_only=True, slots=True)
class Tournament(BaseModel):
    """
    A competition, e.g. a league; ``category_name`` is usually its country.
    """
    tournament_id: int
    tournament_name: str
    category_name: str | None = None


@dataclass(kw_only=True, slots=True)
class Season(BaseModel):
    """
    The season a fixture belongs to.
    """
    season_id: int | None = None
    season_name: str | None = None
    season_round: int | None = None


@dataclass(kw_only=True, slots=True)
class Venue(BaseModel):
    """
    Where a fixture is played.
    """
    venue_id: int | None = None
    venue_name: str | None = None
    venue_location: str | None = None


@dataclass(kw_only=True, slots=True)
class Participants(BaseModel):
    """
    A fixture's two sides, in 55Tech's order: outcome ``"1"`` is ``participant1``.
    """
    participant1_id: int
    participant1_name: str
    participant1_short_name: str | None = None
    participant1_abbr: str | None = None
    participant1_rot_nr: int | None = None
    participant2_id: int
    participant2_name: str
    participant2_short_name: str | None = None
    participant2_abbr: str | None = None
    participant2_rot_nr: int | None = None


@dataclass(kw_only=True, slots=True)
class Score(BaseModel):
    """
    The score of one period, e.g. ``"p1"``, ``"fulltime"``, ``"result"``.
    """
    period: str
    participant1_score: int | None = None
    participant2_score: int | None = None
    updated_at: datetime | None = None


@dataclass(kw_only=True, slots=True)
class Clock(BaseModel):
    """
    A live fixture's match clock; every field is ``None`` before kick-off.
    """
    current_period: str | None = None
    current_time: str | None = None
    remaining_time: str | None = None
    remaining_time_in_period: str | None = None
    stopped: bool | None = None
    updated_at: datetime | None = None


@dataclass(kw_only=True, slots=True)
class BookmakerFixtureMeta(BaseModel):
    """
    One bookmaker's state on one fixture, from ``GET /fixtures?bookmakers=...`` or the
    ``bookmakers`` stream channel. Present only while the bookmaker has valid odds on it.
    """
    bookmaker: str
    bookmaker_fixture_id: str | None = None
    fixture_path: str | None = None
    has_odds: bool
    stale_odds: bool
    suspended: bool
    participants_rotated: bool
    updated_at: datetime
    meta: dict[str, Any] | None = None


@dataclass(kw_only=True, slots=True)
class Fixture(BaseModel):
    """
    A fixture (an event): who plays, when, and its live state.
    """
    fixture_id: str
    reissued_fixture_id: str | None = None
    status: FixtureStatus
    sport: Sport
    tournament: Tournament
    season: Season | None = None
    venue: Venue | None = None
    start_time: int
    true_start_time: datetime | None = None
    true_end_time: datetime | None = None
    participants: Participants
    scores: dict[str, Score] = field(default_factory=dict)
    clock: Clock | None = None
    expected_periods: int | None = None
    period_length: int | None = None
    external_providers: dict[str, Any] = field(default_factory=dict)
    bookmakers: dict[str, BookmakerFixtureMeta] = field(default_factory=dict)

    @property
    def starts_at(self) -> datetime:
        """
        ``start_time`` as an aware UTC datetime.
        """
        return from_timestamp(self.start_time)


@dataclass(kw_only=True, slots=True)
class FixturesResponse(Response):
    """
    Fixtures (``GET /fixtures``; the array is wrapped as ``fixtures``).
    """
    fixtures: list[Fixture] = field(default_factory=list)


@dataclass(kw_only=True, slots=True)
class FixtureMapping(BaseModel):
    """
    One bookmaker fixture id resolved to a 55Tech fixture.
    One row per id asked for. The unknown side of an unpaired id is ``None`` (the
    spec says otherwise, the API does this); pairings fill in as coverage catches up.
    """
    bookmaker: str
    fixture_id: str | None = None
    bookmaker_fixture_id: str | None = None
    participants_rotated: bool | None = None


@dataclass(kw_only=True, slots=True)
class FixtureMappingResponse(Response):
    """
    Fixture pairings (``GET /fixtures/mapping``; the array is wrapped as ``mappings``),
    one per id asked for, paired or not.
    """
    mappings: list[FixtureMapping] = field(default_factory=list)


@dataclass(kw_only=True, slots=True)
class OddsRecord(BaseModel):
    """
    One bookmaker's price on one outcome, as REST and the stream both send it.

    Keyed by its ``oddsId``, ``"{fixtureId}:{bookmaker}:{outcomeId}:{playerId}"``;
    treat the key as opaque and read the fields. Each record is the latest state,
    not a delta. Kind and line come from the market dictionary, via ``outcome_id``;
    the bookmaker's own ids sit beside them as sent.
    """
    bookmaker: str
    outcome_id: int
    player_id: int
    price: float
    active: bool
    changed_at: int
    market_id: int | None = None
    market_active: bool | None = None
    main_line: bool | None = None
    limit: float | None = None
    bookmaker_changed_at: int | None = None
    bookmaker_market_id: str | None = None
    bookmaker_outcome_id: str | None = None
    price_fractional: str | None = None
    price_american: int | None = None
    meta: dict[str, Any] | None = None
    betslip: str | None = None


@dataclass(kw_only=True, slots=True)
class FixtureOddsResponse(Fixture):
    """
    One fixture with its current odds (``GET /fixtures/odds``): every ``Fixture``
    field, plus ``odds`` and the response timings.
    """
    odds: dict[str, dict[str, OddsRecord]] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)
    sent_at: datetime = field(init=False, repr=False, compare=False)
    received_at: datetime = field(init=False, repr=False, compare=False)
    latency_ms: float = field(init=False, repr=False, compare=False)
