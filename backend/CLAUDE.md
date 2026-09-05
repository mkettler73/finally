# Backend — Developer Guide

## Project Setup

```bash
cd backend
uv sync --extra dev   # Install all dependencies including test/lint tools
```

## Running the App

```bash
uv run uvicorn app.main:app --reload --port 8000
```

`app/main.py` wires the market data subsystem into a FastAPI app: a `lifespan`
context manager creates the shared `PriceCache`, builds the data source via
`create_market_data_source()`, starts it with `DEFAULT_TICKERS` on startup, and
stops it on shutdown. Exposes `GET /api/health` and `GET /api/stream/prices`.

## Market Data API

The market data subsystem lives in `app/market/`. Use these imports:

```python
from app.market import PriceCache, PriceUpdate, MarketDataSource, create_market_data_source
```

### Core Types

- **`PriceUpdate`** — Immutable dataclass: `ticker`, `price`, `previous_price`, `timestamp`, plus properties `change`, `change_percent`, `direction` ("up"/"down"/"flat"), and `to_dict()` for JSON serialization.

- **`PriceCache`** — Thread-safe in-memory store. Key methods:
  - `update(ticker, price, timestamp=None) -> PriceUpdate`
  - `get(ticker) -> PriceUpdate | None`
  - `get_price(ticker) -> float | None`
  - `get_all() -> dict[str, PriceUpdate]`
  - `remove(ticker)`
  - `version` property — monotonic counter, increments on every update (for SSE change detection)

- **`MarketDataSource`** — Abstract interface implemented by `SimulatorDataSource` and `MassiveDataSource`. Lifecycle: `start(tickers)` -> `add_ticker()` / `remove_ticker()` -> `stop()`.
  - Tickers are normalised (stripped, upper-cased) at every boundary via `normalize_ticker()`. Pass user input straight through — `" aapl "` becomes `"AAPL"`.
  - `start()` seeds the cache before returning, so the first SSE frame is never empty.
  - `remove_ticker()` evicts from the cache too; `stop()` is idempotent and safe before `start()`.
  - New implementations must pass `tests/market/test_conformance.py`, which runs the whole contract against every source.

- **`create_market_data_source(cache)`** — Factory. Returns `MassiveDataSource` if `MASSIVE_API_KEY` is set, otherwise `SimulatorDataSource`. A free Massive key cannot call snapshots, so `MassiveDataSource` degrades to end-of-day grouped-daily closes on the first plan/auth refusal.

### SSE Streaming

```python
from app.market import create_stream_router

router = create_stream_router(price_cache)  # Returns a fresh FastAPI APIRouter
# Endpoint: GET /api/stream/prices (text/event-stream)
```

Frames are emitted only when `price_cache.version` changes; an SSE comment
heartbeat every 15s keeps idle connections alive through proxies.

**Do not consume this endpoint through `fastapi.testclient.TestClient`** — its
blocking portal never returns headers for an unbounded stream, so the call hangs
rather than failing. Test `_generate_events` directly (see
`tests/market/test_stream.py`) or drive a real uvicorn process.

### Seed Data

Default tickers: AAPL, GOOGL, MSFT, AMZN, TSLA, NVDA, META, JPM, V, NFLX. Seed prices and per-ticker volatility/drift params are in `app/market/seed_prices.py`.

## Running Tests

```bash
uv run --extra dev pytest -v                    # All tests (151, ~16s)
uv run --extra dev pytest --cov=app             # With coverage (97%)
uv run --extra dev ruff check app/ tests/       # Lint
uv run --extra dev ruff format app/ tests/      # Format
```

Two standing rules for this suite: **never touch the network** (mock the REST
client) and **never sleep for real cadences** (inject the interval).

When mocking an external SDK's models, build them with the real class — e.g.
`TickerSnapshot.from_dict(...)` — not a bare `MagicMock`. A bare `MagicMock`
auto-creates whatever attribute is asked of it, which is how a completely broken
Massive client previously passed 13 green tests.

`GBMSimulator` and `SimulatorDataSource` accept `rng=np.random.default_rng(seed)`
for deterministic assertions.

## Demo

```bash
uv run market_data_demo.py   # Live terminal dashboard with simulated prices
```
