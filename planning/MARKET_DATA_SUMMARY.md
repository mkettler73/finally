# Market Data Backend — Summary

**Status:** Complete. Reviewed 2026-09-05 (`MARKET_DATA_REVIEW.md`); all findings resolved.

## What Was Built

A complete market data subsystem in `backend/app/market/` (8 modules, ~500 lines) providing live price simulation and real market data via a unified interface.

### Architecture

```
MarketDataSource (ABC)
├── SimulatorDataSource  →  GBM simulator (default, no API key needed)
└── MassiveDataSource    →  Polygon.io REST poller (when MASSIVE_API_KEY set)
        │
        ▼
   PriceCache (thread-safe, in-memory)
        │
        ├──→ SSE stream endpoint (/api/stream/prices)
        ├──→ Portfolio valuation
        └──→ Trade execution
```

### Modules

| File | Purpose |
|------|---------|
| `models.py` | `PriceUpdate` — immutable frozen dataclass (ticker, price, previous_price, timestamp, change, direction) |
| `interface.py` | `MarketDataSource` — abstract base class defining `start/stop/add_ticker/remove_ticker/get_tickers` |
| `cache.py` | `PriceCache` — thread-safe price store with version counter for SSE change detection |
| `seed_prices.py` | Realistic seed prices, per-ticker GBM params (drift/volatility), correlation groups |
| `simulator.py` | `GBMSimulator` (Geometric Brownian Motion with Cholesky-correlated moves) + `SimulatorDataSource` |
| `massive_client.py` | `MassiveDataSource` — REST polling client for Polygon.io via the `massive` package, with `_extract_price()` parsing and a free-tier grouped-daily fallback |
| `factory.py` | `create_market_data_source()` — selects simulator or Massive based on `MASSIVE_API_KEY` env var |
| `stream.py` | `create_stream_router()` — FastAPI SSE endpoint factory using version-based change detection |

### Key Design Decisions

- **Strategy pattern** — both data sources implement the same ABC; downstream code is source-agnostic
- **PriceCache as single point of truth** — producers write, consumers read; no direct coupling
- **GBM with correlated moves** — Cholesky decomposition of sector-based correlation matrix; tech stocks correlate at 0.6, finance at 0.5, cross-sector at 0.3
- **Random shock events** — ~0.1% chance per tick per ticker of a 2-5% move for visual drama
- **SSE over WebSockets** — simpler, one-way push, universal browser support

## Test Suite

**151 tests, all passing. 97% coverage.** 9 test modules in `backend/tests/`.

| Module | Tests | Covers |
|--------|-------|--------|
| test_models.py | 11 | `models.py` 100% |
| test_cache.py | 14 | `cache.py` 100% |
| test_simulator.py | 23 | `simulator.py` 98% |
| test_simulator_source.py | 13 | simulator integration |
| test_massive.py | 35 | `massive_client.py` 93% |
| test_stream.py | 11 | `stream.py` 91% |
| test_conformance.py | 32 | both sources against the ABC contract |
| test_factory.py | 7 | `factory.py` 100% |
| test_main.py | 7 | `main.py` 100% |

`test_conformance.py` is the highest-value module: one parametrised suite runs
every `MarketDataSource` obligation against both the simulator and Massive, so a
source-specific divergence fails the build.

Two rules the suite holds to: **no network** (the REST client is always mocked)
and **no real cadences** (intervals are injected). Massive fixtures are built
with real `TickerSnapshot.from_dict` models rather than bare `MagicMock`, which
would auto-create whatever attribute is asked of it and certify a broken client
as working.

Note: `fastapi.testclient.TestClient` cannot consume `/api/stream/prices` — its
blocking portal never returns headers for an unbounded stream, so the call hangs.
The SSE generator is tested directly instead; the live endpoint is verified under
real uvicorn.

## Code Review & Fixes Applied

A first review resolved 7 issues (build config, lazy imports, SSE return type,
public `get_tickers()`, correlation constants, unused imports, test mocks).

A second, comprehensive review on 2026-09-05 (`MARKET_DATA_REVIEW.md`) found 22
further issues and all are now resolved. The consequential ones:

1. **The Massive path was entirely non-functional.** The client read
   `snap.last_trade.timestamp`, which does not exist on `LastTrade` (it is
   `sip_timestamp`). The `AttributeError` was caught by the surrounding handler,
   so every ticker was silently skipped and the cache never filled with a real
   API key. Fixed by extracting `_extract_price()`.
2. **Nanosecond timestamps were divided by 1,000** instead of 1,000,000,000,
   which would have produced dates in the year 50,832.
3. **The tests certified the bug.** Fixtures built from bare `MagicMock`
   manufactured the nonexistent attribute, so all 13 Massive tests passed against
   broken code, and one asserted the wrong unit. Fixtures now use real models.
4. **No `prev_day.close` fallback**, so the watchlist rendered empty overnight,
   pre-market and at weekends — the common case for a demo app.
5. **`stream.py` mutated a module-scope router**, so a second call to the factory
   returned one shared router with duplicate `/prices` routes; FastAPI matched
   the first, which closed over the first cache. The router is now built inside
   the factory.
6. **Tickers were not normalised**, and the two sources disagreed about it, so
   `add_ticker(" aapl ")` created a second cache key that could not be removed.
   Now enforced for both sources via `MarketDataSource.normalize_ticker()`.
7. **Free-tier degradation was designed but not built.** A free Basic key cannot
   call snapshots at all; it now falls back to grouped-daily closes, logged once.

Also fixed: unguarded `np.linalg.cholesky`, `timestamp or time.time()` swallowing
a valid `0.0`, `massive>=1.0.0` understating the real v2.x requirement, global RNG
state (now injectable and seedable), a swallowed `CancelledError`, a missing SSE
heartbeat, in-place mutation of the ticker list under concurrent read, an unclosed
urllib3 pool, and a deprecated event-loop-policy fixture.

## Demo

A Rich terminal demo is available at `backend/market_data_demo.py`:

```bash
cd backend
uv run market_data_demo.py
```

Displays a live-updating dashboard with all 10 tickers, sparklines, color-coded direction arrows, and an event log for notable price moves. Runs 60 seconds or until Ctrl+C.

## Usage for Downstream Code

```python
from app.market import PriceCache, create_market_data_source

# Startup
cache = PriceCache()
source = create_market_data_source(cache)  # Reads MASSIVE_API_KEY
await source.start(["AAPL", "GOOGL", "MSFT", ...])

# Read prices
update = cache.get("AAPL")          # PriceUpdate or None
price = cache.get_price("AAPL")     # float or None
all_prices = cache.get_all()        # dict[str, PriceUpdate]

# Dynamic watchlist (input is normalised: " tsla " -> "TSLA")
await source.add_ticker("TSLA")
await source.remove_ticker("GOOGL")

# Shutdown
await source.stop()
```
