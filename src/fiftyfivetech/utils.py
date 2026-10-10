import dataclasses
import re
import types
import typing
from collections.abc import Callable, Iterator, Mapping, Sequence
from datetime import datetime, timezone
from functools import cache
from typing import Any, Literal, TypeVar, Union

from fiftyfivetech.exceptions import FiftyFiveTechDecodeError, FiftyFiveTechInvalidRequestError

T = TypeVar("T")
Item = TypeVar("Item")

DEFAULT_BATCH_SIZE = 200
CAMEL_BOUNDARY = re.compile(r"(?<!^)(?=[A-Z])")
ISO_DATETIME = re.compile(
    r"(?P<base>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?)"
    r"(?:\.(?P<frac>\d+))?"
    r"(?P<tz>Z|[+-]\d{2}:?\d{2})?$"
)


def to_snake_case(key: str) -> str:
    """
    Convert an API key to its field name, e.g. ``bookmakerFixtureId`` -> ``bookmaker_fixture_id``.

    :param key: JSON key.
    :returns: Field name.
    """
    return CAMEL_BOUNDARY.sub("_", key.lstrip("_")).lower()


def to_camel_case(name: str) -> str:
    """
    Convert a field name to its API key, e.g. ``participants_rotated`` -> ``participantsRotated``.

    :param name: Field name.
    :returns: JSON key.
    """
    head, *rest = name.split("_")
    return head + "".join(part.title() for part in rest)


def parse_datetime(value: str) -> datetime:
    """
    Parse an API timestamp into an aware datetime.

    Digits beyond microseconds are truncated; no offset means UTC.

    :param value: ISO 8601 string, e.g. ``"2026-10-10T08:02:25.161029+00:00"``.
    :returns: The timestamp.
    :raises FiftyFiveTechDecodeError: If ``value`` is not an ISO 8601 date-time.
    """
    match = ISO_DATETIME.match(value)
    if not match:
        raise FiftyFiveTechDecodeError(f"not an ISO 8601 date-time: {value!r}")
    text = match["base"]
    if match["frac"]:
        text += "." + match["frac"][:6].ljust(6, "0")
    tz = match["tz"]
    if tz and tz != "Z":
        text += tz if ":" in tz else f"{tz[:3]}:{tz[3:]}"
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def to_epoch_seconds(value: datetime | int | None) -> int | None:
    """
    Convert a schedule-time filter to epoch seconds, as the API takes them.

    :param value: Aware datetime (naive means UTC), epoch seconds, or ``None``.
    :returns: Whole epoch seconds, or ``None``.
    """
    if value is None or isinstance(value, int):
        return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp())


def from_timestamp(seconds: float) -> datetime:
    """
    Convert an API epoch time to an aware datetime.

    :param seconds: Seconds since the epoch; divide millisecond fields such as
        ``changedAt`` by 1000 first.
    :returns: The time, in UTC.
    """
    return datetime.fromtimestamp(seconds, timezone.utc)


@cache
def get_fields(cls: type) -> dict[str, Any]:
    """
    Resolve a dataclass's init field types, cached per class.

    :param cls: Model class.
    :returns: Field name to resolved type.
    """
    hints = typing.get_type_hints(cls)
    return {f.name: hints[f.name] for f in dataclasses.fields(cls) if f.init}


def decode(tp: Any, value: Any) -> Any:
    """
    Convert decoded JSON into ``tp``.

    Models ignore keys they don't declare; a union of models picks the one whose
    fields cover most of the object's keys.

    :param tp: A model, ``list[...]``, ``dict[str, ...]``, ``X | None``, ``datetime``,
        ``Literal`` alias or scalar.
    :param value: Decoded JSON value.
    :returns: The converted value; ``None`` stays ``None``.
    :raises FiftyFiveTechDecodeError: If ``value`` doesn't have the shape of ``tp``.
    """
    if value is None:
        return None

    try:
        origin = typing.get_origin(tp)

        if origin in (Union, types.UnionType):
            options = [a for a in typing.get_args(tp) if a is not type(None)]
            models = [o for o in options if dataclasses.is_dataclass(o)]
            if len(options) == 1 or not models or not isinstance(value, dict):
                return decode(options[0], value)

            # Untagged unions: the model covering most of the object's keys
            # wins; ties go to the first.
            keys = {to_snake_case(k) for k in value}
            return decode(max(models, key=lambda m: len(keys & set(get_fields(m)))), value)

        if origin is list:
            (item,) = typing.get_args(tp)
            return [decode(item, v) for v in value]

        if origin is dict:
            _, item = typing.get_args(tp)
            return {k: decode(item, v) for k, v in value.items()}

        if origin is Literal or tp is Any:
            return value

        if dataclasses.is_dataclass(tp):
            fields = get_fields(tp)
            kwargs: dict[str, Any] = {}
            for key, item in value.items():
                name = to_snake_case(key)

                # A list field the API sent as null keeps its empty-list default.
                null_list = item is None and typing.get_origin(fields.get(name)) is list
                if name in fields and not null_list:
                    kwargs[name] = decode(fields[name], item)

            if "raw" in fields and "raw" not in kwargs:
                kwargs["raw"] = value
            model: Any = tp
            try:
                return model(**kwargs)
            except TypeError as exc:  # a required field is missing
                raise FiftyFiveTechDecodeError(f"{model.__name__}: {exc}") from exc

        if tp is datetime:
            return parse_datetime(value)

        if tp is float and isinstance(value, int) and not isinstance(value, bool):
            return float(value)

        return value

    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        # Wrong shapes (a string where a list belongs, say) surface here.
        raise FiftyFiveTechDecodeError(f"cannot decode {value!r:.100} as {tp}") from exc


def encode(value: Any) -> Any:
    """
    Convert a model, or a structure of models, to JSON-ready data.

    ``None`` fields are omitted.

    :param value: Model, list, dict, datetime or scalar.
    :returns: The JSON-ready equivalent, with camelCase keys.
    """
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            to_camel_case(f.name): encode(getattr(value, f.name))
            for f in dataclasses.fields(value)
            if f.init and f.name != "raw" and getattr(value, f.name) is not None
        }
    if isinstance(value, list):
        return [encode(v) for v in value]
    if isinstance(value, dict):
        return {k: encode(v) for k, v in value.items()}
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return value


def format_query_params(params: Mapping[str, Any]) -> dict[str, Any]:
    """
    Encode query parameters the way the API expects.

    :param params: Raw parameters; ``None`` values are dropped, lists comma-joined,
        booleans lower-case.
    :returns: Parameters ready for ``requests``.
    """
    out: dict[str, Any] = {}
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, str):
            out[key] = value
        elif isinstance(value, bool):
            out[key] = "true" if value else "false"
        elif isinstance(value, Sequence):
            out[key] = ",".join(str(v) for v in value)
        else:
            out[key] = str(value)
    return out


def batch(
    method: Callable[..., T],
    items: Sequence[Item],
    param: str,
    size: int = DEFAULT_BATCH_SIZE,
    **kwargs: Any,
) -> Iterator[T]:
    """
    Send ``items`` through a list-taking endpoint ``size`` at a time, yielding each response.

    Nothing is sent until the iterator is consumed, one call per step, so you
    can ``break`` to stop. If a call raises, the batches before it have already
    been sent::

        for page in batch(client.get_fixture_mapping, pin_ids, "bookmaker_fixture_ids",
                          bookmaker="pinnacle"):
            links += [m for m in page.mappings if m.fixture_id]

    :param method: A client method, e.g. ``client.get_fixture_mapping``.
    :param items: Everything to send, in order.
    :param param: The keyword ``method`` takes the list as, e.g. ``"fixture_ids"``.
    :param size: Items per call, at least 1.
    :param kwargs: Further keyword arguments for every call.
    :returns: An iterator of responses, one per batch, in order.
    :raises FiftyFiveTechInvalidRequestError: If ``size`` is below 1 (on first iteration).
    """
    if size < 1:
        raise FiftyFiveTechInvalidRequestError(f"size must be at least 1, not {size}")
    for start in range(0, len(items), size):
        yield method(**{param: items[start:start + size]}, **kwargs)
