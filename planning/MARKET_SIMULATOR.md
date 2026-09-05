# Market Simulator Design

**Purpose:** How FinAlly generates realistic-looking live stock prices with no API key,
no network, and no external dependencies — the default market data source.

**Companion docs:** `MARKET_INTERFACE.md` (the `MarketDataSource` contract this
implements) and `MASSIVE_API.md` (the real-data alternative).

---

## 1. Why a Simulator Is the Default

A student clones the repo, runs one Docker command, and must see a trading terminal with
prices moving. Requiring a market data account before anything renders would sink that
experience. So the simulator is not a fallback — it is the **primary** experience, and
Massive is the optional upgrade.

That framing sets the bar. The simulator has to be good enough that a viewer cannot tell
at a glance that it is fake:

| Requirement | Why it matters |
|---|---|
| Prices move at a plausible magnitude | Cent-level ticks, not dollar-level jumps |
| Prices stay positive, always | A negative stock price destroys the illusion instantly |
| Related tickers move together | Ten independent random walks looks obviously synthetic |
| Something dramatic happens occasionally | A flat tape is boring to demo |
| Starting prices are recognisable | AAPL near $190 reads as real; AAPL at $12.34 does not |
| It is cheap | It runs every 500 ms forever inside the API process |
| It is deterministic under test | Seeded RNG, or the test suite is flaky |

---

## 2. The Model: Geometric Brownian Motion

GBM is the standard model for equity prices and the foundation of Black–Scholes. It is
the right choice here because it is one line of arithmetic, is guaranteed to produce
positive prices, and its two parameters map onto quantities that are actually published
for real stocks.

The exact-solution discrete form (not an Euler approximation — this is exact for GBM,
so the step size cannot introduce drift error):

```
S(t+dt) = S(t) · exp( (μ − σ²/2)·dt  +  σ·√dt·Z )
```

| Symbol | Meaning | Source |
|---|---|---|
| `S(t)` | current price | simulator state |
| `μ` | annualised drift (expected return) | `TICKER_PARAMS` |
| `σ` | annualised volatility | `TICKER_PARAMS` |
| `dt` | time step as a fraction of a trading year | derived, see §3 |
| `Z` | standard normal draw, correlated across tickers | §4 |

Two properties earn GBM its place:

- **Positivity by construction.** The price is multiplied by `exp(...)`, which is always
  positive. No clamping, no `max(0.01, price)` hack, no special cases.
- **The `−σ²/2` term.** Without it, `E[S(t)] = S(0)·exp((μ + σ²/2)t)` — volatility alone
  would push the expected price upward. The correction makes `μ` mean what it says, so a
  high-volatility ticker like TSLA does not silently drift to the moon.

---

## 3. Choosing `dt`

`μ` and `σ` are quoted per year, so `dt` must be a 500 ms tick expressed as a fraction of
a **trading** year — not a calendar year. Markets are open 6.5 hours a day, 252 days a
year:

```python
TRADING_SECONDS_PER_YEAR = 252 * 6.5 * 3600   # 5,896,800
DEFAULT_DT = 0.5 / TRADING_SECONDS_PER_YEAR   # ≈ 8.48e-8
```

This tiny `dt` is exactly what makes the tape look right. Working the numbers for AAPL
(σ = 0.22, price ≈ $190):

| Horizon | Ticks | Std. dev. of return | Dollar move on $190 |
|---|---|---|---|
| One tick (500 ms) | 1 | 0.0064% | ~1.2¢ |
| One minute | 120 | 0.070% | ~13¢ |
| One hour | 7,200 | 0.54% | ~$1.03 |
| One session (6.5 h) | 46,800 | 1.39% | ~$2.63 |

The per-tick move lands just above the 1-cent rounding boundary, which is the sweet spot:
prices change on most ticks (so the flash animation fires and the tape feels alive) but
never jump. And the session-level figure round-trips correctly —
`1.39% × √252 ≈ 22%`, recovering the annualised σ we put in. The parameters mean what
they claim at every timescale.

---

## 4. Correlated Moves

Ten independent random walks look wrong. Real tech stocks rise and fall together, and a
viewer notices the absence of that structure even without being able to name it.

The fix is to draw correlated normals via **Cholesky decomposition**. Given a correlation
matrix `C`, factor it as `C = L·Lᵀ` (lower-triangular `L`). Then for a vector `z` of
independent standard normals, `L·z` has covariance `L·I·Lᵀ = C` — correlated draws, at
the cost of one matrix-vector product per tick.

```python
self._cholesky = np.linalg.cholesky(corr)   # once, on ticker add/remove
...
z_independent = np.random.standard_normal(n)          # every tick
z_correlated  = self._cholesky @ z_independent
```

### The correlation structure

```python
CORRELATION_GROUPS = {
    "tech":    {"AAPL", "GOOGL", "MSFT", "AMZN", "META", "NVDA", "NFLX"},
    "finance": {"JPM", "V"},
}

INTRA_TECH_CORR    = 0.6   # tech names move together
INTRA_FINANCE_CORR = 0.5   # banks move together
CROSS_GROUP_CORR   = 0.3   # sector-to-sector, and any unknown ticker
TSLA_CORR          = 0.3   # TSLA is in tech but does its own thing
```

TSLA is deliberately carved out of the tech block. It is the app's high-drama ticker
(σ = 0.50), and letting it drag the entire tech sector with it on every move would make
the whole watchlist swing as one. Giving it cross-group correlation lets it spike alone.

The matrix is rebuilt on every add/remove. That is O(n²) to construct plus O(n³) to
factor, but n is under 50 and it happens only on a watchlist edit — never in the hot path.

### Positive-definiteness

`np.linalg.cholesky` raises `LinAlgError` on a non-positive-definite matrix. The block
structure above is safe (every cross-block coefficient is ≤ every intra-block one), but a
future edit to the constants could break it and take the app down on startup. Guard it:

```python
try:
    self._cholesky = np.linalg.cholesky(corr)
except np.linalg.LinAlgError:
    logger.warning("Correlation matrix not positive-definite; using independent draws")
    self._cholesky = None      # step() falls back to uncorrelated normals
```

Degrading to independent moves is far better than refusing to start.

---

## 5. Random Events

GBM alone produces a smooth, slightly dull tape. Real markets have jumps — earnings,
downgrades, headlines. A small jump process supplies the drama:

```python
if random.random() < self._event_prob:            # 0.001 per ticker per tick
    shock_magnitude = random.uniform(0.02, 0.05)  # 2% – 5%
    shock_sign = random.choice([-1, 1])
    self._prices[ticker] *= 1 + shock_magnitude * shock_sign
```

Calibration: 10 tickers × 2 ticks/second × 0.001 = **0.02 events per second, or one
roughly every 50 seconds.** Frequent enough that a demo watcher sees several, rare enough
that it still reads as an event rather than as noise.

The shock is a **permanent level shift**, not a spike that mean-reverts. GBM has no
mean reversion, so the price simply continues its walk from the new level — which is how
a real repricing behaves.

Sizing matters. 2–5% is large enough to be unmistakable on a sparkline and to flip a
position's P&L sign, and small enough not to look like a data error.

---

## 6. Seed Prices and Per-Ticker Parameters

`backend/app/market/seed_prices.py`.

```python
SEED_PRICES = {
    "AAPL": 190.00, "GOOGL": 175.00, "MSFT": 420.00, "AMZN": 185.00,
    "TSLA": 250.00, "NVDA": 800.00, "META": 500.00, "JPM":  195.00,
    "V":    280.00, "NFLX": 600.00,
}
```

Recognisable prices are load-bearing for believability, and the spread of magnitudes
($175 to $800) is itself useful: it exercises the portfolio heatmap's weighting and makes
the positions table look like a real book.

```python
TICKER_PARAMS = {
    "AAPL":  {"sigma": 0.22, "mu": 0.05},
    "GOOGL": {"sigma": 0.25, "mu": 0.05},
    "MSFT":  {"sigma": 0.20, "mu": 0.05},
    "AMZN":  {"sigma": 0.28, "mu": 0.05},
    "TSLA":  {"sigma": 0.50, "mu": 0.03},   # the drama ticker
    "NVDA":  {"sigma": 0.40, "mu": 0.08},   # volatile, strong drift
    "META":  {"sigma": 0.30, "mu": 0.05},
    "JPM":   {"sigma": 0.18, "mu": 0.04},   # a bank: quiet
    "V":     {"sigma": 0.17, "mu": 0.04},   # payments: quietest
    "NFLX":  {"sigma": 0.35, "mu": 0.05},
}
DEFAULT_PARAMS = {"sigma": 0.25, "mu": 0.05}
```

The volatility spread from 0.17 (V) to 0.50 (TSLA) is roughly 3×, and it is visible in
the UI: the V sparkline is nearly flat while TSLA's is jagged. That contrast is what
makes the watchlist look like a real market rather than ten copies of one process.

Drifts are kept small and mildly positive. A large `μ` would make every position
profitable within minutes and rob the P&L chart of any tension.

### Unknown tickers

A ticker not in the tables — the user adds `PYPL`, or the AI assistant does — gets
`DEFAULT_PARAMS` and a starting price of `random.uniform(50.0, 300.0)`.

This is a known, accepted limitation: added tickers start at an unrealistic price. The
alternatives are worse for a zero-dependency default (bundling a price table goes stale;
fetching a real quote needs the API key we do not have in this mode). If it ever grates,
the cheapest improvement is a larger static seed table covering the S&P 100.

---

## 7. Code Structure

Two classes with a deliberate split of responsibilities.

### `GBMSimulator` — the model, fully synchronous

Pure computation. No asyncio, no cache, no I/O — which is what makes it directly
testable with a seeded RNG.

```python
class GBMSimulator:
    TRADING_SECONDS_PER_YEAR = 252 * 6.5 * 3600
    DEFAULT_DT = 0.5 / TRADING_SECONDS_PER_YEAR

    def __init__(self, tickers: list[str], dt: float = DEFAULT_DT,
                 event_probability: float = 0.001) -> None: ...

    def step(self) -> dict[str, float]:
        """Advance every ticker one time step. Returns {ticker: new_price}.

        Hot path — runs every 500 ms. One numpy draw, one matvec, one loop.
        """

    def add_ticker(self, ticker: str) -> None:      # rebuilds Cholesky
    def remove_ticker(self, ticker: str) -> None:   # rebuilds Cholesky
    def get_price(self, ticker: str) -> float | None
    def get_tickers(self) -> list[str]
```

State is three parallel dicts keyed by ticker (`_prices`, `_params`) plus an ordered
`_tickers` list. **The list order defines the row/column order of the correlation matrix**,
so `step()` can zip `self._tickers` against the correlated draw vector by index. Any code
that mutates `_tickers` must rebuild `_cholesky` in the same breath.

`step()` returns prices rounded to 2 dp, matching what the cache and the UI will show.

### `SimulatorDataSource` — the async adapter

Implements `MarketDataSource`. Owns the task and the cache; owns no maths.

```python
class SimulatorDataSource(MarketDataSource):
    def __init__(self, price_cache: PriceCache, update_interval: float = 0.5,
                 event_probability: float = 0.001) -> None: ...

    async def start(self, tickers: list[str]) -> None:
        self._sim = GBMSimulator(tickers, event_probability=self._event_prob)
        for ticker in tickers:                       # seed so SSE is never empty
            price = self._sim.get_price(ticker)
            if price is not None:
                self._cache.update(ticker=ticker, price=price)
        self._task = asyncio.create_task(self._run_loop(), name="simulator-loop")

    async def _run_loop(self) -> None:
        while True:
            try:
                if self._sim:
                    for ticker, price in self._sim.step().items():
                        self._cache.update(ticker=ticker, price=price)
            except Exception:
                logger.exception("Simulator step failed")   # log, never die
            await asyncio.sleep(self._interval)
```

Three details that matter:

1. **`start()` seeds the cache before returning.** The first SSE frame carries real
   prices; the watchlist never renders blank.
2. **The loop catches broadly and continues.** A bug in `step()` degrades the app to a
   frozen tape rather than killing the background task silently.
3. **`update_interval` is injectable.** Tests pass `0.01` and finish in milliseconds
   instead of sleeping for real cadences.

`add_ticker()` seeds the new ticker's price into the cache immediately, so a
watchlist addition shows a price on the very next SSE frame — the behaviour the Massive
source cannot match (see `MARKET_INTERFACE.md` §3, contract obligation 2).

### Why the split

`GBMSimulator` can be stepped ten thousand times in a unit test with a fixed seed and
asserted on statistically. `SimulatorDataSource` can be tested for lifecycle behaviour
with a stub model. Neither test needs the other half. Fusing them would force every maths
assertion to go through an event loop.

---

## 8. Testing

| Property | Test |
|---|---|
| Prices stay positive | Step 10,000 times with an extreme σ; assert `all(p > 0)` |
| Volatility is calibrated | Seeded run; the realised std. dev. of log returns matches `σ√dt` within tolerance |
| Drift is unbiased | With `σ = 0`, the price follows `S₀·exp(μ·dt·n)` exactly |
| Correlation is real | Seeded run; sample correlation of tech log-returns is near 0.6, tech-vs-finance near 0.3 |
| Events fire at the right rate | `event_probability=1.0` forces one every tick; `0.0` forces none |
| Add/remove is consistent | Matrix dimension tracks ticker count; existing prices survive |
| Cholesky stays valid | Add and remove many tickers in sequence; no `LinAlgError` |
| Unknown tickers work | Add `ZZZZ`; it gets `DEFAULT_PARAMS` and a price in range |
| Lifecycle | `start()` seeds the cache, the loop fills it, `stop()` cancels cleanly and is idempotent |
| The loop survives errors | Patch `step()` to raise; assert the task is still alive after several intervals |

Seed the RNG in tests — both `random.seed()` and `np.random.seed()`, since the module
uses both — or the statistical assertions will flake.

---

## 9. Tuning Knobs

Everything worth adjusting is a constructor argument or a module constant, so tuning
needs no code surgery:

| Knob | Where | Effect |
|---|---|---|
| `update_interval` | `SimulatorDataSource` | Tick rate. 0.5 s is matched to the SSE cadence |
| `event_probability` | both classes | Drama frequency. 0.001 ≈ one event / 50 s at 10 tickers |
| `dt` | `GBMSimulator` | Time compression. Raising it accelerates the market |
| `sigma`, `mu` | `TICKER_PARAMS` | Per-ticker character |
| Correlation constants | `seed_prices.py` | How tightly the watchlist moves as one |
| `SEED_PRICES` | `seed_prices.py` | Starting levels |

To make a demo more eventful without touching the maths, raise `event_probability` to
`0.005` (one event every ~10 s) or raise `dt` by a factor of 10 (a session's worth of
movement in 40 minutes). Prefer `dt` — it accelerates the market coherently rather than
just adding jumps.

---

## 10. Known Limitations

Stated plainly, because each is a deliberate trade rather than an oversight:

- **No mean reversion.** Over a long-running container a ticker can wander far from its
  seed price. Acceptable for a session-length demo; an Ornstein–Uhlenbeck term would fix
  it at the cost of the model's simplicity.
- **No volume, bid/ask, or order book.** FinAlly is market-orders-only with instant fill
  at the cache price, so none of it is needed.
- **No market hours.** Prices move at 3 AM on a Sunday. Gating on a trading calendar
  would leave the app looking broken most of the time a student runs it.
- **No overnight gaps, earnings, or intraday volatility patterns.** Real markets are
  most volatile at the open and close; the simulator is uniform.
- **Unknown tickers start at a random price** (§6).
- **Correlation is static and sector-based.** Real correlations move, and spike toward 1
  in a crash. Modelling that is out of scope.

None of these are visible in a five-minute demo, which is the design target.
