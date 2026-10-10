from datetime import datetime
from typing import Any

import requests


class FiftyFiveTechError(Exception):
    """
    Base class for every error this SDK raises.
    """


class FiftyFiveTechInvalidRequestError(FiftyFiveTechError):
    """
    A call is missing a required value, or has one out of range; raised before
    anything is sent.
    """


class FiftyFiveTechDecodeError(FiftyFiveTechError):
    """
    A 2xx response body or stream frame could not be decoded; the cause is the ``__cause__``.
    """


class FiftyFiveTechTransportError(FiftyFiveTechError):
    """
    The request got no response; the ``requests`` error is the ``__cause__``.
    """

    def __init__(self, message: str, *, sent_at: datetime, latency_ms: float) -> None:
        super().__init__(message)
        self.sent_at = sent_at
        self.latency_ms = latency_ms


class FiftyFiveTechSocketError(FiftyFiveTechError):
    """
    The stream's socket could not connect, failed, or was closed by the server;
    any underlying error is the ``__cause__``.
    """

    def __init__(
        self, message: str, *, close_code: int | None = None, close_reason: str | None = None,
    ) -> None:
        super().__init__(message)
        self.close_code = close_code
        self.close_reason = close_reason


class FiftyFiveTechStreamAuthenticationError(FiftyFiveTechError):
    """
    The stream refused the login, or did not answer it in time.
    """

    def __init__(self, message: str, reply: Any = None) -> None:
        super().__init__(message)
        self.reply = reply


class FiftyFiveTechAPIError(FiftyFiveTechError):
    """
    The API answered with a non-2xx status.
    """

    def __init__(
        self,
        response: requests.Response,
        *,
        sent_at: datetime,
        received_at: datetime,
        latency_ms: float,
    ) -> None:
        try:
            body: Any = response.json()
        except ValueError:
            body = response.text

        code = message = details = None
        if isinstance(body, dict):
            code = body.get("code")
            message = body.get("message")
            details = body.get("details")

        self.status_code = response.status_code
        self.code = code
        self.message = message
        self.details = details
        self.body = body
        self.response = response
        self.sent_at = sent_at
        self.received_at = received_at
        self.latency_ms = latency_ms
        summary = message or response.reason or "error"
        if code:
            summary += f" ({code})"
        super().__init__(f"{self.status_code} {summary}")

    @property
    def retry_after(self) -> float | None:
        """
        Seconds to wait before retrying, on a 429: the ``Retry-After`` header.
        """
        value = self.response.headers.get("Retry-After")
        try:
            return float(value) if value is not None else None
        except ValueError:
            return None
