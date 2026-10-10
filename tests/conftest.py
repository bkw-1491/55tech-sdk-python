import dataclasses
import os
import types
import typing
from datetime import datetime
from pathlib import Path

import pytest
from dotenv import load_dotenv

from fiftyfivetech import FiftyFiveTechClient, FiftyFiveTechError
from fiftyfivetech.fixtures import Fixture
from fiftyfivetech.utils import get_fields, parse_datetime, to_snake_case

load_dotenv(Path(__file__).parents[1] / ".env")


R = typing.TypeVar("R")

# Deprecated keys the API still sends, left off the models on purpose.
DEPRECATED = {
    "BookmakerFixtureMeta": {"staleOddsResponseCode"},
    "Bookmaker": {"serverGroup", "price"},
}
SOCCER = 10
PINNACLE = "pinnacle"


@pytest.fixture(scope="session")
def client() -> FiftyFiveTechClient:
    """
    A client on the live API; skips when the key is missing.
    """
    api_key = os.environ.get("FIFTYFIVETECH_API_KEY")
    if not api_key:
        pytest.skip("FIFTYFIVETECH_API_KEY not set")
    return FiftyFiveTechClient(api_key)


@pytest.fixture(scope="session")
def fixtures(client: FiftyFiveTechClient) -> list[Fixture]:
    """
    Every soccer fixture, fetched once.
    """
    return check(client.get_fixtures(sport_id=SOCCER)).fixtures


@pytest.fixture(scope="session")
def pinnacle_fixture(client: FiftyFiveTechClient) -> Fixture:
    """
    A pre-game soccer fixture Pinnacle is quoting now, so it maps both ways.
    """
    quoted = client.get_fixtures(sport_id=SOCCER, bookmakers=[PINNACLE]).fixtures
    for fixture in quoted:
        meta = fixture.bookmakers.get(PINNACLE)
        if (fixture.status.status_id == 0 and meta and meta.has_odds
                and fixture.external_providers.get("pinnacleId")):
            return fixture
    pytest.skip("no pre-game soccer fixture Pinnacle is quoting")


def schema_errors(tp: typing.Any, value: typing.Any, path: str = "$") -> list[str]:
    """
    Ways a decoded JSON ``value`` doesn't match type ``tp``: keys the model doesn't
    declare, required fields missing or null, wrong types, unparseable timestamps
    and enum values outside the declared set.
    """
    args = [a for a in typing.get_args(tp) if a is not type(None)]
    origin = typing.get_origin(tp)
    optional = origin in (typing.Union, types.UnionType) and len(args) < len(typing.get_args(tp))
    if value is None:
        return [] if optional or tp is typing.Any else [f"{path}: null for non-optional {tp}"]
    if origin in (typing.Union, types.UnionType):
        models = [a for a in args if dataclasses.is_dataclass(a)]
        if models and isinstance(value, dict):
            keys = {to_snake_case(k) for k in value}
            best = max(models, key=lambda m: len(keys & set(get_fields(m))))
            return schema_errors(best, value, path)
        return schema_errors(args[0], value, path) if len(args) == 1 else []
    if origin is typing.Literal:
        return [] if value in typing.get_args(tp) else [f"{path}: {value!r} not in {tp}"]
    if origin is list:
        if not isinstance(value, list):
            return [f"{path}: expected a list, got {type(value).__name__}"]
        return [p for i, v in enumerate(value) for p in schema_errors(args[0], v, f"{path}[{i}]")]
    if origin is dict:
        if not isinstance(value, dict):
            return [f"{path}: expected an object, got {type(value).__name__}"]
        return [p for k, v in value.items() for p in schema_errors(args[1], v, f"{path}.{k}")]
    if dataclasses.is_dataclass(tp):
        model: typing.Any = tp
        if not isinstance(value, dict):
            return [f"{path}: expected an object, got {type(value).__name__}"]
        model_fields = get_fields(tp)
        given = {to_snake_case(k): (k, v) for k, v in value.items()}
        found = [f"{path}.{k}: not declared by {model.__name__}"
                 for name, (k, _) in given.items()
                 if name not in model_fields and k not in DEPRECATED.get(model.__name__, ())]
        for field in dataclasses.fields(tp):
            if not field.init or field.name == "raw":
                continue
            required = (field.default is dataclasses.MISSING
                        and field.default_factory is dataclasses.MISSING)
            if field.name not in given:
                if required:
                    found.append(f"{path}: {model.__name__}.{field.name} missing")
                continue
            key, sub = given[field.name]
            found += schema_errors(model_fields[field.name], sub, f"{path}.{key}")
        return found
    if tp is datetime:
        try:
            parse_datetime(value)
        except FiftyFiveTechError:
            return [f"{path}: {value!r} is not a timestamp"]
        return []
    if tp is float:
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
        return [] if ok else [f"{path}: expected a number, got {value!r}"]
    if tp in (int, str, bool):
        ok = isinstance(value, tp) and not (tp is int and isinstance(value, bool))
        return [] if ok else [f"{path}: expected {tp.__name__}, got {value!r}"]
    return []


def check(result: R) -> R:
    """
    Assert a live response matches its model exactly, and return it.
    """
    found = schema_errors(type(result), result.raw)
    assert not found, f"{type(result).__name__} doesn't match the API:\n" + "\n".join(found[:20])
    return result
