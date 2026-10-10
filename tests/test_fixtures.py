from datetime import timedelta

import pytest
from conftest import PINNACLE, SOCCER, check

from fiftyfivetech import (
    FiftyFiveTechAPIError,
    FiftyFiveTechClient,
    FiftyFiveTechInvalidRequestError,
    FiftyFiveTechTransportError,
)
from fiftyfivetech.fixtures import Fixture
from fiftyfivetech.utils import batch


def test_fixtures(fixtures: list[Fixture]):
    """
    Fixtures list both participants and a start time.
    """
    assert fixtures
    fixture = fixtures[0]
    assert fixture.fixture_id and fixture.participants.participant1_id
    assert fixture.starts_at.tzinfo is not None


def test_fixture_filters(client: FiftyFiveTechClient, pinnacle_fixture: Fixture):
    """
    Fixtures filter by id, tournament and start time, and carry bookmaker state on request.
    """
    by_id = check(client.get_fixtures(fixture_ids=[pinnacle_fixture.fixture_id],
                                      bookmakers=[PINNACLE])).fixtures
    assert [f.fixture_id for f in by_id] == [pinnacle_fixture.fixture_id]
    meta = by_id[0].bookmakers.get(PINNACLE)
    if meta is not None:
        assert meta.bookmaker == PINNACLE and isinstance(meta.stale_odds, bool)

    starts = pinnacle_fixture.starts_at
    window = check(client.get_fixtures(
        tournament_id=pinnacle_fixture.tournament.tournament_id,
        start_time_from=starts - timedelta(minutes=1),
        start_time_to=starts + timedelta(minutes=1))).fixtures
    assert pinnacle_fixture.fixture_id in {f.fixture_id for f in window}


def test_fixtures_live_and_today(client: FiftyFiveTechClient):
    """
    The live and today lists decode.
    """
    live = check(client.get_fixtures_live(sport_id=SOCCER)).fixtures
    assert all(f.status.live for f in live)
    check(client.get_fixtures_today(sport_id=SOCCER))


def test_fixture_mapping(client: FiftyFiveTechClient, pinnacle_fixture: Fixture):
    """
    Mapping resolves both ways; unknown ids come back unpaired.
    """
    pin_id = str(pinnacle_fixture.external_providers["pinnacleId"])
    rows = check(client.get_fixture_mapping(
        bookmaker=PINNACLE, bookmaker_fixture_ids=[pin_id, "1"])).mappings
    by_id = {row.bookmaker_fixture_id: row for row in rows}
    assert by_id[pin_id].fixture_id == pinnacle_fixture.fixture_id
    assert by_id[pin_id].participants_rotated is not None
    assert by_id["1"].fixture_id is None

    back = check(client.get_fixture_mapping(
        bookmaker=PINNACLE, fixture_ids=[pinnacle_fixture.fixture_id, "id0"])).mappings
    by_fixture = {row.fixture_id: row for row in back}
    assert by_fixture[pinnacle_fixture.fixture_id].bookmaker_fixture_id == pin_id
    assert by_fixture["id0"].bookmaker_fixture_id is None

    pages = list(batch(client.get_fixture_mapping, [pin_id, "1", "2"],
                       "bookmaker_fixture_ids", size=2, bookmaker=PINNACLE))
    assert [len(p.mappings) for p in pages] == [2, 1]


def test_fixture_odds(client: FiftyFiveTechClient, pinnacle_fixture: Fixture):
    """
    A fixture's odds come keyed by bookmaker, then oddsId; ``since`` filters by changedAt.
    """
    odds = check(client.get_fixture_odds(pinnacle_fixture.fixture_id, bookmakers=[PINNACLE]))
    assert odds.fixture_id == pinnacle_fixture.fixture_id
    assert set(odds.odds) <= {PINNACLE}
    records = odds.odds.get(PINNACLE, {})
    for odds_id, record in records.items():
        assert odds_id == f"{odds.fixture_id}:{PINNACLE}:{record.outcome_id}:{record.player_id}"
    assert odds.latency_ms > 0

    if records:
        newest = max(r.changed_at for r in records.values())
        later = check(client.get_fixture_odds(
            pinnacle_fixture.fixture_id, bookmakers=[PINNACLE], since=newest))
        assert all(r.changed_at >= newest for r in later.odds.get(PINNACLE, {}).values())
        main = check(client.get_fixture_odds(
            pinnacle_fixture.fixture_id, bookmakers=[PINNACLE], main_line=True))
        assert all(r.main_line for r in main.odds.get(PINNACLE, {}).values())


def test_mapping_needs_one_id_list():
    """
    Mapping takes exactly one non-empty id list, refused before sending otherwise.
    """
    client = FiftyFiveTechClient("key")
    with pytest.raises(FiftyFiveTechInvalidRequestError):
        client.get_fixture_mapping(bookmaker=PINNACLE)
    with pytest.raises(FiftyFiveTechInvalidRequestError):
        client.get_fixture_mapping(bookmaker=PINNACLE, fixture_ids=["a"],
                                   bookmaker_fixture_ids=["b"])


def test_api_error(client: FiftyFiveTechClient):
    """
    A bad key is a 401 with the body's code, and the message doesn't echo the key.
    """
    bad = FiftyFiveTechClient("not-a-real-key", base_url=client.conn.base_url)
    with pytest.raises(FiftyFiveTechAPIError) as info:
        bad.get_markets(sport_id=SOCCER)
    assert info.value.status_code == 401
    assert info.value.code == "invalid_api_key"
    assert "not-a-real-key" not in str(info.value)
    assert "not-a-real-key" not in info.value.response.url


def test_transport_error_keeps_key_out():
    """
    The key travels in a header, so a failed request's message doesn't carry it.
    """
    client = FiftyFiveTechClient("secret-key-123", base_url="http://127.0.0.1:9", timeout=2)
    with pytest.raises(FiftyFiveTechTransportError) as info:
        client.get_markets(sport_id=SOCCER)
    assert "secret-key-123" not in str(info.value)
