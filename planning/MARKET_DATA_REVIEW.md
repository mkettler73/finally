# Market Data Backend — Code Review

> **Status: all 22 findings resolved on 2026-09-05.** The fixes were applied in
> the order recommended at the bottom of this document. The suite went from
> **78 tests / 92% coverage** to **151 tests / 97%**, with `stream.py` rising
> from 42% to 91%. `ruff check` and `ruff format --check` are both clean, and
> the DeprecationWarnings are gone. This document is kept as the record of what
> was wrong and why; see "Resolution" at the end for verification of the fixed
> state.

**Reviewed:** 2026-09-05
**Scope:** `backend/app/main.py`, `backend/app/market/` (8 modules), `backend/tests/` (7 modules),
`backend/pyproject.toml`, `backend/market_data_demo.py`
**Reference specs:** `PLAN.md` §6/§8/§12, `MARKET_INTERFACE.md`, `MARKET_SIMULATOR.md`, `MASSIVE_API.md`

---

## Verdict

**The simulator path is production-ready. The Massive path is non-functional.**

With no `MASSIVE_API_KEY` — the default and the path every student will exercise — the
subsystem works end-to-end. Verified against a live `uvicorn app.main:app`: the SSE
endpoint returns `200 text/event-stream`, emits the `retry: 1000` preamble, and streams
correct, correlated, cent-level price frames for all ten default tickers.

With a real `MASSIVE_API_KEY`, **the price cache never fills**. Every snapshot is silently
skipped by an exception handler. The failure is invisible at `WARNING` level in aggregate
and produces exactly the same user-visible symptom as an expired API key.

The test suite does not catch this. All 13 Massive tests pass against the broken code
because they build fixtures from bare `MagicMock`, which manufactures whatever attribute
is asked of it — including the one that does not exist. One test actively asserts the
wrong unit conversion.

`MARKET_DATA_SUMMARY.md` currently reads **"Complete, tested, reviewed, all issues
resolved."** That is not accurate and should be corrected; `MARKET_INTERFACE.md` §7
already documents findings 1–3 and 5 below as known-open.

### Test run

```
78 passed, 78 warnings in 27.72s          # uv run --extra dev pytest --cov=app
TOTAL  373 stmts  28 miss  92% coverage
ruff check app/ tests/ market_data_demo.py   →  All checks passed!
ruff format --check                          →  3 files would be reformatted
```

Per-module coverage: `main.py` 100%, `cache.py` 100%, `models.py` 100%, `factory.py` 100%,
`interface.py` 100%, `simulator.py` 98%, `massive_client.py` 94%, **`stream.py` 42%**.

(Note: `MARKET_DATA_SUMMARY.md` claims 73 tests / 84% coverage. The current figures are
78 / 92%.)

---

## Findings

### Critical

#### 1. `LastTrade` has no `timestamp` attribute — every real snapshot is discarded

`backend/app/market/massive_client.py:101-103`

```python
price = snap.last_trade.price
timestamp = snap.last_trade.timestamp / 1000.0     # AttributeError
```

The `massive` model field is `sip_timestamp`. Because `massive` builds models with a
`@modelclass` decorator that assigns only declared attributes, `.timestamp` raises
`AttributeError` rather than returning `None`. That exception is caught by the
`except (AttributeError, TypeError)` at line 110, the ticker is skipped, and the loop
moves on. Ten tickers means ten warnings per poll and an empty cache forever.

Reproduced against `massive` 2.2.0 with a real `TickerSnapshot`, not a mock:

```
WARNING:app.market.massive_client:Skipping snapshot for AAPL:
        'LastTrade' object has no attribute 'timestamp'
REAL-MODEL POLL RESULT -> cache.get('AAPL') = None
```

This is `MASSIVE_API.md` §4 "Trap 2", documented in the research notes before the code
was written.

#### 2. Nanosecond timestamps divided by 1,000

`backend/app/market/massive_client.py:103`

`lastTrade.t` is Unix **nanoseconds** (19 digits); bar timestamps are milliseconds. The
divisor must be `1_000_000_000`. Fixing #1 alone yields `1605192894630916.5` — a
timestamp in the year 50,832 — which would flow straight into the SSE payload and the
frontend's chart axis.

#### 3. `MagicMock` fixtures certify the bug instead of catching it

`backend/tests/market/test_massive.py:11-18`

```python
snap = MagicMock()
snap.last_trade = MagicMock()
snap.last_trade.timestamp = timestamp_ms      # invents a field that cannot exist
```

A bare `MagicMock` auto-creates any attribute, so the fixture silently papers over #1.
Worse, `test_timestamp_conversion` (line 86) asserts `update.timestamp == 1707580800.0`
from a millisecond input — it pins the **wrong** unit as the expected behaviour, so
fixing #2 will break a passing test.

**Fix:** build fixtures with the real model, so the type system does the checking:

```python
from massive.rest.models import TickerSnapshot

def _make_snapshot(ticker, price, sip_ns, prev_close=None):
    d = {"ticker": ticker, "lastTrade": {"p": price, "t": sip_ns}}
    if prev_close is not None:
        d["prevDay"] = {"c": prev_close}
    return TickerSnapshot.from_dict(d)
```

`MagicMock(spec=LastTrade)` is an acceptable second choice. A bare `MagicMock` for an
external SDK's model is not — this finding is the root cause of #1 and #2 surviving to
review, and is the highest-leverage single change in this document.

---

### High

#### 4. No `prev_day.close` fallback — the watchlist is empty outside market hours

`backend/app/market/massive_client.py:99-115`

`MASSIVE_API.md` §4 documents that snapshot data is cleared daily at 3:30 AM ET and
`lastTrade` is `None` until exchanges report. Overnight, pre-market and all weekend,
every ticker would be skipped even after #1 and #2 are fixed. Since this is a demo app
that will most often be run outside US market hours, the fallback is not an edge case —
it is the common path.

**Fix:** extract the parsing into the single testable function `MARKET_INTERFACE.md` §6
already specifies, and call it from the loop:

```python
def _extract_price(snap) -> tuple[float, float] | None:
    """(price_in_dollars, timestamp_in_unix_seconds), or None if unusable."""
    trade = getattr(snap, "last_trade", None)
    if trade is not None and trade.price is not None:
        ns = trade.sip_timestamp
        return float(trade.price), (ns / 1_000_000_000 if ns else time.time())

    prev = getattr(snap, "prev_day", None)
    if prev is not None and prev.close is not None:
        return float(prev.close), time.time()

    return None
```

This resolves #1, #2 and #4 together, in one function, in one file. Then narrow the
`except (AttributeError, TypeError)` — with parsing extracted, a broad catch around the
update loop only hides future bugs.

#### 5. `stream.py` mutates a module-scope router — duplicate, cache-shadowing routes

`backend/app/market/stream.py:17`

```python
router = APIRouter(prefix="/api/stream", tags=["streaming"])   # module scope

def create_stream_router(price_cache: PriceCache) -> APIRouter:
    @router.get("/prices")                                      # mutates the shared object
    async def stream_prices(request: Request): ...
    return router
```

Every call to the factory adds another `/prices` route to the *same* router and returns
it. The docstring's claim that this "lets us inject the PriceCache without globals" is
inverted — the router *is* the global.

Verified:

```
create_stream_router(cache1) is create_stream_router(cache2)  ->  True
routes on the returned router: ['/api/stream/prices', '/api/stream/prices']
```

FastAPI matches the **first** registered route, which closes over the **first** cache.
This already bites the test suite: `tests/test_main.py` reloads `app.main` per test, so
by the third reload the app carries three `/prices` routes and the live one is shadowed
by a dead cache that nothing writes to:

```
stream routes: ['/api/stream/prices', '/api/stream/prices', '/api/stream/prices']
live cache size: 10          # but the matched route reads an empty cache
```

Production imports `main` once, so this is latent today — but it breaks the moment the
app is mounted twice, a second stream router is added, or a test reloads the module. The
existing test `test_stream_route_reads_from_app_price_cache` asserts
`app.state.price_cache is main_module.price_cache`, which does not test the route's
closure at all and passes regardless.

**Fix:** move `router = APIRouter(...)` inside the factory. One line.

#### 6. Tickers are not normalised, and the two sources disagree about it

`MARKET_INTERFACE.md` §3 obligation 5 requires normalisation to stripped upper case at
the boundary. `SimulatorDataSource` never normalises; `MassiveDataSource` normalises in
`add_ticker`/`remove_ticker` but **not** in `start()`.

Verified against the simulator:

```
sim tickers after add_ticker(" aapl "):  ['AAPL', ' aapl ']
cache keys:                              [' aapl ', 'AAPL']
after remove_ticker("aapl"):             ['AAPL', ' aapl ']   # removed neither
```

And against Massive: `start([" tsla "])` yields `[' tsla ']`, which is sent verbatim as a
query parameter to a case-sensitive API.

Once `POST /api/watchlist` is wired up, a user typing `aapl` gets a duplicate row that
they cannot delete, and the position/valuation code will key off a ticker the cache does
not hold. This is the finding most likely to cause a visible bug in the next phase of
work.

**Fix:** normalise once, in one place. The cleanest option is a concrete helper on the
ABC that every implementation routes through, so a third source cannot get it wrong:

```python
@staticmethod
def _normalize(ticker: str) -> str:
    return ticker.upper().strip()
```

Apply it in `start()`, `add_ticker()` and `remove_ticker()` in both sources. Then extend
the tests in #10 to assert it for every implementation.

#### 7. The SSE generator is effectively untested (42% coverage)

`backend/app/market/stream.py:51-87` — lines 62-87 are uncovered. `PLAN.md` §12 and
`MARKET_INTERFACE.md` §10 both call for asserting the `text/event-stream` content type,
the `retry:` preamble, and that unchanged cache state does not re-emit. None of that is
tested. This is the largest coverage gap in the subsystem and it covers the one code path
the entire frontend depends on.

The likely reason it was skipped: **`fastapi.testclient.TestClient` hangs on this
endpoint.** I confirmed a `c.stream("GET", "/api/stream/prices")` call never returns
headers and times out — an artifact of TestClient's blocking portal with an unbounded
stream, not a defect in the endpoint. Under real uvicorn the same endpoint responds
immediately and streams correctly.

The generator is nonetheless directly testable, because it is a plain async generator
with an injectable `interval`. This runs green today and takes milliseconds:

```python
class FakeRequest:
    def __init__(self, ticks): self.client, self._n, self._limit = None, 0, ticks
    async def is_disconnected(self): self._n += 1; return self._n > self._limit

async def test_sse_preamble_and_version_gating():
    cache = PriceCache(); cache.update("AAPL", 190.0)
    frames = [c async for c in _generate_events(cache, FakeRequest(5), interval=0.01)]
    assert frames[0] == "retry: 1000\n\n"
    assert len(frames) == 2          # unchanged cache must not re-emit
    assert json.loads(frames[1].removeprefix("data: "))["AAPL"]["price"] == 190.0
```

Add a content-type/status assertion via a short-lived real uvicorn server in the E2E
suite (`test/`), where a live server is available anyway. Leave a comment in
`test_main.py` recording the TestClient limitation so the next agent does not rediscover
it.

---

### Medium

#### 8. Free-tier degradation is designed but not built

`MARKET_INTERFACE.md` §6 and `MASSIVE_API.md` §9 specify detecting a plan/auth failure on
the first snapshot poll and falling back to `get_grouped_daily_aggs` at a 900 s interval.
`massive_client.py` has no `_mode` state and no grouped-daily path.

A free Stocks Basic key — the only key most students can obtain — cannot call snapshots
at all. Today that produces `logger.error("Massive poll failed: ...")` every 15 seconds
forever and a permanently empty watchlist, with no signal to the user about why. Given
findings #1–#4 this is currently masked, but it becomes the next visible failure the
moment they are fixed.

At minimum, log the 401/403 loudly **once** rather than every cycle, and consider falling
back to the simulator so the app is never left serving nothing.

#### 9. `np.linalg.cholesky` is unguarded

`backend/app/market/simulator.py:172`

`MARKET_SIMULATOR.md` §4 explicitly calls for catching `LinAlgError` and degrading to
independent draws. The current constants are safe — I verified the 10 default tickers and
a 30-ticker stress set both factor cleanly — but a future edit to
`INTRA_TECH_CORR` / `CROSS_GROUP_CORR` would raise inside `__init__`, inside `lifespan`,
and take the whole app down at startup. Degrading to uncorrelated moves is strictly
better than refusing to boot.

```python
try:
    self._cholesky = np.linalg.cholesky(corr)
except np.linalg.LinAlgError:
    logger.warning("Correlation matrix not positive-definite; using independent draws")
    self._cholesky = None
```

`step()` already handles `self._cholesky is None`, so this is a three-line change with no
downstream impact.

#### 10. The interface-conformance suite is missing

`MARKET_INTERFACE.md` §10 calls this "the highest-value test in the subsystem: it is what
guarantees that swapping sources on `MASSIVE_API_KEY` cannot change observable
behaviour." No such test exists. Findings #4 and #6 are both source-asymmetry bugs — the
exact class this suite is designed to catch.

One `@pytest.mark.parametrize`d module run against both sources, asserting the §3
obligations (`start()` seeds the cache before returning; `remove_ticker()` evicts;
`stop()` is idempotent and safe before `start()`; tickers are normalised) would have
caught #6 and would catch the next one.

#### 11. `timestamp or time.time()` swallows a zero timestamp

`backend/app/market/cache.py:30`

```python
ts = timestamp or time.time()
```

Verified: `cache.update("AAPL", 100.0, timestamp=0.0)` stores wall-clock time, not `0.0`.
No caller passes zero today, but this is precisely the guard that would mask a future
unit-conversion bug — the same family as #2. Use `ts = time.time() if timestamp is None
else timestamp`.

#### 12. `massive>=1.0.0` understates the real requirement

`backend/pyproject.toml:11` floors `massive` at 1.0.0, but the code depends on v2.x model
naming (`sip_timestamp`, `prev_day`, `SnapshotMarketType` import path). `uv.lock` pins
2.2.0, so builds are reproducible today, but a lockfile refresh under a wider range or a
non-`uv` install could resolve to something incompatible. Set `massive>=2.2.0` to match
`MASSIVE_API.md` §7.

#### 13. `GBMSimulator` uses global RNG state — no seeded determinism

`simulator.py:84` uses `np.random.standard_normal` and `simulator.py:105` uses the global
`random` module. `MARKET_SIMULATOR.md` §1 lists "deterministic under test" as a
requirement and `MARKET_INTERFACE.md` §10 calls for a "seeded RNG for determinism", but
there is no way to inject one and no determinism test.

`test_prices_change_over_time` (line 68) asserts only `final_price != initial_price` after
1000 steps — a weak assertion that cannot verify the GBM math. Accepting a
`rng: np.random.Generator | None = None` constructor argument would let the suite assert
actual expected prices, and would let `test_prices_are_positive` (which currently runs
10,000 real steps) shrink.

---

### Low

14. **`conftest.py` deprecation.** The `event_loop_policy` fixture returns
    `asyncio.DefaultEventLoopPolicy()`, deprecated and slated for removal in Python 3.16.
    It generates all 78 warnings in the run and returns the policy pytest-asyncio would
    use anyway — the fixture can simply be deleted.

15. **`stream.py:86-87` swallows `CancelledError`.** The `except asyncio.CancelledError`
    logs and returns without re-raising. Re-raise after logging so cancellation
    propagates normally.

16. **No SSE heartbeat.** Frames are emitted only when `price_cache.version` changes. In
    Massive mode that is every 15 s, and 900 s in the (unbuilt) grouped-daily mode. Idle
    connections that long are routinely dropped by proxies and load balancers. Emit
    `": ping\n\n"` every ~15 s when the version is unchanged.

17. **`MassiveDataSource.add_ticker` mutates a list under concurrent read.**
    `self._tickers.append(...)` runs on the event loop while `_fetch_snapshots` may be
    iterating the same list inside `asyncio.to_thread`. Rebind instead —
    `self._tickers = [*self._tickers, ticker]` — matching what `remove_ticker` already
    does correctly.

18. **`PriceCache` lock nits.** `version` reads `_version` outside the lock (benign on
    CPython, but inconsistent with the rest of the class), and `get_price()` acquires the
    lock a second time via `get()`.

19. **`ruff format --check` fails** on `test_models.py`, `test_simulator.py`,
    `test_simulator_source.py`. `ruff check` passes; only formatting drifts.

20. **`MassiveDataSource.stop()` drops `_client` without closing it**, leaking the
    `urllib3` connection pool. Minor in a single-shot process, untidy on repeated
    start/stop.

21. **Python version drift.** `backend/.venv` is Python 3.14 while `PLAN.md` §11 targets
    `python:3.12-slim`. Tests pass on 3.14; ensure CI and the Dockerfile pin the same
    version actually being tested against.

22. **Doc drift.** `MARKET_DATA_SUMMARY.md` reports 73 tests / 84% coverage and states
    "all issues resolved". Actual: 78 tests / 92%, with findings #1–#3 and #5 still open
    and already listed in `MARKET_INTERFACE.md` §7.

---

## What is done well

These are worth preserving as the rest of the platform is built on top:

- **The producer/consumer split is correct and load-bearing.** No consumer holds a
  reference to a `MarketDataSource`; omitting `get_price` from the ABC is what
  structurally enforces that. This is the decision that makes the simulator/Massive swap
  invisible downstream, and it is honoured everywhere in the code.
- **`PriceUpdate` cannot hold inconsistent state.** Frozen, slotted, with `change`,
  `change_percent` and `direction` all derived. `direction` uses strict `>`/`<`, so
  "flat" is genuinely distinct from "down" — exactly what the UI flash animation needs.
- **`PriceCache.update()` computes `previous_price` itself**, which makes tick-to-tick
  direction correct by construction and impossible for a caller to get wrong. First write
  yields `previous_price == price`, so there is no spurious flash on page load.
- **Rounding happens in exactly one place** (`cache.py:36-37`), so the API, the UI and
  trade fills cannot disagree about a price.
- **The version counter is the right primitive** for SSE change detection: O(1), and it
  collapses ~30 redundant frames into one in Massive mode. Verified working — a static
  cache emits exactly one data frame.
- **The GBM math is right.** The `−σ²/2` correction is present, `dt` is derived from a
  *trading* year, and the resulting ~1.2¢ ticks on AAPL land exactly where
  `MARKET_SIMULATOR.md` §3 predicts. Correlated moves via Cholesky are correctly
  implemented, and carving TSLA out of the tech block is a good call.
- **`asyncio.to_thread` around the synchronous `RESTClient`** is correct and easy to get
  wrong.
- **Both loops are wrapped in `try/except Exception`** and cannot die — the single most
  important resilience property for a background feed.
- **The factory `.strip()`es the API key**, so `MASSIVE_API_KEY=` in a `.env` correctly
  selects the simulator rather than constructing a client that 401s forever. This is
  tested (`test_creates_simulator_when_api_key_whitespace`).
- **`main.py` lifespan wiring is clean** — start on boot, stop in `finally`, source and
  cache both on `app.state`, no `try` around `yield` swallowing errors.
- **Cache, model and factory tests are genuinely thorough** (100% coverage each, boundary
  cases included). The problem is confined to the Massive mocks and the SSE gap.

---

## Recommended order of work

| # | Change | Effort |
|---|---|---|
| 1 | Rewrite `test_massive.py` fixtures on `TickerSnapshot.from_dict` (#3) — do this *first*, so it fails and proves the bug | S |
| 2 | Extract `_extract_price()`; fixes `sip_timestamp`, ns→s and the `prev_day` fallback (#1, #2, #4) | S |
| 3 | Move `router = APIRouter(...)` inside `create_stream_router` (#5) | XS |
| 4 | Normalise tickers in `start`/`add`/`remove` on both sources (#6) | S |
| 5 | Add the SSE generator tests via `_generate_events` + `FakeRequest` (#7) | S |
| 6 | Add the parametrised interface-conformance suite (#10) | M |
| 7 | Guard `cholesky` with `LinAlgError` (#9); fix `timestamp or` (#11); bump `massive>=2.2.0` (#12) | XS |
| 8 | Implement grouped-daily free-tier fallback, or log-once-and-degrade (#8) | M |
| 9 | Sweep the Low findings; run `ruff format`; correct `MARKET_DATA_SUMMARY.md` (#14–#22) | S |

Items 1–5 are roughly half a day and take the Massive path from broken to working. Item 6
is what stops this class of divergence recurring when a third source is added.

## Verification performed

- `uv run --extra dev pytest -q --cov=app --cov-report=term-missing` — 78 passed, 92%
- `uv run --extra dev ruff check app/ tests/ market_data_demo.py` — clean
- `uv run --extra dev ruff format --check app/ tests/` — 3 files would be reformatted
- Introspected `massive` 2.2.0 `LastTrade`: confirmed `sip_timestamp` exists, `timestamp`
  raises `AttributeError`, and `prev_day.close` parses from `prevDay.c`
- Drove `MassiveDataSource._poll_once()` with a real `TickerSnapshot` — cache stayed empty
- Called `create_stream_router()` twice — confirmed shared router with duplicate routes
- Reloaded `app.main` twice under `TestClient` — confirmed three `/prices` routes
- Exercised ticker normalisation on both sources — confirmed the duplicate-entry defect
- Factored the correlation matrix for 10 and 30 tickers — both positive-definite today
- Ran a live `uvicorn app.main:app` and `curl -N /api/stream/prices` — confirmed 200,
  `text/event-stream`, `retry: 1000` preamble, and correct streaming price frames
- Ran `_generate_events` directly against a fake request — confirmed the preamble,
  version gating, and payload shape, and that this is a viable test approach

---

## Resolution

All findings were fixed on 2026-09-05.

### Code changes

| File | Change | Findings |
|---|---|---|
| `app/market/massive_client.py` | Extracted `_extract_price()`: `sip_timestamp`, ns→s, `prev_day.close` fallback. Added `_is_plan_or_auth_error()` and a grouped-daily degraded mode with a walk-back over non-trading days. Normalised tickers in `start`/`add`/`remove`; rebind rather than mutate `_tickers`; close the REST client in `stop()`. | 1, 2, 4, 6, 8, 17, 20 |
| `app/market/interface.py` | Added `normalize_ticker()` and `_normalize_all()` to the ABC, so no implementation can get normalisation wrong independently. | 6 |
| `app/market/stream.py` | `APIRouter` built inside the factory; re-raise `CancelledError`; SSE comment heartbeat every 15s when the cache is idle. | 5, 15, 16 |
| `app/market/simulator.py` | `LinAlgError` guard degrading to independent draws; normalisation at the source boundary; injectable `np.random.Generator` replacing global RNG state. | 6, 9, 13 |
| `app/market/cache.py` | `time.time() if timestamp is None else timestamp`; `version` read under the lock; `get_price()` takes the lock once. | 11, 18 |
| `backend/pyproject.toml` | `massive>=2.2.0`; `--strict-markers --strict-config`. | 12 |

### Test changes

| File | Change | Findings |
|---|---|---|
| `tests/market/test_massive.py` | Rewritten (13 → 35 tests). Fixtures built with `TickerSnapshot.from_dict`, so the type system catches a wrong attribute name. Covers ns→s, the `prev_day` fallback, normalisation, degradation, and log-once behaviour. | 3, and the regression guard for 1, 2, 4 |
| `tests/market/test_conformance.py` | **New** (32 tests). One parametrised suite asserting every section 3 obligation against both sources. | 10 |
| `tests/market/test_stream.py` | **New** (11 tests). Preamble, payload shape, version gating, heartbeat, disconnect, cancellation, and single-route registration. | 5, 7, 15, 16 |
| `tests/test_main.py` | Added an end-to-end regression test that boots the app with `MASSIVE_API_KEY` set and a mocked client, asserting the cache actually fills with second-resolution timestamps. | 1, 2, 5 |
| `tests/market/test_cache.py`, `test_simulator.py`, `test_simulator_source.py` | Zero-timestamp preservation; seeded-RNG determinism; global-RNG isolation; Cholesky degradation; normalisation. | 9, 11, 13 |
| `tests/conftest.py` | Removed the deprecated `event_loop_policy` fixture. | 14 |

### Documentation

`MARKET_DATA_SUMMARY.md` status line, test figures and review history corrected;
`MARKET_INTERFACE.md` §7 rewritten from "Divergences" to "Conformance";
`backend/CLAUDE.md` and `backend/README.md` updated (findings 22, and 21 noted).

### Verification of the fixed state

```
uv run --extra dev pytest -q --cov=app        ->  151 passed, 0 warnings, 97%
uv run --extra dev ruff check app/ tests/     ->  All checks passed!
uv run --extra dev ruff format --check        ->  24 files already formatted
```

Per-module coverage: `main.py`, `cache.py`, `models.py`, `factory.py`,
`interface.py`, `seed_prices.py` 100%; `simulator.py` 98%; `massive_client.py`
93%; `stream.py` 91% (was 42%).

Behaviour re-verified outside the suite:

- Live `uvicorn app.main:app` + `curl -N /api/stream/prices` — 200,
  `text/event-stream`, `retry: 1000` preamble, correct streaming price frames;
  `/openapi.json` lists `/api/stream/prices` exactly once.
- `MassiveDataSource._poll_once()` driven with a real `TickerSnapshot` now fills
  the cache at `190.5` with a timestamp in Unix seconds, where before it logged
  `'LastTrade' object has no attribute 'timestamp'` and left the cache empty.

### Not changed

Finding 21 (the venv is Python 3.14 while `PLAN.md` §11 targets
`python:3.12-slim`) is left as-is: the Dockerfile and CI workflow that would pin
it do not exist yet. It is flagged here so that whoever writes the Dockerfile
pins the version the suite is actually green on, or re-runs the suite on 3.12.
