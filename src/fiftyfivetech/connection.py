import time
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import requests

from fiftyfivetech.exceptions import (
    FiftyFiveTechAPIError,
    FiftyFiveTechDecodeError,
    FiftyFiveTechInvalidRequestError,
    FiftyFiveTechTransportError,
)
from fiftyfivetech.utils import decode, format_query_params


DEFAULT_BASE_URL = "https://v5.oddspapi.io/en"
DEFAULT_STREAM_URL = "wss://v5.oddspapi.io/ws"


class FiftyFiveTechConnection:
    """
    Where and how to reach the 55Tech API.

    Every endpoint function takes one first::

        conn = FiftyFiveTechConnection(api_key)
        markets.get_markets(conn, sport_id=10)

    :param api_key: API key, sent as the ``X-API-Key`` header and in the stream login.
    :param base_url: API root, including the language segment: one of ``en``, ``es``,
        ``fr``, ``pt``, ``de``, ``it``, ``ru``, ``zh``. Only names are translated.
    :param stream_url: WebSocket gateway URL.
    :param session: ``requests.Session`` to send through; created if omitted. Mount
        your own adapter on it for retries or TCP keepalives.
    :param timeout: Seconds to wait per request; ``None`` waits forever.
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
        Create a connection; see the class docstring for parameters.
        """
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.stream_url = stream_url
        self.http = session or requests.Session()
        self.timeout = timeout

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
    ) -> Any:
        """
        Send a request to any path, e.g. one this SDK does not cover yet.

        :param method: HTTP method.
        :param path: Path below ``base_url``, e.g. ``"/sports"``.
        :param params: Query parameters; ``None`` dropped, lists comma-joined,
            booleans lower-case.
        :param json: Request body.
        :returns: The decoded JSON body, or ``None`` if empty.
        :raises FiftyFiveTechAPIError: On a non-2xx response.
        :raises FiftyFiveTechTransportError: If no response was received.
        :raises FiftyFiveTechDecodeError: If a 2xx body is not JSON.
        """
        return self._send(method, path, params, json)[0]

    def call(
        self,
        model: Any,
        method: str,
        path: str,
        *,
        path_params: Mapping[str, str] | None = None,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
        wrap: str | None = None,
    ) -> Any:
        """
        Send a request and decode the body into ``model``, stamped with its timings.

        :param model: Response model to decode into.
        :param method: HTTP method.
        :param path: Path below ``base_url``, with ``{placeholders}``.
        :param path_params: Placeholder values; URL-escaped.
        :param params: Query parameters, encoded as in ``request``.
        :param json: Request body.
        :param wrap: For an array body, the key to wrap it under, e.g. ``"markets"``.
        :returns: The decoded model.
        :raises FiftyFiveTechInvalidRequestError: If a path parameter is empty.
        :raises FiftyFiveTechDecodeError: If the body does not fit ``model``.
        """
        if path_params:
            missing = [k for k, v in path_params.items() if not v]
            if missing:
                raise FiftyFiveTechInvalidRequestError(
                    f"{path} needs a value for {', '.join(missing)}")
            path = path.format_map(
                {k: quote(str(v), safe="") for k, v in path_params.items()})
        body, sent_at, received_at, latency_ms = self._send(method, path, params, json)
        if wrap is not None:
            if not isinstance(body, list):
                raise FiftyFiveTechDecodeError(
                    f"{method} {path}: expected a JSON array, got {body!r:.100}")
            body = {wrap: body}
        try:
            result = decode(model, body or {})
        except FiftyFiveTechDecodeError as exc:
            raise FiftyFiveTechDecodeError(
                f"{method} {path}: body does not fit {model.__name__}") from exc
        result.sent_at = sent_at
        result.received_at = received_at
        result.latency_ms = latency_ms
        return result

    def _send(
        self,
        method: str,
        path: str,
        params: Mapping[str, Any] | None,
        json: Any,
    ) -> tuple[Any, datetime, datetime, float]:
        """
        Send a request, timing it.

        :returns: The decoded JSON body (or ``None`` if empty), when it was sent and
            when the response arrived (UTC), and the round trip in milliseconds.
        """
        sent_at = datetime.now(timezone.utc)
        started = time.perf_counter()
        try:
            response = self.http.request(
                method,
                self.base_url + path,
                params=format_query_params(params or {}),
                json=json,
                # The header keeps the key out of URLs, and so out of logs and errors.
                headers={"Accept": "application/json", "X-API-Key": self.api_key},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise FiftyFiveTechTransportError(
                f"{method} {path} failed: {exc}", sent_at=sent_at,
                latency_ms=(time.perf_counter() - started) * 1000) from exc
        # The monotonic clock times the trip; the wall clock can jump.
        latency_ms = (time.perf_counter() - started) * 1000
        received_at = datetime.now(timezone.utc)
        if not response.ok:
            raise FiftyFiveTechAPIError(
                response, sent_at=sent_at, received_at=received_at, latency_ms=latency_ms)
        if not response.content:
            return None, sent_at, received_at, latency_ms
        try:
            return response.json(), sent_at, received_at, latency_ms
        except ValueError as exc:
            raise FiftyFiveTechDecodeError(f"{method} {path} returned a non-JSON body") from exc
