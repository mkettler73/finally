# Massive API Reference (formerly Polygon.io)

**Purpose:** Everything FinAlly needs to fetch real-time and end-of-day prices for
multiple tickers from Massive. This is the research input for `MARKET_INTERFACE.md`.

**Researched:** September 2026, against the live docs at `massive.com/docs` and the
`massive` Python package **v2.2.0** as installed in `backend/.venv`.

---

## 1. Overview

Polygon.io rebranded to **Massive** in early 2026. The platform, the API surface, and
existing API keys are unchanged — only the domain and the Python package name moved.

| | Value |
|---|---|
| Base URL | `https://api.massive.com` |
| Docs | https://massive.com/docs |
| Machine-readable doc index | https://massive.com/docs/llms.txt |
| Python package | `massive` (PyPI), v2.2.0 |
| Auth | `Authorization: Bearer <API_KEY>` request header |
| Default env var | `MASSIVE_API_KEY` |
| Dashboard / keys | https://massive.com/dashboard/keys |

The legacy `api.polygon.io` host and the `polygon-api-client` package still work, but
new code should use `api.massive.com` and `massive`.

### Fortunate coincidence

The Massive Python client reads its key from the environment variable `MASSIVE_API_KEY`
by default — the exact name FinAlly's `PLAN.md` already specifies. Verified in
`massive/rest/__init__.py`:

```python
BASE = "https://api.massive.com"
ENV_KEY = "MASSIVE_API_KEY"

class RESTClient(AggsClient, ..., SnapshotClient, ...):
    def __init__(self, api_key: Optional[str] = os.getenv(ENV_KEY), ...):
```

FinAlly still passes the key explicitly to the constructor so the factory controls
which source is selected rather than relying on implicit environment pickup.

---

## 2. Plans, Rate Limits, and What This Means for FinAlly

This is the single most important section for FinAlly's design, because **the endpoint
you would naturally reach for is not available on the free tier.**

| Plan | Price | Rate limit | Data recency | Snapshots? |
|---|---|---|---|---|
| **Stocks Basic** | Free | **5 calls/min** | End-of-day, 2 yrs history | ❌ **No** |
| Stocks Starter | ~$29/mo | Unlimited | 15-min delayed, 5 yrs | ✅ Yes (delayed) |
| Stocks Developer | ~$79/mo | Unlimited | 15-min delayed, 10 yrs | ✅ Yes (delayed) |
| Stocks Advanced | ~$199/mo | Unlimited | **Real-time**, 20+ yrs | ✅ Yes (real-time) |
| Stocks Business | Custom | Unlimited | Real-time | ✅ Yes (real-time) |

> Prices are indicative and were gathered from third-party summaries; confirm current
> figures at https://massive.com/pricing. The **tier-to-endpoint availability** below
> comes from the official endpoint docs and is the part that matters.

### Consequences

1. **Free (Basic) keys cannot call the snapshot endpoints.** A free key hitting
   `/v2/snapshot/...` returns an authorization error, not data.
2. **Aggregate endpoints are available on every plan, including Basic.** In particular
   the *Daily Market Summary* (grouped daily) endpoint returns **every US ticker for a
   given date in one call** — the ideal end-of-day, multi-ticker fetch for a free key.
3. **"Real-time" is only genuinely real-time on Advanced and above.** Starter and
   Developer are 15-minute delayed. For a demo trading app this is visually
   indistinguishable from live, but it must not be described as live tick data.
4. **5 calls/min on Basic** means a polling interval of **≥ 15 seconds**, and a design
   that costs *one call per poll regardless of watchlist size*. Both of FinAlly's chosen
   endpoints satisfy this.

---

## 3. Endpoint Catalogue

The stocks REST surface is large (aggregates, corporate actions, filings, fundamentals,
market operations, news, snapshots, technical indicators, tickers, trades & quotes). The
full index is at https://massive.com/docs/llms.txt. FinAlly needs only these:

| # | Purpose | Path | Plans |
|---|---|---|---|
| 1 | Live prices, many tickers, one call | `GET /v2/snapshot/locale/us/markets/stocks/tickers` | Starter+ |
| 2 | EOD prices, all tickers, one call | `GET /v2/aggs/grouped/locale/us/market/stocks/{date}` | **All incl. Basic** |
| 3 | Previous close, one ticker | `GET /v2/aggs/ticker/{ticker}/prev` | All incl. Basic |
| 4 | Historical bars for charts | `GET /v2/aggs/ticker/{ticker}/range/{mult}/{span}/{from}/{to}` | All incl. Basic |
| 5 | Live price, single ticker | `GET /v2/snapshot/locale/us/markets/stocks/tickers/{ticker}` | Starter+ |
| 6 | Is the market open? | `GET /v1/marketstatus/now` | All |

Endpoints 1 and 2 are the two that FinAlly's market data source actually polls;
3–6 are useful supporting calls.

---

## 4. Endpoint 1 — Full Market Snapshot (primary, real-time path)

```
GET /v2/snapshot/locale/us/markets/stocks/tickers?tickers=AAPL,MSFT,NVDA
```

**One HTTP call returns the current state of every requested ticker.** This is why it is
FinAlly's primary endpoint: watchlist size does not affect API cost.

### Query parameters

| Parameter | Type | Required | Notes |
|---|---|---|---|
| `tickers` | string | No | Case-sensitive comma-separated list. Omit to get all 10,000+ US tickers. |
| `include_otc` | boolean | No | Default `false`. |

### Response shape

```json
{
  "count": 1,
  "status": "OK",
  "tickers": [
    {
      "ticker": "BCAT",
      "day":     { "o": 20.64, "h": 20.64, "l": 20.506, "c": 20.506, "v": 37216, "vw": 20.616 },
      "prevDay": { "o": 20.79, "h": 21.0,  "l": 20.5,   "c": 20.63,  "v": 292738, "vw": 20.6939 },
      "min":     { "o": 20.506, "h": 20.506, "l": 20.506, "c": 20.506,
                   "v": 5000, "vw": 20.5105, "av": 37216, "n": 1, "t": 1684428600000 },
      "lastTrade": { "p": 20.506, "s": 2416, "t": 1605192894630916600, "x": 4,
                     "i": "71675577320245", "c": [14, 41] },
      "lastQuote":  { "P": 20.6, "S": 22, "p": 20.5, "s": 13, "t": 1605192959994246100 },
      "todaysChange": -0.124,
      "todaysChangePerc": -0.601,
      "updated": 1605192894630916600
    }
  ]
}
```

### Field decoder

Massive uses terse single-letter keys throughout. The Python client renames them, so the
mapping matters when reading raw JSON or writing tests:

**Bar objects** (`day`, `prevDay`, `min`):

| Key | Meaning | Python attribute |
|---|---|---|
| `o` `h` `l` `c` | open / high / low / close | `.open` `.high` `.low` `.close` |
| `v` | volume | `.volume` |
| `vw` | volume-weighted average price | `.vwap` |
| `n` | number of transactions | `.transactions` |
| `t` | timestamp, Unix **milliseconds** | `.timestamp` |
| `av` | accumulated day volume (`min` only) | `.accumulated_volume` |

**`lastTrade`** → `massive.rest.models.LastTrade`:

| Key | Meaning | Python attribute |
|---|---|---|
| `p` | **price** | `.price` |
| `s` | size (shares) | `.size` |
| `t` | SIP timestamp, Unix **nanoseconds** | **`.sip_timestamp`** |
| `x` | exchange id | `.exchange` |
| `i` | trade id | `.id` |
| `c` | condition codes | `.conditions` |

**`lastQuote`** → `LastQuote`: `P`/`S` are **ask** price/size, `p`/`s` are **bid**
price/size, `t` is the SIP timestamp in nanoseconds.

**Top level:** `todaysChange` (dollars vs. prior close), `todaysChangePerc` (percent),
`updated` (Unix **nanoseconds**), `fmv` (fair market value, Business plans only).

### ⚠️ Two traps in this response

**Trap 1 — timestamps are nanoseconds, and the units are not uniform.**
`lastTrade.t`, `lastQuote.t`, and `updated` are Unix **nanoseconds** (19 digits), while
bar timestamps (`day.t`, `min.t`) are Unix **milliseconds** (13 digits). Python's
`time.time()` is seconds. Convert deliberately:

```python
timestamp_seconds = snapshot.last_trade.sip_timestamp / 1_000_000_000  # ns → s
minute_bar_seconds = snapshot.min.timestamp / 1_000                    # ms → s
```

**Trap 2 — `LastTrade` has no attribute called `timestamp`.**
The model field is `sip_timestamp`. Because `massive` builds its models with a custom
`@modelclass` decorator that only assigns declared attributes, `snapshot.last_trade.timestamp`
raises `AttributeError` rather than returning `None`. If that access sits inside a
`try/except AttributeError`, **every ticker is silently skipped and the cache never
fills** — a failure mode that looks exactly like "the API returned nothing".

> This exact bug is present today in `backend/app/market/massive_client.py`, which reads
> `snap.last_trade.timestamp / 1000.0`. See `MARKET_INTERFACE.md` §7 for the fix.

Confirmed empirically against `massive` v2.2.0:

```
>>> snap = TickerSnapshot.from_dict({"ticker": "AAPL",
...     "lastTrade": {"p": 190.5, "t": 1605192894630916600}})
>>> snap.last_trade.timestamp
AttributeError: 'LastTrade' object has no attribute 'timestamp'
>>> snap.last_trade.sip_timestamp / 1_000_000_000
1605192894.6309166          # correct: Nov 2020
>>> snap.last_trade.sip_timestamp / 1000.0
1605192894630916.5          # the /1000 divisor: the year 50,832
```

### Snapshot data lifecycle

Snapshot data is **cleared daily at 3:30 AM ET** and repopulates as exchanges report,
starting as early as 4:00 AM ET. Between the clear and the first trades, `lastTrade` may
be `None` for a ticker. Overnight and at weekends, expect `day` to be empty and `prevDay`
to hold the last real session — so **`prevDay.c` is the correct fallback price** when
`lastTrade` is missing.

### Python

```python
from massive import RESTClient
from massive.rest.models import SnapshotMarketType

client = RESTClient(api_key="YOUR_KEY")  # or omit to read MASSIVE_API_KEY

snapshots = client.get_snapshot_all(
    market_type=SnapshotMarketType.STOCKS,
    tickers=["AAPL", "GOOGL", "MSFT", "NVDA"],
)

for snap in snapshots:
    price = snap.last_trade.price if snap.last_trade else None
    if price is None and snap.prev_day:
        price = snap.prev_day.close          # overnight / pre-open fallback
    print(f"{snap.ticker:6} {price:>9.2f}  {snap.todays_change_percent:+.2f}%")
```

Signature (from `massive/rest/snapshot.py`):

```python
def get_snapshot_all(
    self,
    market_type: Union[str, SnapshotMarketType],
    tickers: Optional[Union[str, List[str]]] = None,
    include_otc: Optional[bool] = None,
    params: Optional[Dict[str, Any]] = None,
    raw: bool = False,
    options: Optional[RequestOptionBuilder] = None,
) -> Union[List[TickerSnapshot], HTTPResponse]: ...
```

---

## 5. Endpoint 2 — Daily Market Summary (end-of-day, free-tier path)

```
GET /v2/aggs/grouped/locale/us/market/stocks/2026-09-03?adjusted=true
```

**Available on every plan including free Basic**, and like the snapshot it returns
**all tickers in a single call** — roughly 10,000 results. Perfect for a 5-calls/min key.

### Query parameters

| Parameter | Type | Required | Notes |
|---|---|---|---|
| `date` (path) | `YYYY-MM-DD` | Yes | The trading day. |
| `adjusted` | boolean | No | Split-adjusted. Default `true`. |
| `include_otc` | boolean | No | Default `false`. |

### Response

```json
{
  "adjusted": true,
  "queryCount": 3,
  "resultsCount": 3,
  "status": "OK",
  "results": [
    { "T": "VSAT", "o": 34.9, "h": 35.47, "l": 34.21, "c": 34.24,
      "v": 312583, "vw": 34.4736, "n": 4966, "t": 1602705600000 }
  ]
}
```

`T` is the ticker (note the capital); the rest matches the bar decoder in §4. `t` is Unix
**milliseconds**. Coverage starts 2003-09-10.

### Python

```python
from datetime import date, timedelta

def fetch_eod_closes(client, tickers: set[str], on: date) -> dict[str, float]:
    """One API call → closing price for every requested ticker."""
    bars = client.get_grouped_daily_aggs(str(on), adjusted=True)
    return {b.ticker: b.close for b in bars if b.ticker in tickers}

# Weekends and holidays return an empty result set — walk backwards.
def latest_eod_closes(client, tickers: set[str], max_lookback: int = 7):
    day = date.today()
    for _ in range(max_lookback):
        closes = fetch_eod_closes(client, tickers, day)
        if closes:
            return closes, day
        day -= timedelta(days=1)
    raise RuntimeError("No trading day found in lookback window")
```

**A non-trading date is not an error** — it returns `status: "OK"` with zero results.
Any caller must walk backwards through the calendar rather than treating the empty
response as a failure.

---

## 6. Endpoints 3–6 — Supporting Calls

### Previous Day Bar — one ticker, all plans

```python
prev = client.get_previous_close_agg("AAPL", adjusted=True)
print(prev.close, prev.volume)     # PreviousCloseAgg
```
`GET /v2/aggs/ticker/{ticker}/prev`. One call per ticker, so 10 tickers = 10 calls,
which blows the free-tier budget. Use §5 for multi-ticker EOD; use this for one-offs
such as seeding a newly added watchlist ticker.

### Custom Bars — historical series for the detail chart

```python
bars = client.get_aggs(
    ticker="AAPL",
    multiplier=5,
    timespan="minute",          # second | minute | hour | day | week | month | quarter | year
    from_="2026-09-01",         # YYYY-MM-DD, date, datetime, or Unix ms
    to="2026-09-04",
    adjusted=True,
    sort="asc",
    limit=5000,                 # max 50000
)
for b in bars:
    print(b.timestamp, b.open, b.high, b.low, b.close, b.volume)
```
`GET /v2/aggs/ticker/{t}/range/{mult}/{span}/{from}/{to}`. Note `list_aggs()` is the
auto-paginating iterator variant of the same endpoint. Second-resolution bars require
Developer or above.

### Single Ticker Snapshot

```python
snap = client.get_snapshot_ticker(SnapshotMarketType.STOCKS, "AAPL")
```
`GET /v2/snapshot/locale/us/markets/stocks/tickers/{ticker}`. Starter+ only.

### Market Status

```python
status = client.get_market_status()   # status.market == "open" | "closed" | "extended-hours"
```
Useful for deciding whether to poll at full cadence or back off overnight.

---

## 7. Python Client Reference

### Install

```toml
# backend/pyproject.toml
dependencies = ["massive>=2.2.0"]
```

### Construction

```python
from massive import RESTClient

client = RESTClient(
    api_key="YOUR_KEY",      # defaults to os.getenv("MASSIVE_API_KEY")
    connect_timeout=10.0,
    read_timeout=10.0,
    num_pools=10,
    retries=3,               # urllib3 Retry with backoff, built in
    base="https://api.massive.com",
    pagination=True,
    trace=False,             # True logs full URLs with the key REDACTED
)
```

The client sends `Authorization: Bearer <key>` and a
`Massive.com PythonClient/<version>` user agent. Constructing it with no key and no
environment variable raises `AuthError` immediately.

### The client is synchronous

`RESTClient` is built on `urllib3` and **blocks**. FinAlly's event loop must never call
it directly:

```python
snapshots = await asyncio.to_thread(self._fetch_snapshots)   # correct
snapshots = self._client.get_snapshot_all(...)               # blocks the whole app
```

### Exceptions

`massive.exceptions` defines exactly two:

| Exception | Raised when |
|---|---|
| `AuthError` | API key missing or empty at construction time |
| `BadResponse` | Any non-200 response from the API |

`BadResponse` is deliberately coarse — a `401` (bad key), `403` (plan does not include
this endpoint) and `429` (rate limited) all surface as the same type. The message body
carries the detail, so log `str(e)` rather than only the type. Network-level failures
surface as `urllib3` exceptions, so a polling loop must catch broad `Exception`.

### Naming conventions in the models

The client renames Massive's terse JSON keys to readable attributes. When in doubt, the
`from_dict` staticmethod on each model in `massive/rest/models/` is the authoritative
mapping. The traps worth memorising:

| You might write | Correct attribute |
|---|---|
| `last_trade.timestamp` | `last_trade.sip_timestamp` |
| `snapshot.prev_day.c` | `snapshot.prev_day.close` |
| `snapshot.todays_change_perc` | `snapshot.todays_change_percent` |
| `grouped_agg.T` | `grouped_agg.ticker` |

---

## 8. Streaming (WebSocket) — Considered and Rejected

`massive.WebSocketClient` offers a genuine push feed (`T.*` trades, `Q.*` quotes,
`A/AM` aggregates) on the `wss://socket.massive.com/stocks` cluster. FinAlly does **not**
use it, for three reasons:

1. WebSocket access starts at Starter; the free Basic tier has none, so the fallback
   path would still need REST and we would maintain two ingestion paths.
2. FinAlly already pushes to the browser over SSE at a fixed ~500ms cadence from an
   in-memory cache. A push source upstream of that cache adds no user-visible latency.
3. REST polling is uniform across simulator and live modes, which keeps
   `MarketDataSource` implementations symmetrical.

Should real tick data ever be wanted, the WebSocket client slots in behind the same
`MarketDataSource` interface with no downstream changes — which is the point of the
design in `MARKET_INTERFACE.md`.

---

## 9. Practical Polling Strategy for FinAlly

```
MASSIVE_API_KEY set?
├── no  ──────────────────► SimulatorDataSource (see MARKET_SIMULATOR.md)
└── yes ──► probe plan capability on first poll
            ├── snapshot call succeeds ──► poll get_snapshot_all every 15s
            └── snapshot call 403/401 ──► fall back to get_grouped_daily_aggs
                                          (EOD closes, refresh every 15 min)
```

Recommended intervals:

| Situation | Interval | Calls/min |
|---|---|---|
| Free Basic, EOD via grouped daily | 900 s | 0.07 |
| Starter/Developer, 15-min delayed snapshot | 15 s | 4 |
| Advanced/Business, real-time snapshot | 2–5 s | 12–30 |

15 seconds is the safe default: it sits inside the free tier's 5 calls/min budget and is
correct for every paid tier.

### Error handling in the loop

A polling loop must never die. Catch broad, log, and let the next tick retry — stale
prices in the cache are far better than a dead price feed:

```python
async def _poll_once(self) -> None:
    try:
        snapshots = await asyncio.to_thread(self._fetch_snapshots)
    except Exception as e:                 # BadResponse, urllib3, DNS, ...
        logger.error("Massive poll failed: %s", e)
        return                             # keep last known prices; retry next tick
    ...
```

Treat `429` as a signal to widen the interval (exponential backoff to a ceiling), and a
repeated `401`/`403` as fatal-but-degradable: log loudly once and consider falling back
to the simulator rather than serving a permanently empty watchlist.

---

## Sources

- [Massive API Docs](https://massive.com/docs)
- [Massive docs index (llms.txt)](https://massive.com/docs/llms.txt)
- [Stocks REST Overview](https://massive.com/docs/rest/stocks/overview)
- [Full Market Snapshot](https://massive.com/docs/rest/stocks/snapshots/full-market-snapshot)
- [Unified Snapshot](https://massive.com/docs/rest/stocks/snapshots/unified-snapshot)
- [Daily Market Summary (grouped daily)](https://massive.com/docs/rest/stocks/aggregates/daily-market-summary)
- [Massive + Python blog post](https://massive.com/blog/polygon-io-with-python-for-stock-market-data)
- [Polygon.io/Massive pricing summary](https://apicostcalc.com/polygon.html)
- Local source of truth: `backend/.venv/lib/python3.14/site-packages/massive/` (v2.2.0)
