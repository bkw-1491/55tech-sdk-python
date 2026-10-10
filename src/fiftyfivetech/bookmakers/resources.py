from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from fiftyfivetech.resources import BaseModel, Response


@dataclass(kw_only=True, slots=True)
class Bookmaker(BaseModel):
    """
    A bookmaker the key can see (``GET /bookmakers``).
    """
    slug: str
    bookmaker_name: str
    active: bool
    domain: str | None = None
    websocket_pregame: bool | None = None
    websocket_live: bool | None = None
    player_props: bool | None = None
    max_delay_pregame_in_sec: float | None = None
    max_delay_live_in_sec: float | None = None
    max_delay_pregame_main_in_sec: float | None = None
    available_countries: list[str] = field(default_factory=list)
    available_sports: str | None = None
    limit_currency: str | None = None
    stale_threshold_sec: Any = None
    stale_odds_since: datetime | None = None
    last_odds_at: datetime | None = None


@dataclass(kw_only=True, slots=True)
class BookmakersResponse(Response):
    """
    The bookmaker catalogue (``GET /bookmakers``; the array is wrapped as ``bookmakers``).
    """
    bookmakers: list[Bookmaker] = field(default_factory=list)
