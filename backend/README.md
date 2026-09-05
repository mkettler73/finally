# FinAlly Backend

FastAPI backend for the FinAlly AI Trading Workstation.

## Running the API

```bash
uv run uvicorn app.main:app --reload --port 8000
```

Starts the market data source (simulator by default, or Massive if `MASSIVE_API_KEY` is set) and serves:

- `GET /api/health` - health check
- `GET /api/stream/prices` - SSE stream of live price updates

## Structure

- `app/` - Application code
  - `main.py` - FastAPI app: lifespan-managed market data source, health check, SSE routing
  - `market/` - Market data subsystem
    - `models.py` - PriceUpdate dataclass
    - `cache.py` - Thread-safe price cache
    - `interface.py` - MarketDataSource abstract interface
    - `simulator.py` - GBM-based market simulator
    - `massive_client.py` - Massive/Polygon.io API client
    - `factory.py` - Data source factory
    - `stream.py` - SSE streaming endpoint
    - `seed_prices.py` - Default ticker prices and parameters

- `tests/` - Unit and integration tests
  - `market/` - Market data tests, including `test_conformance.py`, which runs the
    full `MarketDataSource` contract against every implementation

## Running Tests

```bash
# Install dependencies
uv sync --extra dev

# Run all tests
uv run --extra dev pytest

# Run with coverage
uv run pytest --cov=app --cov-report=html

# Run specific test file
uv run pytest tests/market/test_simulator.py

# Run with verbose output
uv run pytest -v
```

## Environment Variables

- `MASSIVE_API_KEY` - Optional. If set, use real market data from Massive API. If not set, use the built-in simulator.

A free Stocks Basic key cannot call the snapshot endpoint. The client detects
that on its first poll and falls back to end-of-day grouped-daily closes
(refreshed every 15 minutes), logging the downgrade once. Prices are then static
session closes rather than a live tape.

## Development

```bash
# Install dependencies
uv sync --dev

# Run linter
uv run ruff check .

# Format code
uv run ruff format .
```
