from dataclasses import dataclass, field

from fiftyfivetech.resources import BaseModel, Response


@dataclass(kw_only=True, slots=True)
class MarketOutcome(BaseModel):
    """
    One outcome (side) of a market definition.
    """
    outcome_id: int
    outcome_name: str | None = None


@dataclass(kw_only=True, slots=True)
class Market(BaseModel):
    """
    What a 55Tech market id is: odds records name markets and outcomes only by id.

    A market is the coordinates ``market_type -> period -> handicap``; every line is its
    own ``market_id``, so "Over 2.5" and "Over 3.5" are two markets. Corners and
    bookings have their own types (``spreads-corners``, ``totals-bookings``, ...).
    ``market_type`` and ``period`` are open vocabularies that grow, and have been
    renamed (see the 2026-10-05 changelog): key on ``market_id`` where you can.
    """
    market_id: int
    sport_id: int | None = None
    market_type: str | None = None
    period: str | None = None
    handicap: float | None = None
    market_length: int | None = None
    player_prop: bool | None = None
    market_name: str | None = None
    market_name_short: str | None = None
    outcomes: list[MarketOutcome] = field(default_factory=list)


@dataclass(kw_only=True, slots=True)
class MarketsResponse(Response):
    """
    Market definitions (``GET /markets``; the array is wrapped as ``markets``).
    """
    markets: list[Market] = field(default_factory=list)
