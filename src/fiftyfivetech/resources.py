from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from fiftyfivetech.utils import decode, encode


@dataclass(kw_only=True, slots=True)
class BaseModel:
    """
    Base class for every model.
    """

    @classmethod
    def from_json(cls: Any, data: dict[str, Any]) -> Any:
        """
        Build the model from a decoded JSON object.

        :param data: Object as the API returns it; undeclared keys are ignored.
        :returns: The model.
        """
        return decode(cls, data)

    def to_json(self) -> dict[str, Any]:
        """
        Convert the model to a JSON-ready dict.

        :returns: The non-``None`` fields, with camelCase keys.
        """
        return encode(self)


@dataclass(kw_only=True, slots=True)
class Response(BaseModel):
    """
    Base class for a top-level API response.

    Endpoints whose body is a JSON array are wrapped in an object, e.g.
    ``{"markets": [...]}``, so every response has named fields and a ``raw``.
    """
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)
    sent_at: datetime = field(init=False, repr=False, compare=False)
    received_at: datetime = field(init=False, repr=False, compare=False)
    latency_ms: float = field(init=False, repr=False, compare=False)
