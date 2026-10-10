from collections.abc import Sequence
from datetime import datetime

from fiftyfivetech.endpoints import Endpoints
from fiftyfivetech.exceptions import FiftyFiveTechInvalidRequestError
from fiftyfivetech.fixtures.resources import (
    FixtureMappingResponse,
    FixtureOddsResponse,
    FixturesResponse,
)
from fiftyfivetech.utils import to_epoch_seconds


class FixturesEndpoints(Endpoints):
    """
    Fixtures, fixture mappings and per-fixture odds.
    """

    def get_fixtures(
        self,
        *,
        fixture_ids: Sequence[str] | None = None,
        sport_id: int | None = None,
        tournament_id: int | None = None,
        start_time_from: datetime | int | None = None,
        start_time_to: datetime | int | None = None,
        bookmakers: Sequence[str] | None = None,
    ) -> FixturesResponse:
        """
        Fetch fixtures: ``GET /fixtures``. Give ``fixture_ids``, or filters.

        :param fixture_ids: These fixtures (the fast path).
        :param sport_id: Fixtures of one sport, e.g. ``10`` for soccer.
        :param tournament_id: Fixtures of one tournament.
        :param start_time_from: Earliest scheduled start; datetime or epoch seconds.
        :param start_time_to: Latest scheduled start; datetime or epoch seconds.
        :param bookmakers: Include each fixture's ``bookmakers`` state for these slugs,
            and keep only fixtures they are mapped to.
        :returns: The matching fixtures.
        """
        return self.conn.call(FixturesResponse, "GET", "/fixtures", wrap="fixtures", params={
            "fixtureIds": fixture_ids,
            "sportId": sport_id,
            "tournamentId": tournament_id,
            "startTimeFrom": to_epoch_seconds(start_time_from),
            "startTimeTo": to_epoch_seconds(start_time_to),
            "bookmakers": bookmakers,
        })

    def get_fixtures_live(
        self,
        *,
        sport_id: int | None = None,
        tournament_id: int | None = None,
        bookmakers: Sequence[str] | None = None,
    ) -> FixturesResponse:
        """
        Fetch fixtures in play: ``GET /fixtures/live``.

        :param sport_id: Fixtures of one sport.
        :param tournament_id: Fixtures of one tournament.
        :param bookmakers: Bookmaker slugs to include state for, and filter by.
        :returns: The live fixtures.
        """
        return self.conn.call(
            FixturesResponse, "GET", "/fixtures/live", wrap="fixtures", params={
                "sportId": sport_id,
                "tournamentId": tournament_id,
                "bookmakers": bookmakers,
            })

    def get_fixtures_today(
        self,
        *,
        sport_id: int | None = None,
        tournament_id: int | None = None,
        bookmakers: Sequence[str] | None = None,
    ) -> FixturesResponse:
        """
        Fetch today's fixtures: ``GET /fixtures/today``.

        :param sport_id: Fixtures of one sport.
        :param tournament_id: Fixtures of one tournament.
        :param bookmakers: Bookmaker slugs to include state for, and filter by.
        :returns: Today's fixtures.
        """
        return self.conn.call(
            FixturesResponse, "GET", "/fixtures/today", wrap="fixtures", params={
                "sportId": sport_id,
                "tournamentId": tournament_id,
                "bookmakers": bookmakers,
            })

    def get_fixture_mapping(
        self,
        *,
        bookmaker: str,
        fixture_ids: Sequence[str] | None = None,
        bookmaker_fixture_ids: Sequence[str] | None = None,
    ) -> FixtureMappingResponse:
        """
        Pair 55Tech fixtures with a bookmaker's: ``GET /fixtures/mapping``. Give
        exactly one of ``fixture_ids`` or ``bookmaker_fixture_ids``.

        Send long lists with ``utils.batch``.

        :param bookmaker: Bookmaker slug, e.g. ``"pinnacle"``. The spec makes it
            optional, but the API rejects a request without it.
        :param fixture_ids: 55Tech fixture ids to find the bookmaker's ids for.
        :param bookmaker_fixture_ids: The bookmaker's ids to find 55Tech fixtures for.
        :returns: One row per id; the other side of an unpaired id is ``None``.
        :raises FiftyFiveTechInvalidRequestError: Unless exactly one non-empty id list is given.
        """
        given = [ids for ids in (fixture_ids, bookmaker_fixture_ids) if ids]
        if len(given) != 1:
            raise FiftyFiveTechInvalidRequestError(
                "get_fixture_mapping needs one of fixture_ids or bookmaker_fixture_ids")
        return self.conn.call(
            FixtureMappingResponse, "GET", "/fixtures/mapping", wrap="mappings", params={
                "bookmaker": bookmaker,
                "fixtureIds": fixture_ids,
                "bookmakerFixtureIds": (
                    [str(i) for i in bookmaker_fixture_ids] if bookmaker_fixture_ids else None),
            })

    def get_fixture_odds(
        self,
        fixture_id: str,
        *,
        bookmakers: Sequence[str] | None = None,
        since: int | None = None,
        market_active: bool | None = None,
        main_line: bool | None = None,
    ) -> FixtureOddsResponse:
        """
        Fetch one fixture's current odds: ``GET /fixtures/odds``.

        One fixture per call: a comma-joined id is treated as a single unknown one.
        Every line is returned, alternates included, unless ``main_line`` says otherwise.
        Merge into streamed state per ``oddsId``, keeping the later ``changed_at``.

        :param fixture_id: 55Tech fixture id.
        :param bookmakers: Bookmaker slugs to include; all the key allows if omitted.
        :param since: Only records with ``changedAt`` at or after this, epoch milliseconds.
        :param market_active: Only records whose ``marketActive`` is this.
        :param main_line: Only records whose ``mainLine`` is this.
        :returns: The fixture with each bookmaker's records.
        :raises FiftyFiveTechInvalidRequestError: If ``fixture_id`` is empty.
        """
        if not fixture_id:
            raise FiftyFiveTechInvalidRequestError("get_fixture_odds needs a fixture_id")
        return self.conn.call(FixtureOddsResponse, "GET", "/fixtures/odds", params={
            "fixtureId": fixture_id,
            "bookmakers": bookmakers,
            "since": since,
            "marketActive": market_active,
            "mainLine": main_line,
        })
