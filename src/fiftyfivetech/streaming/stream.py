import base64
import json
import threading
import time
from collections.abc import Sequence
from typing import Any

import websocket
import zstandard
from websocket import ABNF

from fiftyfivetech.connection import FiftyFiveTechConnection
from fiftyfivetech.exceptions import (
    FiftyFiveTechDecodeError,
    FiftyFiveTechError,
    FiftyFiveTechInvalidRequestError,
    FiftyFiveTechSocketError,
    FiftyFiveTechStreamAuthenticationError,
)
from fiftyfivetech.streaming.listener import BaseListener
from fiftyfivetech.streaming.resources import ODDS_CHANNEL, ReceiveType

# Largest frame a zstd data frame may decompress to; frames carry no content size.
MAX_FRAME_BYTES = 64 * 1024 * 1024
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"


class FiftyFiveTechStream:
    """
    One connection to the 55Tech WebSocket gateway.

    Filters are sent at login, so changing them needs a reconnect. ``start`` raises
    ``FiftyFiveTechSocketError`` when the socket fails; call it again to reconnect,
    resuming from the last ``entryId`` per channel where the gateway allows.

    :param conn: Supplies the API key and the default URL.
    :param listener: Receives every frame.
    :param url: Gateway URL; defaults to ``conn.stream_url``.
    :param channels: Channels to receive, e.g. ``["odds", "bookmakers"]``.
    :param bookmakers: Bookmaker slugs to receive, e.g. ``["pinnacle"]``; all the
        key allows if omitted.
    :param sport_ids: Sports to receive, e.g. ``[10]``; all if omitted.
    :param tournament_ids: Tournaments to receive; all if omitted.
    :param fixture_ids: Fixtures to receive; all if omitted.
    :param receive_type: ``"zstd"`` (compressed data frames), ``"zstd-dict"`` (smaller,
        with dictionaries) or ``"json"``.
    :param lang: Language for translated names, e.g. ``"en"``.
    :param client_name: Name to identify this client to the gateway.
    :param timeout: Seconds to wait to connect, or for the login reply.
    :param keepalive: Seconds without a frame before sending a ping; ``None`` never pings.
    """

    def __init__(
        self,
        conn: FiftyFiveTechConnection,
        listener: BaseListener,
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
    ) -> None:
        """
        Create a new stream.
        """
        self.conn = conn
        self.listener = listener
        self.url = url or conn.stream_url
        self.channels = list(channels)
        self.bookmakers = list(bookmakers) if bookmakers is not None else None
        self.sport_ids = list(sport_ids) if sport_ids is not None else None
        self.tournament_ids = list(tournament_ids) if tournament_ids is not None else None
        self.fixture_ids = list(fixture_ids) if fixture_ids is not None else None
        self.receive_type = receive_type
        self.lang = lang
        self.client_name = client_name
        self.timeout = timeout
        self.keepalive = keepalive

        # Resume state: the last entryId seen per channel, the gateway epoch they
        # belong to, and the channels it replays. Updated on the read thread.
        self.last_seen_ids: dict[str, str] = {}
        self.server_epoch: str | None = None
        self.replay_channels: set[str] = set()
        self.resuming = False
        self.receive_type_negotiated: str | None = None

        self._ws: websocket.WebSocket | None = None
        self._running = threading.Event()
        self._lock = threading.Lock()
        self._decompressor = zstandard.ZstdDecompressor()
        self._dict_decompressors: dict[int, zstandard.ZstdDecompressor] = {}

    @property
    def is_connected(self) -> bool:
        """
        Whether the socket is open.
        """
        return self._ws is not None and self._ws.connected

    @property
    def is_running(self) -> bool:
        """
        Whether ``start`` is reading frames.
        """
        return self._running.is_set()

    def connect(self) -> None:
        """
        Open the socket and log in, resuming if cursors are held.

        :raises FiftyFiveTechInvalidRequestError: If the connection has no API key.
        :raises FiftyFiveTechSocketError: If the socket can't be opened.
        :raises FiftyFiveTechStreamAuthenticationError: If the gateway refuses the login.
        """
        if not self.conn.api_key:
            raise FiftyFiveTechInvalidRequestError("no API key on the connection")
        self.close()
        try:
            self._ws = websocket.create_connection(self.url, timeout=self.timeout)
        except (websocket.WebSocketException, OSError) as exc:
            raise FiftyFiveTechSocketError(
                f"could not connect to stream at {self.url}: {exc}") from exc
        self.login()

    def login(self) -> None:
        """
        Send the login and wait for the gateway to accept it.

        Called by ``connect``; the gateway closes a socket with no login within 10s.
        Frames that arrive meanwhile go to the listener.

        :raises FiftyFiveTechStreamAuthenticationError: If the gateway refuses, or no
            ``login_ok`` arrives within ``timeout``.
        :raises FiftyFiveTechSocketError: If the socket fails or is closed meanwhile.
        """
        ws = self._socket()
        # Dictionaries are re-sent on every connection.
        self._dict_decompressors.clear()
        frame = self.login_frame()
        self.resuming = "lastSeenId" in frame
        self._send_json(frame)
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            try:
                ws.settimeout(max(deadline - time.monotonic(), 0.01))
                raw = self._recv(ws)
            except websocket.WebSocketTimeoutException:
                break
            except (websocket.WebSocketException, OSError) as exc:
                raise FiftyFiveTechSocketError(f"stream failed while logging in: {exc}") from exc
            data = self._decode(raw)
            mtype = data.get("type") if isinstance(data, dict) else None
            if mtype == "login_ok":
                resume = data.get("resume") or {}
                with self._lock:
                    self.server_epoch = resume.get("serverEpoch") or self.server_epoch
                    self.replay_channels = {str(c) for c in resume.get("replayChannels") or []}
                self.receive_type_negotiated = data.get("receiveType")
                ws.settimeout(self.keepalive)
                self.listener.on_data(data)
                return
            if mtype in ("login_error", "error"):
                self.close()
                raise FiftyFiveTechStreamAuthenticationError(
                    f"stream at {self.url} refused the login: {data!r:.200}", reply=data)
            self.listener.on_data(data)
        self.close()
        raise FiftyFiveTechStreamAuthenticationError(
            f"stream at {self.url} did not accept the login within {self.timeout}s")

    def login_frame(self) -> dict[str, Any]:
        """
        Build the login message, with resume cursors for the channels the gateway replays.

        :returns: The message, API key included.
        """
        frame: dict[str, Any] = {
            "type": "login",
            "apiKey": self.conn.api_key,
            "channels": self.channels,
            "receiveType": self.receive_type,
        }
        optional = {
            "bookmakers": self.bookmakers,
            "sportIds": self.sport_ids,
            "tournamentIds": self.tournament_ids,
            "fixtureIds": self.fixture_ids,
            "lang": self.lang,
            "clientName": self.client_name,
        }
        frame.update({k: v for k, v in optional.items() if v is not None})
        with self._lock:
            cursors = {ch: eid for ch, eid in self.last_seen_ids.items()
                       if ch in self.replay_channels}
            if self.server_epoch and cursors:
                frame["serverEpoch"] = self.server_epoch
                frame["lastSeenId"] = cursors
        return frame

    def reset_cursors(self, channels: Sequence[str] | None = None) -> None:
        """
        Forget resume cursors, so the next login starts fresh on those channels.

        :param channels: Channels to forget; all if omitted.
        """
        with self._lock:
            if channels is None:
                self.last_seen_ids.clear()
            else:
                for channel in channels:
                    self.last_seen_ids.pop(channel, None)

    def start(self) -> None:
        """
        Read frames and pass them to the listener until ``stop``, or until the
        listener returns ``False``. Blocks; run it in a thread to do other work.

        Connects first if not connected.

        :raises FiftyFiveTechSocketError: If the socket fails or the server closes it.
        :raises FiftyFiveTechStreamAuthenticationError: If (re)connecting is refused.
        """
        self._running.set()
        try:
            if not self.is_connected:
                self.connect()
            ws = self._socket()
            while self._running.is_set():
                try:
                    raw = self._recv(ws)
                except websocket.WebSocketTimeoutException:
                    try:
                        ws.ping()
                    except (websocket.WebSocketException, OSError) as exc:
                        raise FiftyFiveTechSocketError(
                            f"stream connection to {self.url} lost: {exc}") from exc
                    continue
                except (websocket.WebSocketException, OSError) as exc:
                    if not self._running.is_set():
                        break
                    raise FiftyFiveTechSocketError(
                        f"stream connection to {self.url} lost: {exc}") from exc
                if self.listener.on_data(self._decode(raw)) is False:
                    break
        except FiftyFiveTechError:
            # Raised because ``stop`` closed the socket mid-connect: not a failure.
            if self._running.is_set():
                raise
        finally:
            self._running.clear()
            self.close()

    def stop(self) -> None:
        """
        Stop ``start`` and close the socket; safe from any thread.
        """
        self._running.clear()
        self.close()

    def close(self) -> None:
        """
        Close the socket, if open.
        """
        ws, self._ws = self._ws, None
        if ws is not None:
            ws.close()

    def _recv(self, ws: websocket.WebSocket) -> str | bytes:
        """
        Read one data frame.

        :returns: A text frame's text, or a binary frame's bytes.
        :raises FiftyFiveTechSocketError: If the server closes the socket, with its close code.
        """
        opcode, data = ws.recv_data()
        if opcode == ABNF.OPCODE_TEXT:
            return data.decode("utf-8") if isinstance(data, bytes) else data
        if opcode == ABNF.OPCODE_BINARY:
            return data
        code = int.from_bytes(data[:2], "big") if len(data) >= 2 else None
        reason = data[2:].decode("utf-8", errors="replace") if len(data) > 2 else None
        raise FiftyFiveTechSocketError(
            f"stream connection to {self.url} closed by the server"
            + (f": {code} {reason or ''}".rstrip() if code else ""),
            close_code=code, close_reason=reason)

    def _decode(self, raw: str | bytes) -> Any:
        """
        Decode one frame, loading dictionaries and advancing the resume cursors.
        """
        if isinstance(raw, (bytes, bytearray)):
            if raw[:4] == ZSTD_MAGIC:
                try:
                    dict_id = zstandard.get_frame_parameters(raw).dict_id
                    decompressor = self._dict_decompressors.get(dict_id, self._decompressor)
                    raw = decompressor.decompress(raw, max_output_size=MAX_FRAME_BYTES)
                except zstandard.ZstdError as exc:
                    raise FiftyFiveTechDecodeError("could not decompress a stream frame") from exc
            raw = raw.decode("utf-8", errors="replace")
        try:
            data = json.loads(raw)
        except ValueError:
            return raw
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict):
                self._track(item)
        return data

    def _track(self, message: dict[str, Any]) -> None:
        """
        Update the stream's state from one message: cursors and dictionaries.
        """
        channel, entry_id = message.get("channel"), message.get("entryId")
        mtype = message.get("type")
        if mtype == "dict" and isinstance(message.get("dictId"), int):
            data = base64.b64decode(message.get("data") or "")
            self._dict_decompressors[message["dictId"]] = zstandard.ZstdDecompressor(
                dict_data=zstandard.ZstdCompressionDict(data))
            return
        with self._lock:
            if isinstance(channel, str) and isinstance(entry_id, str):
                self.last_seen_ids[channel] = entry_id
            elif mtype == "snapshot_required":
                # The gap can't be replayed, so these cursors are worthless.
                channels = message.get("channels") or list(self.last_seen_ids)
                for ch in channels:
                    self.last_seen_ids.pop(str(ch), None)
                self.server_epoch = message.get("serverEpoch") or self.server_epoch

    def _send_json(self, message: dict[str, Any]) -> None:
        """
        Send one JSON message.
        """
        try:
            self._socket().send(json.dumps(message))
        except (websocket.WebSocketException, OSError) as exc:
            raise FiftyFiveTechSocketError(f"stream send failed: {exc}") from exc

    def _socket(self) -> websocket.WebSocket:
        """
        The open socket.
        """
        if self._ws is None:
            raise FiftyFiveTechSocketError("stream is not connected")
        return self._ws
