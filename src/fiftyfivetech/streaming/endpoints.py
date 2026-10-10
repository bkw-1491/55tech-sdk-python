from collections.abc import Sequence

from fiftyfivetech.endpoints import Endpoints
from fiftyfivetech.streaming.listener import BaseListener, StreamListener
from fiftyfivetech.streaming.resources import ODDS_CHANNEL, ReceiveType
from fiftyfivetech.streaming.stream import FiftyFiveTechStream


class StreamingEndpoints(Endpoints):
    """
    The WebSocket gateway.
    """

    def create_stream(
        self,
        listener: BaseListener | None = None,
        *,
        url: str | None = None,
        channels: Sequence[str] = (ODDS_CHANNEL,),
        bookmakers: Sequence[str] | None = None,
        sport_ids: Sequence[int] | None = None,
        tournament_ids: Sequence[int] | None = None,
        fixture_ids: Sequence[str] | None = None,
        receive_type: ReceiveType = "zstd",
        lang: str | None = None,
        client_name: str | None = None,
        timeout: float = 30.0,
        keepalive: float | None = 60.0,
    ) -> FiftyFiveTechStream:
        """
        Create a stream on this connection; nothing is sent until it connects.

        :param listener: Receives every frame; a new ``StreamListener`` if omitted.
        :param url: Gateway URL; defaults to the connection's ``stream_url``.
        :param channels: Channels to receive.
        :param bookmakers: Bookmaker slugs to receive; all the key allows if omitted.
        :param sport_ids: Sports to receive; all if omitted.
        :param tournament_ids: Tournaments to receive; all if omitted.
        :param fixture_ids: Fixtures to receive; all if omitted.
        :param receive_type: ``"zstd"``, ``"zstd-dict"`` or ``"json"``.
        :param lang: Language for translated names, e.g. ``"en"``.
        :param client_name: Name to identify this client to the gateway.
        :param timeout: Seconds to wait to connect, or for the login reply.
        :param keepalive: Seconds without a frame before sending a ping; ``None`` never pings.
        :returns: The stream.
        """
        return FiftyFiveTechStream(
            self.conn,
            listener if listener is not None else StreamListener(),
            url=url,
            channels=channels,
            bookmakers=bookmakers,
            sport_ids=sport_ids,
            tournament_ids=tournament_ids,
            fixture_ids=fixture_ids,
            receive_type=receive_type,
            lang=lang,
            client_name=client_name,
            timeout=timeout,
            keepalive=keepalive,
        )
