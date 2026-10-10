# 55tech

A Python client for the [55Tech](https://oddspapi.io) REST and WebSocket APIs.

```sh
pip install 55tech
```

## REST

```python
from fiftyfivetech import FiftyFiveTechClient

client = FiftyFiveTechClient(api_key)

markets = client.get_markets(sport_id=10).markets
fixtures = client.get_fixtures(sport_id=10).fixtures
odds = client.get_fixture_odds(fixtures[0].fixture_id, bookmakers=["pinnacle"]).odds
```

Errors derive from `FiftyFiveTechError`.

## Streaming

```python
import threading
from fiftyfivetech.streaming import FixtureBook, Reconnect, SnapshotRequired, StreamListener

listener = StreamListener()
stream = client.create_stream(listener, channels=["odds", "bookmakers"],
                              bookmakers=["pinnacle"], sport_ids=[10])
threading.Thread(target=stream.start, daemon=True).start()

# Seed each fixture you follow, after the stream is connected
listener.apply_snapshot(client.get_fixture_odds(fixture_id, bookmakers=["pinnacle"]), ["pinnacle"])

while True:
    update = listener.output_queue.get()
    if isinstance(update, FixtureBook):
        if update.is_tradeable("pinnacle"):          # has odds, not stale, not suspended
            record = update.get("pinnacle", outcome_id)
            ...
    elif isinstance(update, SnapshotRequired):
        ...    # updates were missed: call apply_snapshot again for each fixture you follow
    elif isinstance(update, Reconnect):
        stream.stop()    # the gateway is about to close: start() again
```

The listener merges stream updates and snapshots into one `FixtureBook` per fixture, keeping the later `changed_at` per record.
`remove_fixture` frees a finished one.

`start()` raises `FiftyFiveTechSocketError` when the connection drops; call it again to reconnect and resume.
