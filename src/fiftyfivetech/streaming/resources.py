from dataclasses import dataclass, field
from typing import Any, Literal

from fiftyfivetech.fixtures.resources import BookmakerFixtureMeta, OddsRecord
from fiftyfivetech.resources import BaseModel

ReceiveType = Literal["json", "zstd", "zstd-dict"]
ODDS_CHANNEL = "odds"
BOOKMAKERS_CHANNEL = "bookmakers"


@dataclass(kw_only=True, slots=True)
class Access(BaseModel):
    """
    What the key may receive; set by the key, not the login's filters.
    """
    live: bool | None = None
    pregame: bool | None = None


@dataclass(kw_only=True, slots=True)
class ResumeInfo(BaseModel):
    """
    What the gateway can replay on a later login.
    """
    server_epoch: str | None = None
    resume_window_ms: int | None = None
    replay_channels: list[str] = field(default_factory=list)
    server_entry_ids: dict[str, str] = field(default_factory=dict)


@dataclass(kw_only=True, slots=True)
class LoginOk(BaseModel):
    """
    The gateway accepted the login.
    """
    type: Literal["login_ok"]
    receive_type: str | None = None
    access: Access | None = None
    channels: list[str] = field(default_factory=list)
    resume: ResumeInfo | None = None


@dataclass(kw_only=True, slots=True)
class ErrorMessage(BaseModel):
    """
    The gateway refused the login or a later message, e.g. ``login_failed``.
    """
    type: Literal["error", "login_error"]
    message: str | None = None
    code: Any = None
    reason: str | None = None
    error: Any = None


@dataclass(kw_only=True, slots=True)
class ResumeComplete(BaseModel):
    """
    A resumed login's gap has been replayed; nothing was missed.
    """
    type: Literal["resume_complete"]
    server_epoch: str | None = None


@dataclass(kw_only=True, slots=True)
class SnapshotRequired(BaseModel):
    """
    A resumed login's gap could not be replayed, so state on ``channels`` may be
    stale: re-fetch it over REST (``get_fixture_odds``, ``get_fixtures``) and merge,
    keeping the later ``changedAt`` per row. The stream has already dropped those
    channels' cursors, and keeps streaming.
    """
    type: Literal["snapshot_required"]
    channels: list[str] = field(default_factory=list)
    reason: str | None = None
    server_epoch: str | None = None
    resume_window_ms: int | None = None
    server_entry_ids: dict[str, str] = field(default_factory=dict)


@dataclass(kw_only=True, slots=True)
class Reconnect(BaseModel):
    """
    The gateway is about to close the socket (a release or rebalance): reconnect now,
    whatever the ``reason``. It keeps streaming for a few seconds first. The new
    gateway has a new epoch, so expect ``SnapshotRequired`` after.
    """
    type: Literal["reconnect"]
    reason: str | None = None


@dataclass(kw_only=True, slots=True)
class DictFrame(BaseModel):
    """
    A zstd dictionary, sent after ``LoginOk`` on a ``"zstd-dict"`` stream; the stream
    loads it itself.
    """
    type: Literal["dict"]
    dict_id: int
    channel: str | None = None
    dict_version: str | None = None
    encoding: str | None = None
    data: str = field(default="", repr=False)


@dataclass(kw_only=True, slots=True)
class OddsPayload(BaseModel):
    """
    The odds that changed on one fixture.
    """
    fixture_id: str
    odds: dict[str, dict[str, OddsRecord]] = field(default_factory=dict)


@dataclass(kw_only=True, slots=True)
class BookmakersPayload(BaseModel):
    """
    Bookmaker state that changed on one fixture; a bookmaker absent from the
    fixture has no current odds on it.
    """
    fixture_id: str
    bookmakers: dict[str, BookmakerFixtureMeta] = field(default_factory=dict)


@dataclass(kw_only=True, slots=True)
class OddsUpdate(BaseModel):
    """
    A data frame on the ``odds`` channel.
    """
    type: Literal["UPDATE"]
    channel: Literal["odds"]
    payload: OddsPayload
    entry_id: str | None = None
    ts: int | None = None


@dataclass(kw_only=True, slots=True)
class BookmakersUpdate(BaseModel):
    """
    A data frame on the ``bookmakers`` channel: per-fixture ``stale_odds``,
    ``suspended`` and ``participants_rotated``, to gate ``odds`` on.
    """
    type: Literal["UPDATE"]
    channel: Literal["bookmakers"]
    payload: BookmakersPayload
    entry_id: str | None = None
    ts: int | None = None


@dataclass(kw_only=True, slots=True)
class ChannelUpdate(BaseModel):
    """
    A data frame on a channel this SDK has no model for; ``payload`` as sent.
    """
    type: Literal["UPDATE"]
    channel: str
    payload: dict[str, Any] = field(default_factory=dict)
    entry_id: str | None = None
    ts: int | None = None


@dataclass(kw_only=True, slots=True)
class FixtureBook(BaseModel):
    """
    One fixture's odds and bookmaker state, as a ``StreamListener`` has built them
    from ``OddsUpdate`` and ``BookmakersUpdate`` messages and REST snapshots.

    Each is a copy, safe to read on any thread while the listener moves on.

    :param odds: Bookmaker slug -> ``oddsId`` -> latest record.
    :param bookmakers: Bookmaker slug -> state, from the ``bookmakers`` channel or a
        snapshot.
    :param changed: The ``oddsId`` keys this update added, changed or dropped.
    """
    fixture_id: str
    odds: dict[str, dict[str, OddsRecord]] = field(default_factory=dict)
    bookmakers: dict[str, BookmakerFixtureMeta] = field(default_factory=dict)
    changed: list[str] = field(default_factory=list)

    def get(self, bookmaker: str, outcome_id: int, player_id: int = 0) -> OddsRecord | None:
        """
        A bookmaker's record for one outcome.

        :param bookmaker: Bookmaker slug.
        :param outcome_id: Outcome id, from the market dictionary.
        :param player_id: Player, for player markets.
        :returns: The record, or ``None`` if the bookmaker has none. Check its
            ``active`` and ``market_active`` before trading on it.
        """
        odds_id = f"{self.fixture_id}:{bookmaker}:{outcome_id}:{player_id}"
        return self.odds.get(bookmaker, {}).get(odds_id)

    def is_tradeable(self, bookmaker: str) -> bool:
        """
        Whether a bookmaker's odds on this fixture can be trusted: it has odds, and
        they are neither stale nor suspended.

        Needs the bookmaker's state, from a snapshot or the ``bookmakers`` channel;
        ``False`` without it. Only the channel keeps it current.

        :param bookmaker: Bookmaker slug.
        """
        meta = self.bookmakers.get(bookmaker)
        return meta is not None and meta.has_odds and not meta.stale_odds and not meta.suspended


Control = LoginOk | ErrorMessage | ResumeComplete | SnapshotRequired | Reconnect | DictFrame
Update = OddsUpdate | BookmakersUpdate | ChannelUpdate


CONTROL_TYPES: dict[str, type[Control]] = {
    "login_ok": LoginOk,
    "error": ErrorMessage,
    "login_error": ErrorMessage,
    "resume_complete": ResumeComplete,
    "snapshot_required": SnapshotRequired,
    "reconnect": Reconnect,
    "dict": DictFrame,
}

CHANNEL_TYPES: dict[str, type[Update]] = {
    ODDS_CHANNEL: OddsUpdate,
    BOOKMAKERS_CHANNEL: BookmakersUpdate,
}
