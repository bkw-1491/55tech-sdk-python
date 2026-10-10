from collections.abc import Sequence

from fiftyfivetech.bookmakers.resources import BookmakersResponse
from fiftyfivetech.endpoints import Endpoints


class BookmakersEndpoints(Endpoints):
    """
    The bookmaker catalogue.
    """

    def get_bookmakers(
        self,
        *,
        bookmakers: Sequence[str] | None = None,
        player_props: bool | None = None,
    ) -> BookmakersResponse:
        """
        Fetch the bookmakers the key can see, with their freshness bounds:
        ``GET /bookmakers``.

        :param bookmakers: Slugs to fetch; all if omitted.
        :param player_props: Only bookmakers offering player markets, if ``True``.
        :returns: The bookmakers.
        """
        return self.conn.call(
            BookmakersResponse, "GET", "/bookmakers", wrap="bookmakers", params={
                "bookmakers": bookmakers,
                "playerProps": player_props,
            })
