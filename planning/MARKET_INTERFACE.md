# Market Data Interface Design

**Purpose:** The unified Python API FinAlly uses to retrieve stock prices — backed by the
Massive API when `MASSIVE_API_KEY` is set, and by an in-process simulator otherwise.

**Companion docs:** `MASSIVE_API.md` (the provider research this design is built on) and
`MARKET_SIMULATOR.md` (the simulator implementation).

**Status:** This subsystem exists in `backend/app/market/`. This document is the
specification of record; §7 lists where the current code diverges from it.

---

## 1. Design Goals

| Goal | How it is met |
|---|---|
| One code path for prices, whatever the source | Strategy pattern behind `MarketDataSource` |
| Downstream code never knows which source is live | Everything reads `PriceCache`, never the source |
| Adding a source must not touch consumers | New sources implement the ABC; the factory picks |
| Never block the FastAPI event loop | Blocking SDK calls go through `asyncio.to_thread` |
| A broken feed degrades, it does not crash | Polling loops swallow and log; the cache goes stale |
| Testable without network or wall-clock waits | Injectable interval, injectable RNG seed, mockable client |

### The central decision: producer/consumer via a cache

Data sources **push into** a shared `PriceCache`. Consumers — the SSE endpoint,
portfolio valuation, trade execution, the chat assistant's context builder — **pull
from** that cache. Nothing downstream ever holds a reference to a `MarketDataSource`.

```
                     ┌────────────────────────┐
   MASSIVE_API_KEY ──►  create_market_data_    │
                     │  source(cache)          │
                     └───────────┬────────────┘
                                 │ returns one of
              ┌──────────────────┴──────────────────┐
              ▼                                     ▼
   ┌─────────────────────┐              ┌──────────────────────┐
   │ SimulatorDataSource │              │  MassiveDataSource   │
   │  GBM, 500 ms tick   │              │  REST poll, 15 s     │
   └──────────┬──────────┘              └──────────┬───────────┘
              │            writes                  │
              └───────────────┬────────────────────┘
                              ▼
                   ┌─────────────────────┐
                   │     PriceCache      │  thread-safe, in-memory
                   │  ticker → PriceUpdate│  monotonic version counter
                   └──────────┬──────────┘
                              │ reads
        ┌─────────────────────┼─────────────────────┬──────────────────┐
        ▼                     ▼                     ▼                  ▼
  SSE /api/stream    portfolio valuation     trade execution     LLM context
```

Why this shape rather than letting callers `await source.get_price()`:

- **Decoupled cadences.** The simulator produces every 500 ms; Massive produces every
  15 s; SSE consumes every 500 ms. A cache lets each run at its natural rate.
- **No fan-out amplification.** Ten SSE clients cause zero extra API calls.
- **Trades price off one number.** A trade and the price the user saw on screen read the
  same cache entry, so fills always match the displayed price.
- **Future multi-user for free.** The data layer is already shared, not per-request.

---

## 2. `PriceUpdate` — the wire model

`backend/app/market/models.py`. Immutable, frozen, slotted. Everything derived is a
property, so a `PriceUpdate` can never hold a `change` that disagrees with its prices.

```python
@dataclass(frozen=True, slots=True)
class PriceUpdate:
    ticker: str
    price: float
    previous_price: float
    timestamp: float = field(default_factory=time.time)   # Unix SECONDS

    @property
    def change(self) -> float: ...            # price - previous_price
    @property
    def change_percent(self) -> float: ...     # 0.0 when previous_price == 0
    @property
    def direction(self) -> str: ...            # "up" | "down" | "flat"

    def to_dict(self) -> dict: ...             # JSON payload for SSE
```

**`timestamp` is Unix seconds as a float, always.** Massive supplies nanoseconds for
trades and milliseconds for bars; conversion is the adapter's job and must happen inside
`MassiveDataSource`, never downstream. See `MASSIVE_API.md` §4.

`direction` drives the green/red flash in the UI, so "flat" must be distinct from "down"
— a strict `>` / `<` comparison, not a sign function.

---

## 3. `MarketDataSource` — the abstract interface

`backend/app/market/interface.py`. Six methods, deliberately minimal. Note there is **no
`get_price`** — reading is the cache's job, and omitting it from the ABC is what
structurally prevents downstream code from coupling to a source.

```python
class MarketDataSource(ABC):
    @abstractmethod
    async def start(self, tickers: list[str]) -> None:
        """Begin producing updates into the PriceCache.

        Must populate the cache with an initial price for every ticker BEFORE
        returning, so the first SSE frame is never empty. Call exactly once.
        """

    @abstractmethod
    async def stop(self) -> None:
        """Cancel the background task and release resources. Idempotent."""

    @abstractmethod
    async def add_ticker(self, ticker: str) -> None:
        """Add to the active set. No-op if present. Idempotent."""

    @abstractmethod
    async def remove_ticker(self, ticker: str) -> None:
        """Remove from the active set AND from the cache. No-op if absent."""

    @abstractmethod
    def get_tickers(self) -> list[str]:
        """Current active set. Synchronous — it reads local state only."""
```

### Contract obligations every implementation owes

1. **`start()` seeds the cache synchronously.** The simulator writes its seed prices; the
   Massive source performs one immediate poll before spawning the loop. A user opening
   the page must never see an empty watchlist.
2. **`add_ticker()` should make a price available promptly.** The simulator can seed
   instantly. Massive cannot — the price appears on the next poll, up to 15 s later, so
   the API layer must tolerate a briefly price-less watchlist row. Optionally the Massive
   source may issue a targeted `get_previous_close_agg()` to fill the gap immediately.
3. **`remove_ticker()` must evict from the cache**, not merely stop updating it, or a
   removed ticker lingers in every SSE frame.
4. **The background loop must never die.** Wrap the body in `try/except Exception`, log,
   and continue. A stale cache is a degraded app; a dead loop is a broken one.
5. **Tickers are normalised to upper case, stripped**, at the boundary.
6. **`stop()` is safe to call twice** and safe to call before `start()`.

---

## 4. `PriceCache` — the shared store

`backend/app/market/cache.py`. A dict behind a `threading.Lock`, plus a monotonic
version counter.

```python
class PriceCache:
    def update(self, ticker: str, price: float, timestamp: float | None = None) -> PriceUpdate
    def get(self, ticker: str) -> PriceUpdate | None
    def get_all(self) -> dict[str, PriceUpdate]        # shallow copy
    def get_price(self, ticker: str) -> float | None
    def remove(self, ticker: str) -> None
    @property
    def version(self) -> int
```

Three design points worth stating explicitly:

**Why `threading.Lock` and not `asyncio.Lock`.** The Massive client is synchronous and
runs under `asyncio.to_thread`, so writes genuinely arrive from a worker thread. A
`threading.Lock` is the only correct choice; it is uncontended in practice and the
critical sections are a handful of dict operations.

**Why `update()` computes `previous_price` itself.** Callers pass only the new price. The
cache pairs it with what it already held, which is what makes tick-to-tick direction
correct by construction and impossible for a caller to get wrong. On the first update for
a ticker, `previous_price == price`, so `direction` is `"flat"` — no spurious opening flash.

**Why a version counter.** It gives the SSE generator an O(1) "has anything changed?"
check without diffing the price map. Any write bumps it; the stream re-sends only when it
moves. With the Massive source updating every 15 s, this collapses ~30 redundant SSE
frames into one.

Prices are rounded to 2 decimal places on write, so the cache is the single place
rounding happens and the API, the UI, and trade fills cannot disagree.

---

## 5. `create_market_data_source()` — the switch

`backend/app/market/factory.py`. The entire environment-driven decision lives here, in
one function, so that no other module reads `MASSIVE_API_KEY`.

```python
def create_market_data_source(price_cache: PriceCache) -> MarketDataSource:
    api_key = os.environ.get("MASSIVE_API_KEY", "").strip()
    if api_key:
        logger.info("Market data source: Massive API (real data)")
        return MassiveDataSource(api_key=api_key, price_cache=price_cache)
    logger.info("Market data source: GBM Simulator")
    return SimulatorDataSource(price_cache=price_cache)
```

- `.strip()` matters: `.env` files routinely contain `MASSIVE_API_KEY=` or a key with
  trailing whitespace, and a whitespace-only value must select the simulator, not
  construct a client that will 401 on every poll.
- The source is returned **unstarted**. The caller owns the lifecycle, which keeps the
  factory synchronous and trivially testable.
- The log line is the single most useful diagnostic in the app — it answers "why are my
  prices fake?" without a debugger.

---

## 6. The two implementations

### `SimulatorDataSource`

Thin async wrapper around `GBMSimulator` (fully specified in `MARKET_SIMULATOR.md`). One
`asyncio.Task` loops: step the model, write every ticker to the cache, `await
asyncio.sleep(0.5)`. Add/remove mutate the model in place and rebuild its correlation
matrix. `add_ticker` seeds the cache immediately from the model's starting price.

### `MassiveDataSource`

Adapter over `massive.RESTClient`, poll-based, one API call per cycle regardless of
watchlist size.

```python
class MassiveDataSource(MarketDataSource):
    def __init__(self, api_key: str, price_cache: PriceCache, poll_interval: float = 15.0):
        ...

    async def start(self, tickers: list[str]) -> None:
        self._client = RESTClient(api_key=self._api_key)
        self._tickers = [t.upper().strip() for t in tickers]
        await self._poll_once()                    # seed before serving traffic
        self._task = asyncio.create_task(self._poll_loop(), name="massive-poller")

    async def _poll_loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval)    # sleep first: start() already polled
            await self._poll_once()

    async def _poll_once(self) -> None:
        if not self._tickers or not self._client:
            return
        try:
            # RESTClient is synchronous urllib3 — never call it on the event loop.
            snapshots = await asyncio.to_thread(self._fetch_snapshots)
        except Exception as e:
            logger.error("Massive poll failed: %s", e)
            return                                 # keep stale prices; retry next tick
        for snap in snapshots:
            parsed = _extract_price(snap)
            if parsed is None:
                logger.warning("No usable price for %s", getattr(snap, "ticker", "???"))
                continue
            price, ts = parsed
            self._cache.update(ticker=snap.ticker, price=price, timestamp=ts)

    def _fetch_snapshots(self) -> list[TickerSnapshot]:
        return self._client.get_snapshot_all(
            market_type=SnapshotMarketType.STOCKS,
            tickers=self._tickers,
        )
```

The parsing rules from `MASSIVE_API.md` §4 belong in one small, unit-testable function:

```python
def _extract_price(snap) -> tuple[float, float] | None:
    """(price_in_dollars, timestamp_in_unix_seconds), or None if unusable.

    Prefers the last trade; falls back to the previous close so that overnight,
    pre-market and weekend sessions still show a sensible price.
    """
    trade = getattr(snap, "last_trade", None)
    if trade is not None and trade.price is not None:
        # NOTE: the attribute is sip_timestamp, NOT timestamp, and it is in
        # NANOSECONDS. See MASSIVE_API.md section 4.
        ns = trade.sip_timestamp
        ts = ns / 1_000_000_000 if ns else time.time()
        return float(trade.price), float(ts)

    prev = getattr(snap, "prev_day", None)
    if prev is not None and prev.close is not None:
        return float(prev.close), time.time()

    return None
```

#### Free-tier degradation

A Basic (free) key cannot call snapshots at all — it returns `BadResponse`. Rather than
logging an error every 15 s forever, the source should detect this on the first poll and
switch to the grouped-daily endpoint, which every plan can call:

```python
async def _poll_once(self) -> None:
    if self._mode == "snapshot":
        try:
            await self._poll_snapshot()
            return
        except BadResponse as e:
            if _is_plan_or_auth_error(e):
                logger.warning("Snapshots unavailable on this plan; using end-of-day closes")
                self._mode = "grouped_daily"
                self._interval = 900.0
            else:
                raise
    await self._poll_grouped_daily()
```

In `grouped_daily` mode prices are static closes rather than a live feed. That is the
honest behaviour of a free key, and it is still enough for the portfolio, the positions
table and the heatmap to be correct. The header connection indicator should say so.

---

## 7. Conformance of the current implementation

`backend/app/market/` implements this design. The three blocking divergences
previously listed here — `last_trade.timestamp` instead of `sip_timestamp`, a
`/1000` divisor on a nanosecond field, and the missing `prev_day.close` fallback
— were fixed on 2026-09-05 by extracting `_extract_price()` exactly as written in
§6. See `MARKET_DATA_REVIEW.md` findings #1, #2 and #4.

Also resolved in the same pass:

- `stream.py` now builds its `APIRouter` inside `create_stream_router()`, so
  calling the factory twice no longer returns one shared router carrying
  duplicate `/prices` routes bound to the first cache.
- Ticker normalisation (obligation 5) is enforced for both sources through
  `MarketDataSource.normalize_ticker()` / `_normalize_all()`, applied in
  `start()`, `add_ticker()` and `remove_ticker()`.
- The free-tier degradation described in §6 is implemented: a plan/auth refusal
  on the first snapshot poll switches the source to `get_grouped_daily_aggs`
  at a 900 s interval, logged once rather than every cycle.

The parametrised interface-conformance suite called for in §10 now exists at
`backend/tests/market/test_conformance.py` and runs every obligation in §3
against both sources.

One documented asymmetry remains by design: `SimulatorDataSource.add_ticker()`
seeds the cache instantly, while `MassiveDataSource.add_ticker()` cannot — the
price appears on the next poll, up to 15 s later. This is contract obligation 2,
and the API layer must tolerate a briefly price-less watchlist row.

## 8. Integration with FastAPI

```python
# backend/app/main.py
from contextlib import asynccontextmanager
from app.market import PriceCache, create_market_data_source, create_stream_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    cache = PriceCache()
    source = create_market_data_source(cache)
    tickers = load_watchlist_tickers()             # from SQLite, seeded on first run
    await source.start(tickers)

    app.state.price_cache = cache
    app.state.market_source = source
    try:
        yield
    finally:
        await source.stop()

app = FastAPI(lifespan=lifespan)
app.include_router(create_stream_router(app.state.price_cache))
```

Watchlist mutations must update **both** the database and the live source, in that order,
so a failed write never leaves the two disagreeing:

```python
@router.post("/api/watchlist")
async def add_to_watchlist(body: TickerIn, request: Request):
    ticker = body.ticker.upper().strip()
    db.add_watchlist_ticker(ticker)                       # source of truth
    await request.app.state.market_source.add_ticker(ticker)   # live feed
    return {"ticker": ticker}
```

### SSE

`GET /api/stream/prices` streams the whole cache as one JSON object per event, at ~500 ms,
gated on the cache version:

```
retry: 1000

data: {"AAPL": {"ticker":"AAPL","price":190.42,"previous_price":190.38,
                "timestamp":1757000000.12,"change":0.04,"change_percent":0.021,
                "direction":"up"}, ...}
```

Sending the full map rather than per-ticker deltas keeps the client stateless: each frame
is a complete render. At ten tickers that is ~1 KB per frame, which is inconsequential.
The leading `retry: 1000` makes `EventSource` reconnect after 1 s. The generator polls
`request.is_disconnected()` each iteration so a closed tab does not leak a task.

---

## 9. File Structure

```
backend/app/market/
├── __init__.py          # public surface: PriceUpdate, PriceCache, MarketDataSource,
│                        # create_market_data_source, create_stream_router
├── models.py            # PriceUpdate
├── interface.py         # MarketDataSource ABC
├── cache.py             # PriceCache
├── seed_prices.py       # seed prices, GBM params, correlation groups
├── simulator.py         # GBMSimulator + SimulatorDataSource
├── massive_client.py    # MassiveDataSource (+ _extract_price)
├── factory.py           # create_market_data_source
└── stream.py            # SSE router factory
```

Import direction is strictly one-way — `models` ← `cache` ← {`simulator`,
`massive_client`} ← `factory` — so there are no cycles and `models`/`cache` can be tested
in complete isolation.

Consumers import only from the package root:

```python
from app.market import PriceCache, create_market_data_source
```

---

## 10. Testing Strategy

| Target | Approach |
|---|---|
| `PriceUpdate` | Pure property assertions; `direction` at the up/down/flat boundaries; `change_percent` when `previous_price == 0` |
| `PriceCache` | First-write flat behaviour; version monotonicity; `get_all()` returns a copy; concurrent writes from threads |
| `GBMSimulator` | Seeded RNG for determinism; prices stay positive; correlation matrix stays positive-definite as tickers are added and removed |
| `SimulatorDataSource` | Short `update_interval` (e.g. 0.01) so tests take milliseconds; assert cache fills and `stop()` cancels cleanly |
| `MassiveDataSource` | Mock `RESTClient` entirely — **no network in tests**. Assert the ns→s conversion, the `prev_day` fallback, that a raising client does not kill the loop, and that one poll produces one API call regardless of ticker count |
| Interface conformance | One parametrised suite run against both sources, asserting the §3 contract |
| SSE | `httpx.AsyncClient` against the app; assert `text/event-stream`, the `retry:` preamble, and that identical cache state does not re-emit |

The interface-conformance suite is the highest-value test in the subsystem: it is what
guarantees that swapping sources on `MASSIVE_API_KEY` cannot change observable behaviour.

Two rules for keeping the suite fast and honest: **never sleep for real cadences**
(inject the interval) and **never touch the network** (inject or patch the client).

---

## 11. Extension Points

- **A third source** (Alpaca, IEX, a CSV replay for demos) is a new `MarketDataSource`
  subclass plus one branch in the factory. Nothing downstream changes.
- **WebSocket ingestion** replaces the poll loop with a subscription that writes to the
  same cache. `MASSIVE_API.md` §8 covers why this is deferred.
- **Historical bars** for the detail chart are a separate read-path concern
  (`get_aggs()`), not a `MarketDataSource` responsibility. Keep it out of this interface.
- **Multi-user** needs no change here: the cache is already global and per-ticker, and
  the watchlist union across users is what gets polled.
