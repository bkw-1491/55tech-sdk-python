from collections.abc import Sequence

from fiftyfivetech.endpoints import Endpoints
from fiftyfivetech.exceptions import FiftyFiveTechInvalidRequestError
from fiftyfivetech.markets.resources import MarketsResponse


class MarketsEndpoints(Endpoints):
    """
    The market dictionary.
    """

    def get_markets(
        self,
        *,
        sport_id: int | None = None,
        market_ids: Sequence[int] | None = None,
        outcome_ids: Sequence[int] | None = None,
    ) -> MarketsResponse:
        """
        Fetch market definitions: ``GET /markets``. Give exactly one filter.

        :param sport_id: Every market of one sport, e.g. ``10`` for soccer.
        :param market_ids: These markets.
        :param outcome_ids: The markets these outcomes belong to.
        :returns: The matching definitions.
        :raises FiftyFiveTechInvalidRequestError: Unless exactly one filter is given.
        """
        given = [v for v in (sport_id, market_ids, outcome_ids) if v is not None]
        if len(given) != 1:
            raise FiftyFiveTechInvalidRequestError(
                "get_markets needs exactly one of sport_id, market_ids or outcome_ids")
        return self.conn.call(MarketsResponse, "GET", "/markets", wrap="markets", params={
            "sportId": sport_id,
            "marketIds": market_ids,
            "outcomeIds": outcome_ids,
        })
