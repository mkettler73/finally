# FinAlly — end-to-end suite

Playwright, driven against **the built container**, never against `next dev`.
The artefact under test is the image that ships: the FastAPI process serving the
static Next.js export and the API on one port.

## Running it

Everything in one command — builds the image, waits for health, runs the suite
in a browser container, tears down:

```bash
cd test
npm run e2e
```

Or drive it from your own machine against a published container:

```bash
cd test
npm install
npx playwright install chromium

docker compose -f docker-compose.test.yml down -v     # start from a clean db
docker compose -f docker-compose.test.yml up --build -d app
npx playwright test                                    # BASE_URL defaults to :8010
```

`BASE_URL` overrides the target; `FINALLY_TEST_PORT` overrides the published
host port (8010 by default, since 8000 is often already taken).

## Rules the suite holds itself to

**`down -v` before every run.** `01-fresh-start.spec.ts` asserts a pristine
$10,000 balance and the seeded ten tickers. That is only true of a database that
has never been traded against, so the app's volume is destroyed between runs. If
that spec fails on cash, this is the first thing to check.

**Serial, one worker, no retries.** The app is single-user: one SQLite file, one
cash balance. Parallel workers would trade against each other's balance and make
every money assertion a lie. Retries are off because a spec that half-executed a
trade cannot be replayed cleanly, and a retry would hide the flakiness rather
than report it.

**Readiness is polled, never slept.** `global-setup.ts` polls `GET /api/health`
until it answers, then checks that `GET /` actually serves the frontend — an app
that is up but serving no static export would otherwise fail every UI spec with
a confusing blank page.

**Properties, not prices.** Prices stream every ~500ms from a stochastic
simulator, so no assertion names a price. What is asserted is that a price
*moved*, that cash fell by exactly `quantity × price` from the fill the server
reported, that a weight matches the market value it is drawn from. Where the UI
and the server must agree, both are read at the same instant — usually in a
single `page.evaluate` — because a tick landing between two reads would
manufacture a disagreement that is not a bug.

**Values, not existence.** An assertion that an element is merely present, where
the plausible bug is a wrong number inside it, is worse than no assertion.

## Layout

| File | Covers |
|---|---|
| `01-fresh-start.spec.ts` | Seeded ten tickers, $10,000, empty book, live streaming prices, sparkline accumulation, chart selection |
| `02-watchlist.spec.ts` | Add, price, remove; duplicate and malformed rejections; a held ticker keeps streaming after removal |
| `03-trading.spec.ts` | Buy, partial sell, sell-out; insufficient cash and shares; local validation; the order estimate |
| `04-portfolio-visuals.spec.ts` | Treemap weights and P&L colours against the positions table; tooltip; snapshot history growth |
| `05-chat.spec.ts` | Mock-mode assistant: analysis, buy, sell, rejected trade, watchlist changes, loading state, history restore |
| `06-stream-resilience.spec.ts` | Offline → indicator reports it → reconnect and resume; reload; account readable while down |
| `e2e/helpers.ts` | Contract types, the frontend's formatting rules, API client, stream waits |

The selectors come from `frontend/TESTIDS.md`, and the mock assistant strings
from `planning/LLM_NOTES.md` §3.3. Both are contracts: if a name here needs to
change, it changes there first.
