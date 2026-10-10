import queue
import threading
import time
from collections.abc import Sequence
from typing import Any

from fiftyfivetech.exceptions import FiftyFiveTechDecodeError
from fiftyfivetech.fixtures.resources import (
    BookmakerFixtureMeta,
    FixtureOddsResponse,
    OddsRecord,
)
from fiftyfivetech.streaming.resources import (
    CHANNEL_TYPES,
    CONTROL_TYPES,
    BookmakersUpdate,
    ChannelUpdate,
    Control,
    DictFrame,
    FixtureBook,
    LoginOk,
    OddsUpdate,
    ResumeComplete,
    Update,
)
from fiftyfivetech.utils import decode


def parse(data: Any) -> Update | Control | dict[str, Any]:
    """
    Decode one stream message into its model.

    :param data: One decoded JSON message.
    :returns: The model for a data frame's ``channel`` (``ChannelUpdate`` for one
        without a model) or a control frame's ``type``, or the dict itself for a type
        this SDK doesn't know yet.
    :raises FiftyFiveTechDecodeError: If the message isn't an object, or doesn't fit its model.
    """
    if not isinstance(data, dict):
        raise FiftyFiveTechDecodeError(f"stream message is not an object: {data!r:.100}")
    mtype = data.get("type", "")
    if mtype == "UPDATE":
        return decode(CHANNEL_TYPES.get(data.get("channel", ""), ChannelUpdate), data)
    model = CONTROL_TYPES.get(mtype)
    if model is not None:
        return decode(model, data)
    return data


class BaseListener:
    """
    Receives every frame an ``FiftyFiveTechStream`` reads, decoded from JSON (and
    decompressed first, on a ``zstd`` stream); does nothing with it. Subclass and
    override ``on_data`` to handle frames yourself.
    """

    def on_data(self, data: Any) -> bool | None:
        """
        Handle one frame. Runs on the thread that called ``FiftyFiveTechStream.start``.

        :param data: The decoded frame, usually a dict; the text, if it wasn't JSON.
        :returns: ``False`` to stop the stream.
        """
        return None


class StreamListener(BaseListener):
    """
    Decodes frames into models and puts them on ``output_queue``.

    ``OddsUpdate`` and ``BookmakersUpdate`` messages are applied to a per-fixture
    book, and the updated ``FixtureBook`` is queued instead. Seed a book, and re-seed
    it on ``SnapshotRequired``, with ``apply_snapshot``. Every other message is
    queued as decoded, control frames included (``SnapshotRequired`` and
    ``Reconnect`` call for action), except ``LoginOk``, ``ResumeComplete`` and
    ``DictFrame``, which the stream handles. A message of an unknown type, or one
    that doesn't fit its model, is queued as its dict, and a frame that isn't JSON
    as its text, so nothing is dropped silently.

    Of two copies of a record, the one with the later ``changed_at`` is kept.

    :param output_queue: Where updates go; a new ``queue.Queue`` if omitted.
    """

    def __init__(self, output_queue: queue.Queue[Any] | None = None) -> None:
        """
        Create the listener; see the class docstring for parameters.
        """
        self.output_queue: queue.Queue[Any] = (
            queue.Queue() if output_queue is None else output_queue)
        self.fixture_books: dict[str, FixtureBook] = {}
        # Per fixture: the records and bookmaker states, and when (epoch seconds)
        # the stream last set each, which decides what a snapshot may drop.
        self._odds: dict[str, dict[str, dict[str, OddsRecord]]] = {}
        self._meta: dict[str, dict[str, BookmakerFixtureMeta]] = {}
        self._odds_seen: dict[str, dict[str, float]] = {}
        self._meta_seen: dict[str, dict[str, float]] = {}
        # ``on_data`` runs on the stream's thread, ``apply_snapshot`` on the caller's.
        self._lock = threading.Lock()

    def on_data(self, data: Any) -> bool | None:
        """
        Decode a frame and queue what it carries.

        :param data: The decoded frame.
        :returns: ``None``.
        """
        if isinstance(data, str):
            self.output_queue.put(data)
            return None
        for item in data if isinstance(data, list) else [data]:
            try:
                update = parse(item)
            except FiftyFiveTechDecodeError:
                update = item
            self.on_update(update)
        return None

    def on_update(self, update: Any) -> None:
        """
        Queue one decoded message; see the class docstring for what is queued.

        :param update: The decoded message, or the raw one if it couldn't be decoded.
        """
        if isinstance(update, OddsUpdate):
            self.output_queue.put(self.apply_odds(update))
        elif isinstance(update, BookmakersUpdate):
            self.output_queue.put(self.apply_bookmakers(update))
        elif not isinstance(update, (LoginOk, ResumeComplete, DictFrame)):
            self.output_queue.put(update)

    def apply_odds(self, update: OddsUpdate) -> FixtureBook:
        """
        Apply streamed odds to their fixture's book.

        :param update: The update.
        :returns: The fixture's book after it.
        """
        fixture_id = update.payload.fixture_id
        now = time.time()
        with self._lock:
            odds = self._odds.setdefault(fixture_id, {})
            seen = self._odds_seen.setdefault(fixture_id, {})
            changed = []
            for bookmaker, records in update.payload.odds.items():
                book = odds.setdefault(bookmaker, {})
                for odds_id, record in records.items():
                    current = book.get(odds_id)
                    if current is None or record.changed_at >= current.changed_at:
                        book[odds_id] = record
                        seen[odds_id] = now
                        changed.append(odds_id)
            return self._build(fixture_id, changed)

    def apply_bookmakers(self, update: BookmakersUpdate) -> FixtureBook:
        """
        Apply streamed bookmaker state to its fixture's book.

        :param update: The update.
        :returns: The fixture's book after it.
        """
        fixture_id = update.payload.fixture_id
        now = time.time()
        with self._lock:
            meta = self._meta.setdefault(fixture_id, {})
            seen = self._meta_seen.setdefault(fixture_id, {})
            for bookmaker, state in update.payload.bookmakers.items():
                current = meta.get(bookmaker)
                if current is None or state.updated_at >= current.updated_at:
                    meta[bookmaker] = state
                    seen[bookmaker] = now
            return self._build(fixture_id, [])

    def apply_snapshot(
        self,
        snapshot: FixtureOddsResponse,
        bookmakers: Sequence[str] | None = None,
    ) -> FixtureBook:
        """
        Merge a ``get_fixture_odds`` response into its fixture's book, and queue the book.

        Call it to seed a book (connect the stream first, so nothing is missed while
        the request is in flight) and again on ``SnapshotRequired``. Safe from any
        thread.

        The snapshot is the bookmakers' whole book as of when it was requested. For
        each bookmaker it covers, the later ``changed_at`` wins per record, and a
        record the snapshot lacks is dropped, as the bookmaker no longer offers it,
        unless the stream set it after the request went out. Bookmaker state is
        merged the same way.

        :param snapshot: A response from ``get_fixture_odds``.
        :param bookmakers: The bookmakers the request asked for, so one offering
            nothing is cleared too; those in the response if omitted.
        :returns: The fixture's book after the merge.
        """
        fixture_id = snapshot.fixture_id
        requested_at = snapshot.sent_at.timestamp()
        covered = set(bookmakers) if bookmakers is not None else (
            set(snapshot.odds) | set(snapshot.bookmakers))
        with self._lock:
            odds = self._odds.setdefault(fixture_id, {})
            odds_seen = self._odds_seen.setdefault(fixture_id, {})
            meta = self._meta.setdefault(fixture_id, {})
            meta_seen = self._meta_seen.setdefault(fixture_id, {})
            changed = []
            for bookmaker in covered:
                fresh = snapshot.odds.get(bookmaker, {})
                book = odds.setdefault(bookmaker, {})
                for odds_id in [i for i in book if i not in fresh]:
                    if odds_seen.get(odds_id, 0.0) <= requested_at:
                        del book[odds_id]
                        odds_seen.pop(odds_id, None)
                        changed.append(odds_id)
                for odds_id, record in fresh.items():
                    current = book.get(odds_id)
                    if current is None or record.changed_at > current.changed_at:
                        book[odds_id] = record
                        changed.append(odds_id)
                if not book:
                    del odds[bookmaker]

                state, current_state = snapshot.bookmakers.get(bookmaker), meta.get(bookmaker)
                if state is None:
                    if current_state and meta_seen.get(bookmaker, 0.0) <= requested_at:
                        del meta[bookmaker]
                        meta_seen.pop(bookmaker, None)
                elif current_state is None or state.updated_at >= current_state.updated_at:
                    meta[bookmaker] = state
            book_after = self._build(fixture_id, changed)
        self.output_queue.put(book_after)
        return book_after

    def remove_fixture(self, fixture_id: str) -> None:
        """
        Forget a fixture's book, e.g. once it has finished.

        :param fixture_id: The fixture.
        """
        with self._lock:
            for store in (self.fixture_books, self._odds, self._meta,
                          self._odds_seen, self._meta_seen):
                store.pop(fixture_id, None)

    def _build(self, fixture_id: str, changed: list[str]) -> FixtureBook:
        """
        Copy a fixture's state into a new ``FixtureBook``; call holding the lock.
        """
        book = FixtureBook(
            fixture_id=fixture_id,
            odds={bk: dict(records) for bk, records in self._odds.get(fixture_id, {}).items()},
            bookmakers=dict(self._meta.get(fixture_id, {})),
            changed=changed,
        )
        self.fixture_books[fixture_id] = book
        return book
