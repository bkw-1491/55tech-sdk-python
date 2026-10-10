import requests

from fiftyfivetech.bookmakers import BookmakersEndpoints
from fiftyfivetech.connection import DEFAULT_BASE_URL, DEFAULT_STREAM_URL, FiftyFiveTechConnection
from fiftyfivetech.fixtures import FixturesEndpoints
from fiftyfivetech.markets import MarketsEndpoints
from fiftyfivetech.streaming import StreamingEndpoints


class FiftyFiveTechClient(
    BookmakersEndpoints,
    MarketsEndpoints,
    FixturesEndpoints,
    StreamingEndpoints,
):
    """
    Top-level client for 55Tech.
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        stream_url: str = DEFAULT_STREAM_URL,
        session: requests.Session | None = None,
        timeout: float | None = 30.0,
    ) -> None:
        """
        Create the client and its connection; see ``FiftyFiveTechConnection`` for parameters.
        """
        super().__init__(FiftyFiveTechConnection(
            api_key,
            base_url=base_url,
            stream_url=stream_url,
            session=session,
            timeout=timeout,
        ))
