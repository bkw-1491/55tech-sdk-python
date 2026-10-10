import pytest
from conftest import SOCCER, check

from fiftyfivetech import (
    FiftyFiveTechClient,
    FiftyFiveTechConnection,
    FiftyFiveTechInvalidRequestError,
)
from fiftyfivetech.markets import MarketsEndpoints


def test_markets(client: FiftyFiveTechClient):
    """
    The soccer dictionary lists full-time 1X2 with its three outcomes, and filters by id.
    """
    markets = check(client.get_markets(sport_id=SOCCER)).markets
    assert markets and all(m.sport_id == SOCCER for m in markets)
    one_x_two = next(m for m in markets if m.market_type == "1x2" and m.period == "fulltime")
    assert [o.outcome_name for o in one_x_two.outcomes] == ["1", "X", "2"]

    by_id = check(client.get_markets(market_ids=[one_x_two.market_id])).markets
    assert [m.market_id for m in by_id] == [one_x_two.market_id]
    by_outcome = check(client.get_markets(outcome_ids=[one_x_two.outcomes[0].outcome_id]))
    assert [m.market_id for m in by_outcome.markets] == [one_x_two.market_id]


@pytest.mark.parametrize("kwargs", [{}, {"sport_id": 10, "market_ids": [101]}])
def test_markets_need_one_filter(kwargs):
    """
    Anything but exactly one filter is refused before sending.
    """
    with pytest.raises(FiftyFiveTechInvalidRequestError):
        MarketsEndpoints(FiftyFiveTechConnection("key")).get_markets(**kwargs)
