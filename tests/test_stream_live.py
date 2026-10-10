"""
End-to-end tests against the live gateway. Each holds one connection for a few
seconds; a key may hold 5.
"""

import queue
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from conftest import PINNACLE, SOCCER

from fiftyfivetech import FiftyFiveTechClient, FiftyFiveTechStreamAuthenticationError
from fiftyfivetech.streaming import (
    FiftyFiveTechStream,
    FixtureBook,
    ResumeComplete,
    SnapshotRequired,
    StreamListener,
)

WAIT = 30.0


class RecordingListener(StreamListener):
    """
    A ``StreamListener`` that also keeps the control messages it doesn't queue.
    """

    def __init__(self) -> None:
        super().__init__()
        self.controls: list[Any] = []

    def on_update(self, update: Any) -> None:
        if not isinstance(update, FixtureBook):
            self.controls.append(update)
        super().on_update(update)


def open_stream(client: FiftyFiveTechClient, listener: StreamListener,
                **kwargs: Any) -> FiftyFiveTechStream:
    kwargs = {"channels": ["odds", "bookmakers"], "bookmakers": [PINNACLE],
              "sport_ids": [SOCCER], "client_name": "55tech-sdk-e2e", **kwargs}
    return client.create_stream(listener, **kwargs)


@contextmanager
def running(stream: FiftyFiveTechStream) -> Iterator[threading.Thread]:
    """
    Run ``start`` in a thread, and check ``stop`` ends it.
    """
    errors: list[BaseException] = []

    def target() -> None:
        try:
            stream.start()
        except BaseException as exc:  # surfaced below
            errors.append(exc)

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    try:
        yield thread
    finally:
        stream.stop()
        thread.join(5)
    assert not thread.is_alive(), "stop() did not end start()"
    assert not errors, f"start() raised {errors[0]!r}"


def next_book(listener: StreamListener, fixture_id: str | None = None) -> FixtureBook:
    """
    The next queued book, optionally for one fixture; anything else queued must be decoded.
    """
    deadline = time.monotonic() + WAIT
    while time.monotonic() < deadline:
        try:
            item = listener.output_queue.get(timeout=max(deadline - time.monotonic(), 0.01))
        except queue.Empty:
            break
        assert not isinstance(item, (dict, str)), f"undecoded frame: {item!r:.300}"
        if isinstance(item, FixtureBook) and fixture_id in (None, item.fixture_id):
            return item
    pytest.fail(f"no odds within {WAIT}s")


@pytest.mark.parametrize("receive_type", ["zstd", "zstd-dict", "json"])
def test_receive_types(client: FiftyFiveTechClient, receive_type: str):
    """
    Every encoding logs in and streams odds that decode into books.
    """
    listener = StreamListener()
    stream = open_stream(client, listener, receive_type=receive_type)
    stream.connect()
    assert stream.receive_type_negotiated in (receive_type, "json")  # zstd may be downgraded
    assert stream.server_epoch and not stream.resuming
    with running(stream):
        books = [next_book(listener) for _ in range(5)]
    assert all(b.changed and b.odds.get(PINNACLE) for b in books)
    assert stream.last_seen_ids.get("odds")


def test_resume(client: FiftyFiveTechClient):
    """
    Reconnecting offers the cursor, and the gateway replays the gap then confirms it.
    """
    listener = RecordingListener()
    stream = open_stream(client, listener, channels=["odds"])
    with running(stream):
        next_book(listener)
    time.sleep(1)
    listener.controls.clear()
    stream.connect()
    assert stream.resuming
    with running(stream):
        # The gap is replayed first; resume_complete follows it.
        deadline = time.monotonic() + WAIT
        while (not any(isinstance(c, ResumeComplete) for c in listener.controls)
               and time.monotonic() < deadline):
            next_book(listener)
    assert any(isinstance(c, ResumeComplete) for c in listener.controls)


def test_stale_epoch_requires_snapshot(client: FiftyFiveTechClient):
    """
    Resuming against a gateway that has restarted asks for a snapshot, and drops the cursor.
    """
    listener = RecordingListener()
    stream = open_stream(client, listener, channels=["odds"])
    with running(stream):
        next_book(listener)
    stream.server_epoch = "not-a-real-epoch"
    stream.connect()
    assert stream.resuming
    with running(stream):
        next_book(listener)
    required = [c for c in listener.controls if isinstance(c, SnapshotRequired)]
    assert required and required[0].reason == "server_restarted"
    assert "odds" in required[0].channels


def test_bad_key():
    """
    The gateway refuses an unknown key with ``login_failed``.
    """
    stream = FiftyFiveTechClient("not-a-real-key").create_stream(timeout=10)
    with pytest.raises(FiftyFiveTechStreamAuthenticationError) as info:
        stream.connect()
    assert info.value.reply["error"] == "login_failed"


def test_seed_then_stream_has_no_gaps(client: FiftyFiveTechClient):
    """
    The documented flow end to end: connect, seed a streamed fixture over REST, keep
    streaming, then check the book against a fresh REST fetch.
    """
    listener = StreamListener()
    stream = open_stream(client, listener)
    with running(stream):
        fixture_id = next_book(listener).fixture_id
        response = client.get_fixture_odds(fixture_id, bookmakers=[PINNACLE])
        seeded = listener.apply_snapshot(response, [PINNACLE])
        assert seeded.is_tradeable(PINNACLE) == bool(
            (meta := response.bookmakers.get(PINNACLE))
            and meta.has_odds and not meta.stale_odds and not meta.suspended)
        assert len(seeded.odds.get(PINNACLE, {})) >= len(response.odds.get(PINNACLE, {}))

        time.sleep(5)
        fresh = client.get_fixture_odds(fixture_id, bookmakers=[PINNACLE])
        time.sleep(2)  # let the stream deliver what changed during the fetch
        book = listener.fixture_books[fixture_id]

    # Anything REST has that the book lacks, or has newer, must be newer than the
    # last few seconds' worth of stream; older differences are gaps.
    cutoff_ms = fresh.sent_at.timestamp() * 1000 - 3000
    gaps = []
    for odds_id, record in fresh.odds.get(PINNACLE, {}).items():
        held = book.odds.get(PINNACLE, {}).get(odds_id)
        if held is None or held.changed_at < record.changed_at:
            if record.changed_at < cutoff_ms:
                gaps.append(odds_id)
        elif held.changed_at == record.changed_at:
            assert held.price == record.price, odds_id
    assert not gaps, f"{len(gaps)} records missing or stale in the book, e.g. {gaps[:3]}"
