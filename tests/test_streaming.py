import base64
import copy
import json
import time
from datetime import datetime, timezone
from typing import Any

import pytest
import zstandard
from conftest import PINNACLE
from websocket import ABNF

from fiftyfivetech import (
    FiftyFiveTechClient,
    FiftyFiveTechSocketError,
    FiftyFiveTechStreamAuthenticationError,
)
from fiftyfivetech.fixtures import Fixture, FixtureOddsResponse
from fiftyfivetech.streaming import (
    BaseListener,
    BookmakersUpdate,
    ChannelUpdate,
    DictFrame,
    FixtureBook,
    LoginOk,
    OddsUpdate,
    Reconnect,
    SnapshotRequired,
    StreamListener,
    parse,
)

# The examples from the odds and bookmakers channel docs.
FIXTURE_ID = "id1100013270505136"
UPDATE = {
    "channel": "odds", "type": "UPDATE",
    "payload": {"fixtureId": FIXTURE_ID, "odds": {"pinnacle": {
        f"{FIXTURE_ID}:pinnacle:111:0": {
            "bookmaker": "pinnacle", "outcomeId": 111, "playerId": 0, "active": True,
            "price": 1.155, "marketActive": True, "mainLine": True,
            "bookmakerMarketId": "line/4/487/1628488896/3565645414/0/moneyline",
            "bookmakerOutcomeId": "home", "bookmakerChangedAt": 1776717657043,
            "limit": 19354, "priceAmerican": -645, "priceFractional": "11/71",
            "marketId": 111, "changedAt": 1776717657402}}}},
    "ts": 1776717657500, "entryId": "1776717657500-84805",
}
PREDICTION_UPDATE = {
    "channel": "odds", "type": "UPDATE",
    "payload": {"fixtureId": "id1100064864029581", "odds": {"polymarket": {
        "id1100064864029581:polymarket:112:0": {
            "bookmaker": "polymarket", "outcomeId": 112, "playerId": 0, "active": True,
            "price": 6.369, "priceAmerican": 537, "limit": 4.71,
            "bookmakerMarketId": "1011497", "bookmakerOutcomeId": "4937...",
            "meta": {"back": [{"price": 6.369, "size": 30.0}],
                     "lay": [{"price": 100.0, "size": 15.0}]},
            "marketActive": True, "changedAt": 1766939876376}}}},
    "ts": 1766939876700, "entryId": "1766939876700-653",
}
BOOKMAKERS = {
    "channel": "bookmakers", "type": "UPDATE",
    "payload": {"fixtureId": "id1000070367118324", "bookmakers": {"draftkings": {
        "bookmaker": "draftkings", "bookmakerFixtureId": "33999242",
        "fixturePath": "https://sportsbook.draftkings.com/event/33999242",
        "hasOdds": True, "staleOdds": False, "staleOddsResponseCode": None,
        "suspended": False, "participantsRotated": False, "meta": None,
        "updatedAt": "2026-04-21T00:51:48.283641+00:00"}}},
    "ts": 1776729708000, "entryId": "1776729708000-1",
}
LOGIN_OK = {
    "type": "login_ok", "receiveType": "zstd", "access": {"live": True, "pregame": True},
    "channels": ["odds"],
    "resume": {"serverEpoch": "e1", "resumeWindowMs": 60000, "replayChannels": ["odds"],
               "serverEntryIds": {"odds": "1776717657400-1"}},
}


class FakeSocket:
    """
    Stands in for a ``websocket.WebSocket``: replays frames, records what is sent.
    """

    def __init__(self, frames: list[Any]) -> None:
        self.frames = list(frames)
        self.sent: list[dict[str, Any]] = []
        self.connected = True

    def send(self, text: str) -> None:
        self.sent.append(json.loads(text))

    def recv_data(self) -> tuple[int, Any]:
        frame = self.frames.pop(0)
        if isinstance(frame, tuple):
            return frame
        if isinstance(frame, bytes):
            return ABNF.OPCODE_BINARY, frame
        return ABNF.OPCODE_TEXT, frame.encode()

    def settimeout(self, _timeout: float | None) -> None:
        pass

    def close(self) -> None:
        self.connected = False


class Recorder(BaseListener):
    def __init__(self) -> None:
        self.frames: list[Any] = []

    def on_data(self, data: Any) -> bool | None:
        self.frames.append(data)
        return None


def make_stream(frames: list[Any], api_key: str = "key", **kwargs: Any):
    listener = Recorder()
    stream = FiftyFiveTechClient(api_key).create_stream(listener, **kwargs)
    socket = FakeSocket(frames)
    stream._ws = socket  # type: ignore[assignment]
    return stream, socket, listener


def test_parse():
    """
    Data frames decode by channel; control frames by type; unknown types stay dicts.
    """
    update = parse(UPDATE)
    assert isinstance(update, OddsUpdate) and update.ts == 1776717657500
    record = update.payload.odds["pinnacle"][f"{FIXTURE_ID}:pinnacle:111:0"]
    assert (record.outcome_id, record.price, record.limit) == (111, 1.155, 19354.0)

    exchange = parse(PREDICTION_UPDATE)
    assert isinstance(exchange, OddsUpdate)
    quote = next(iter(exchange.payload.odds["polymarket"].values()))
    assert quote.market_id is None and quote.meta and quote.meta["back"][0]["size"] == 30.0

    books = parse(BOOKMAKERS)
    assert isinstance(books, BookmakersUpdate)
    assert books.payload.bookmakers["draftkings"].stale_odds is False

    other = parse({"type": "UPDATE", "channel": "scores", "payload": {"x": 1}})
    assert isinstance(other, ChannelUpdate) and other.payload == {"x": 1}
    login = parse(LOGIN_OK)
    assert isinstance(login, LoginOk) and login.resume and login.resume.resume_window_ms == 60000
    snapshot = parse({"type": "snapshot_required", "reason": "resume_window_exceeded",
                      "channels": ["scores"], "serverEpoch": "e1", "resumeWindowMs": 60000,
                      "serverEntryIds": {"scores": "1766418738000-220"}})
    assert isinstance(snapshot, SnapshotRequired) and snapshot.channels == ["scores"]
    assert isinstance(parse({"type": "reconnect", "reason": "server_upgrade"}), Reconnect)
    assert parse({"type": "new_thing"}) == {"type": "new_thing"}


def test_stream_listener_queues_updates():
    """
    Odds become books; actionable control frames are queued; ones the stream handles are not.
    """
    listener = StreamListener()
    frames = (LOGIN_OK, UPDATE, {"type": "resume_complete"}, {"type": "reconnect"},
              {"type": "dict", "dictId": 1, "data": ""}, "text")
    for frame in frames:
        listener.on_data(frame)
    queued = [listener.output_queue.get_nowait() for _ in range(listener.output_queue.qsize())]
    assert [type(q).__name__ for q in queued] == ["FixtureBook", "Reconnect", "str"]


def test_login_and_zstd_frames():
    """
    Login sends the filters, and zstd frames decode and advance the cursor.
    """
    compressed = zstandard.ZstdCompressor().compress(json.dumps(UPDATE).encode())
    stream, socket, listener = make_stream(
        [json.dumps(LOGIN_OK), compressed], bookmakers=["pinnacle"], sport_ids=[10],
        tournament_ids=[17], client_name="test")
    stream.login()
    assert socket.sent == [{
        "type": "login", "apiKey": "key", "channels": ["odds"], "receiveType": "zstd",
        "bookmakers": ["pinnacle"], "sportIds": [10], "tournamentIds": [17],
        "clientName": "test"}]
    assert not stream.resuming and stream.server_epoch == "e1"
    assert stream.receive_type_negotiated == "zstd"

    stream.listener.on_data(stream._decode(stream._recv(socket)))  # type: ignore[arg-type]
    assert listener.frames[-1] == UPDATE
    assert stream.last_seen_ids == {"odds": "1776717657500-84805"}


def test_zstd_dict_frames():
    """
    Dictionaries sent after login decode the frames that name them.
    """
    samples = [json.dumps({**UPDATE, "entryId": f"1-{i}", "ts": i}).encode() for i in range(400)]
    trained = zstandard.train_dictionary(4096, samples)
    compressed = zstandard.ZstdCompressor(dict_data=trained).compress(json.dumps(UPDATE).encode())
    dict_frame = {"type": "dict", "channel": "odds", "dictVersion": "odds-v1",
                  "dictId": trained.dict_id(), "encoding": "base64",
                  "data": base64.b64encode(trained.as_bytes()).decode()}
    stream, socket, _ = make_stream(
        [json.dumps({**LOGIN_OK, "receiveType": "zstd-dict"}), json.dumps(dict_frame),
         compressed], receive_type="zstd-dict")
    stream.login()
    assert isinstance(parse(stream._decode(stream._recv(socket))), DictFrame)  # type: ignore[arg-type]
    assert stream._decode(stream._recv(socket)) == UPDATE  # type: ignore[arg-type]


def test_login_resumes_with_cursors():
    """
    A later login offers the cursors; ``snapshot_required`` drops them.
    """
    stream, socket, _ = make_stream([json.dumps(LOGIN_OK), json.dumps(UPDATE)])
    stream.login()
    stream._decode(stream._recv(socket))  # type: ignore[arg-type]

    socket.frames = [json.dumps(LOGIN_OK)]
    stream.login()
    assert stream.resuming
    assert socket.sent[-1]["serverEpoch"] == "e1"
    assert socket.sent[-1]["lastSeenId"] == {"odds": "1776717657500-84805"}

    stream._decode(json.dumps({"type": "snapshot_required", "channels": ["odds"],
                               "reason": "server_restarted", "serverEpoch": "e2"}))
    assert stream.last_seen_ids == {} and stream.server_epoch == "e2"
    assert "lastSeenId" not in stream.login_frame()


def test_login_refused():
    """
    A ``login_error`` raises, carrying the reply.
    """
    reply = {"type": "error", "code": "login_failed", "message": "invalid apiKey"}
    stream, _, _ = make_stream([json.dumps(reply)])
    with pytest.raises(FiftyFiveTechStreamAuthenticationError) as info:
        stream.login()
    assert info.value.reply == reply


def test_close_code_surfaces():
    """
    A gateway close carries its code and reason onto the error.
    """
    close = (ABNF.OPCODE_CLOSE, (4003).to_bytes(2, "big") + b"too_many_connections")
    stream, socket, _ = make_stream([json.dumps(LOGIN_OK), close])
    stream.login()
    stream._running.set()
    with pytest.raises(FiftyFiveTechSocketError) as info:
        stream._recv(socket)  # type: ignore[arg-type]
    assert info.value.close_code == 4003
    assert info.value.close_reason == "too_many_connections"


ODDS_ID = f"{FIXTURE_ID}:pinnacle:111:0"
RECORD = UPDATE["payload"]["odds"]["pinnacle"][ODDS_ID]


def odds_update(**record: Any) -> dict[str, Any]:
    """
    The odds example with its record changed.
    """
    update = copy.deepcopy(UPDATE)
    update["payload"]["odds"]["pinnacle"][ODDS_ID].update(record)
    return update


def bookmakers_update(**state: Any) -> dict[str, Any]:
    """
    The bookmakers example, for Pinnacle on the odds example's fixture.
    """
    update = copy.deepcopy(BOOKMAKERS)
    meta = update["payload"]["bookmakers"].pop("draftkings")
    update["payload"] = {"fixtureId": FIXTURE_ID,
                         "bookmakers": {"pinnacle": {**meta, "bookmaker": "pinnacle", **state}}}
    return update


def snapshot(records: dict[str, dict[str, Any]], sent_at: float,
             meta: dict[str, Any] | None = None) -> FixtureOddsResponse:
    """
    A ``get_fixture_odds`` response for the odds example's fixture.
    """
    response = FixtureOddsResponse.from_json({
        "fixtureId": FIXTURE_ID, "status": {"live": False, "statusId": 0},
        "sport": {"sportId": 10, "sportName": "Soccer"},
        "tournament": {"tournamentId": 1, "tournamentName": "League"},
        "startTime": 1776717600,
        "participants": {"participant1Id": 1, "participant1Name": "Home",
                         "participant2Id": 2, "participant2Name": "Away"},
        "odds": {"pinnacle": records} if records else {},
        "bookmakers": {"pinnacle": meta} if meta else {},
    })
    response.sent_at = datetime.fromtimestamp(sent_at, timezone.utc)
    return response


def drain(listener: StreamListener) -> list[Any]:
    return [listener.output_queue.get_nowait() for _ in range(listener.output_queue.qsize())]


def test_book_accumulates_odds():
    """
    Streamed records build up per fixture; an older copy of a record is ignored.
    """
    listener = StreamListener()
    listener.on_data(UPDATE)
    other = {**RECORD, "outcomeId": 112, "price": 5.77}
    second = copy.deepcopy(UPDATE)
    second["payload"]["odds"]["pinnacle"] = {f"{FIXTURE_ID}:pinnacle:112:0": other}
    listener.on_data(second)
    listener.on_data(odds_update(price=9.99, changedAt=RECORD["changedAt"] - 1))

    books = drain(listener)
    assert [b.changed for b in books] == [[ODDS_ID], [f"{FIXTURE_ID}:pinnacle:112:0"], []]
    book = books[-1]
    assert isinstance(book, FixtureBook) and book is listener.fixture_books[FIXTURE_ID]
    assert book.get("pinnacle", 111).price == 1.155  # type: ignore[union-attr]
    assert book.get("pinnacle", 112).price == 5.77  # type: ignore[union-attr]
    assert book.get("pinnacle", 999) is None and book.get("bet365", 111) is None
    assert books[0].get("pinnacle", 112) is None  # earlier books are copies


def test_book_tradeable():
    """
    Bookmaker state gates trading: unknown, stale or suspended is not tradeable.
    """
    listener = StreamListener()
    listener.on_data(UPDATE)
    assert not listener.fixture_books[FIXTURE_ID].is_tradeable("pinnacle")
    listener.on_data(bookmakers_update())
    assert listener.fixture_books[FIXTURE_ID].is_tradeable("pinnacle")
    listener.on_data(bookmakers_update(staleOdds=True, updatedAt="2026-04-21T00:52:00+00:00"))
    assert not listener.fixture_books[FIXTURE_ID].is_tradeable("pinnacle")
    assert listener.fixture_books[FIXTURE_ID].get("pinnacle", 111) is not None


def test_snapshot_merges_by_changed_at():
    """
    A snapshot seeds the book and its bookmaker state; per record the later changed_at wins.
    """
    listener = StreamListener()
    listener.on_data(odds_update(price=2.0, changedAt=RECORD["changedAt"] + 10))
    older = {**RECORD, "price": 1.5}
    extra = {**RECORD, "outcomeId": 112, "price": 5.0}
    meta = bookmakers_update()["payload"]["bookmakers"]["pinnacle"]
    book = listener.apply_snapshot(snapshot(
        {ODDS_ID: older, f"{FIXTURE_ID}:pinnacle:112:0": extra}, time.time(), meta))
    assert book.get("pinnacle", 111).price == 2.0  # type: ignore[union-attr]
    assert book.get("pinnacle", 112).price == 5.0  # type: ignore[union-attr]
    assert book.is_tradeable("pinnacle")
    assert drain(listener)[-1] is book


def test_snapshot_drops_what_the_bookmaker_withdrew():
    """
    A record the snapshot lacks is dropped, unless the stream set it after the request.
    """
    listener = StreamListener()
    listener.on_data(UPDATE)
    before = time.time() - 60
    assert listener.apply_snapshot(snapshot({}, before), ["pinnacle"]).get("pinnacle", 111)

    after = time.time() + 1
    book = listener.apply_snapshot(snapshot({}, after), ["pinnacle"])
    assert book.get("pinnacle", 111) is None and book.changed == [ODDS_ID]
    assert book.odds == {}

    listener.on_data(UPDATE)
    untouched = listener.apply_snapshot(snapshot({}, time.time() + 1))
    assert untouched.get("pinnacle", 111) is not None  # pinnacle not covered


def test_remove_fixture():
    """
    A removed fixture starts again from nothing.
    """
    listener = StreamListener()
    listener.on_data(UPDATE)
    listener.remove_fixture(FIXTURE_ID)
    assert FIXTURE_ID not in listener.fixture_books
    listener.on_data(bookmakers_update())
    assert listener.fixture_books[FIXTURE_ID].odds == {}


def test_live_snapshot_seeds_book(client: FiftyFiveTechClient, pinnacle_fixture: Fixture):
    """
    A real ``get_fixture_odds`` response seeds a book that answers by outcome.
    """
    listener = StreamListener()
    response = client.get_fixture_odds(pinnacle_fixture.fixture_id, bookmakers=[PINNACLE])
    book = listener.apply_snapshot(response, [PINNACLE])
    records = response.odds.get(PINNACLE, {})
    for record in records.values():
        assert book.get(PINNACLE, record.outcome_id, record.player_id) == record
    assert book.is_tradeable(PINNACLE) == bool(
        (meta := response.bookmakers.get(PINNACLE))
        and meta.has_odds and not meta.stale_odds and not meta.suspended)
