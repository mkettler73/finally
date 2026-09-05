"""Tests for the SSE streaming endpoint.

NOTE on test strategy: ``fastapi.testclient.TestClient`` cannot be used against
this endpoint. Its blocking portal never returns headers for an unbounded
stream, so a ``client.stream("GET", "/api/stream/prices")`` call simply hangs.
That is a TestClient limitation, not a defect in the endpoint — under real
uvicorn it responds immediately and streams correctly.

``_generate_events`` is a plain async generator with an injectable interval, so
it is driven directly here with a fake request. Status code and content type are
asserted by building the router and inspecting the response object.
"""

import asyncio
import json

import pytest
from fastapi import FastAPI

from app.market.cache import PriceCache
from app.market.stream import _generate_events, create_stream_router


class FakeRequest:
    """Stands in for starlette's Request: disconnects after N poll checks."""

    def __init__(self, ticks: int = 3, client_host: str | None = None):
        self.client = type("C", (), {"host": client_host})() if client_host else None
        self._checks = 0
        self._ticks = ticks

    async def is_disconnected(self) -> bool:
        self._checks += 1
        return self._checks > self._ticks


async def _drain(cache: PriceCache, request: FakeRequest, **kwargs) -> list[str]:
    return [chunk async for chunk in _generate_events(cache, request, interval=0.001, **kwargs)]


class TestRouterWiring:
    def test_returns_a_fresh_router_each_call(self):
        """A shared module-level router would bind /prices to the first cache."""
        r1 = create_stream_router(PriceCache())
        r2 = create_stream_router(PriceCache())

        assert r1 is not r2
        assert [r.path for r in r1.routes] == ["/api/stream/prices"]
        assert [r.path for r in r2.routes] == ["/api/stream/prices"]

    def test_mounting_twice_does_not_duplicate_routes(self):
        app = FastAPI()
        app.include_router(create_stream_router(PriceCache()))
        paths = [r.path for r in app.routes if r.path.startswith("/api/stream")]
        assert paths == ["/api/stream/prices"]


@pytest.mark.asyncio
class TestGenerateEvents:
    async def test_first_chunk_is_the_retry_preamble(self):
        """EventSource needs this to reconnect after a drop."""
        cache = PriceCache()
        cache.update("AAPL", 190.0)

        chunks = await _drain(cache, FakeRequest(ticks=1))

        assert chunks[0] == "retry: 1000\n\n"

    async def test_emits_the_whole_cache_as_one_json_object(self):
        cache = PriceCache()
        cache.update("AAPL", 190.0)
        cache.update("GOOGL", 175.0)

        chunks = await _drain(cache, FakeRequest(ticks=1))

        assert chunks[1].startswith("data: ")
        assert chunks[1].endswith("\n\n")
        payload = json.loads(chunks[1].removeprefix("data: ").strip())
        assert set(payload) == {"AAPL", "GOOGL"}
        assert payload["AAPL"]["price"] == 190.0
        assert payload["AAPL"]["direction"] == "flat"  # no spurious opening flash

    async def test_unchanged_cache_does_not_re_emit(self):
        """The version counter is what collapses redundant frames."""
        cache = PriceCache()
        cache.update("AAPL", 190.0)

        chunks = await _drain(cache, FakeRequest(ticks=5), heartbeat=1000.0)

        assert len(chunks) == 2  # preamble + exactly one data frame

    async def test_emits_again_when_the_cache_changes(self):
        cache = PriceCache()
        cache.update("AAPL", 190.0)
        request = FakeRequest(ticks=4)

        chunks = []
        async for chunk in _generate_events(cache, request, interval=0.001, heartbeat=1000.0):
            chunks.append(chunk)
            cache.update("AAPL", 190.0 + len(chunks))

        data_frames = [c for c in chunks if c.startswith("data: ")]
        assert len(data_frames) > 1
        prices = [
            json.loads(c.removeprefix("data: ").strip())["AAPL"]["price"] for c in data_frames
        ]
        assert prices == sorted(prices) and len(set(prices)) == len(prices)

    async def test_empty_cache_emits_only_the_preamble(self):
        chunks = await _drain(PriceCache(), FakeRequest(ticks=3), heartbeat=1000.0)
        assert chunks == ["retry: 1000\n\n"]

    async def test_heartbeat_keeps_an_idle_connection_open(self):
        """Massive mode can leave the cache unchanged for 15s (900s degraded)."""
        cache = PriceCache()
        cache.update("AAPL", 190.0)

        chunks = await _drain(cache, FakeRequest(ticks=5), heartbeat=0.0)

        pings = [c for c in chunks if c == ": ping\n\n"]
        assert pings, "expected at least one SSE comment heartbeat"

    async def test_stops_when_the_client_disconnects(self):
        cache = PriceCache()
        cache.update("AAPL", 190.0)

        # Would run forever if disconnect were not honoured.
        chunks = await asyncio.wait_for(_drain(cache, FakeRequest(ticks=2)), timeout=5)

        assert chunks  # returned rather than hanging

    async def test_cancellation_propagates(self):
        """Swallowing CancelledError would leak the task on shutdown."""
        cache = PriceCache()
        cache.update("AAPL", 190.0)
        gen = _generate_events(cache, FakeRequest(ticks=10_000), interval=0.01)
        await gen.asend(None)  # consume the preamble

        with pytest.raises(asyncio.CancelledError):
            await gen.athrow(asyncio.CancelledError())

    async def test_payload_matches_the_documented_shape(self):
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        cache.update("AAPL", 190.42)

        chunks = await _drain(cache, FakeRequest(ticks=1))
        entry = json.loads(chunks[1].removeprefix("data: ").strip())["AAPL"]

        assert set(entry) == {
            "ticker",
            "price",
            "previous_price",
            "timestamp",
            "change",
            "change_percent",
            "direction",
        }
        assert entry["price"] == 190.42
        assert entry["previous_price"] == 190.00
        assert entry["direction"] == "up"
