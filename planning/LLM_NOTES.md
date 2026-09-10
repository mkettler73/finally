# FinAlly — LLM Chat Subsystem Notes

Owner: **LLM Engineer**. Consumers: the **Integration Tester** (mock strings and
endpoint behaviour), the **Backend API Engineer** (router wiring), the
**Frontend Engineer** (chat response shape).

The HTTP shapes are fixed by `API_CONTRACT.md` §5 and §6. This document adds
what that contract deliberately left to this agent: **the exact mock-mode
strings**, the wiring signature, and the behavioural details a test needs.

---

## 1. Module map

```
backend/app/llm/
├── __init__.py     # Public surface
├── models.py       # Structured-output models (§6) + HTTP request/response models (§5)
├── prompt.py       # SYSTEM_PROMPT and build_system_message()
├── context.py      # build_context() / render_context() — portfolio, watchlist, history
├── client.py       # LiveChatClient (LiteLLM→OpenRouter→Cerebras), build_chat_client()
├── mock.py         # MockChatClient — deterministic, offline
├── executor.py     # execute_actions() — trades then watchlist changes
├── router.py       # create_chat_router()
├── _deps.py        # Late binding to app.db and app.services.trading
└── _rows.py        # Field accessor tolerant of dict / sqlite3.Row / dataclass rows
```

---

## 2. Wiring (for `main.py`)

```python
from .llm import create_chat_router

app.include_router(create_chat_router(price_cache))
```

Full signature:

```python
def create_chat_router(
    price_cache: PriceCache,
    *,
    market_source: MarketDataSource | None = None,
    client: ChatClient | None = None,
    user_id: str = "default",
    prompt_history_turns: int = 20,
) -> APIRouter
```

- `market_source` is optional because the source does not exist when the router
  is built. When omitted it is read per request from `app.state.market_source`,
  which is where the existing lifespan already puts it. An explicitly passed
  source wins.
- `client` is optional; when omitted the client is chosen **per request** from
  `LLM_MOCK`, so setting the variable after import still works.
- Routes registered: `POST /api/chat`, `GET /api/chat/history`.

`main.py` must also load the project-root `.env` (`python-dotenv` is already a
dependency) — nothing in the backend does that yet, and without it
`OPENROUTER_API_KEY` and `LLM_MOCK` are only visible when Docker injects them.

---

## 3. Mock mode — the exact contract

`LLM_MOCK` is truthy for `1`, `true`, `yes`, `on` (case-insensitive). When
truthy, `build_chat_client()` returns `MockChatClient`, which contains no HTTP
client and never imports `litellm`. `backend/tests/llm/test_mock_isolation.py`
proves this in a clean subprocess: after a full mock chat turn, neither
`litellm` nor `httpx` is in `sys.modules`.

### 3.1 Branch order

Evaluated top to bottom; the first match wins.

| # | Condition | Result |
|---|---|---|
| 1 | word `buy` **and** a ticker **and** a number | buy trade |
| 2 | word `sell` **and** a ticker **and** a number | sell trade |
| 3 | word `remove` or `unwatch` **and** a ticker | watchlist remove |
| 4 | word `watch` or `add` **and** a ticker | watchlist add |
| 5 | anything else | analysis fallback |

Removal is checked **before** addition, which is the one deviation from the
order of the table in `API_CONTRACT.md` §6. It is deliberate: a sentence like
`"remove NVDA and add nothing"` must not resolve to an add.

All keyword matching is **whole-word** (`\bword\b`), so:
- `unwatch` does **not** match `watch`.
- `watchlist` does **not** match `watch` — `"what is on my watchlist?"` falls
  through to the fallback and produces no action.

### 3.2 Number and ticker detection

- **Quantity**: the first `\d+(\.\d+)?` in the message. `"buy 2.5 AAPL"` → `2.5`.
- **Ticker**, first match wins across three passes:
  1. any word that matches a ticker the user already holds or watches
     (positions first, then watchlist), case-insensitive;
  2. any 1–5 character **UPPERCASE** word that is not a stopword;
  3. any 1–5 character word that is not a stopword, uppercased.
- If no ticker resolves, branches 1–4 cannot fire and the message falls through
  to the fallback. Same if a trade branch finds no number: `"should I buy
  AAPL?"` returns the fallback, not a trade.

### 3.3 The exact strings

`{qty}` is the quantity with a trailing `.0` stripped (`5.0` → `5`, `2.5` →
`2.5`). `{TICKER}` is uppercase and whitespace-stripped.

| Branch | `message` | Structured actions |
|---|---|---|
| buy | `Buying {qty} {TICKER} at the market price now.` | `trades: [{ticker, side: "buy", quantity}]` |
| sell | `Selling {qty} {TICKER} at the market price now.` | `trades: [{ticker, side: "sell", quantity}]` |
| add | `Adding {TICKER} to your watchlist.` | `watchlist_changes: [{ticker, action: "add"}]` |
| remove | `Removing {TICKER} from your watchlist.` | `watchlist_changes: [{ticker, action: "remove"}]` |
| fallback | see below | none |

The fallback string, in full:

```
You are holding {N} position{s} with ${CASH} in cash. Ask me to buy or sell a ticker, or to add one to your watchlist.
```

- `{N}` is the live open-position count; `{s}` is `""` when `N == 1`, else `"s"`.
- `${CASH}` is the live cash balance formatted with thousands separators and
  exactly two decimals, e.g. `$10,000.00`.

**On a fresh database the fallback is exactly:**

```
You are holding 0 positions with $10,000.00 in cash. Ask me to buy or sell a ticker, or to add one to your watchlist.
```

Total portfolio value is deliberately **not** in the fallback — it moves with
every price tick and would make a verbatim assertion flaky. Cash and position
count only change when a trade executes.

### 3.4 Worked examples for the E2E suite

| Sent | `message` returned | `actions` |
|---|---|---|
| `Buy 5 shares of NVDA` | `Buying 5 NVDA at the market price now.` | one `trade`/`ok` |
| `sell 2 AAPL` | `Selling 2 AAPL at the market price now.` | one `trade`/`ok` |
| `add PYPL to my watchlist` | `Adding PYPL to your watchlist.` | one `watchlist`/`ok` |
| `remove GOOGL` | `Removing GOOGL from your watchlist.` | one `watchlist`/`ok` |
| `How am I doing?` (fresh db) | the fallback above | `[]` |

Note the split: the strings above are the **assistant message**. The `actions`
array is produced by the executor from the real trade and watchlist services,
so its `detail` reflects the actual fill — `Bought 5 NVDA @ $121.40` — and its
price will differ run to run. **Assert on `actions[].type`, `status` and
`data.ticker` / `data.side` / `data.quantity`; do not assert on a price.**

---

## 4. Action detail strings (both modes)

Produced by the executor, not the model.

| Situation | `detail` |
|---|---|
| Buy filled | `Bought {qty} {TICKER} @ ${price}` |
| Sell filled | `Sold {qty} {TICKER} @ ${price}` |
| Trade rejected | the `TradeError.message` from the trade service, verbatim |
| Malformed ticker | `'{raw}' is not a valid ticker symbol.` |
| Watchlist add | `Added {TICKER} to the watchlist` |
| Watchlist remove | `Removed {TICKER} from the watchlist` |
| Watchlist rejected | the `DbError` message, verbatim |

`data` for an ok trade is `{ticker, side, quantity, price, total}`; for a failed
trade it is `{ticker, side, quantity}` (no price — there was no fill). For a
watchlist action it is `{ticker, action}`.

---

## 5. Behavioural details worth testing against

- **Failed actions do not fail the request.** A rejected trade is a `200` whose
  `actions[0].status == "error"`. Only an upstream model failure produces
  `502 {"detail": {"code": "LLM_ERROR", "message": ...}}`.
- **Execution order** is every trade in array order, then every watchlist change
  in array order. One failure does not stop the rest.
- **Every trade goes through `app.services.trading.execute_trade`** — the same
  function `POST /api/portfolio/trade` uses. There is no second trade path.
- **Watchlist adds** also `await source.add_ticker(...)`. **Removes** call
  `source.remove_ticker(...)` only when no open position still holds the ticker,
  so a held position keeps streaming.
- **Persistence order:** the user turn is written before the model is called, so
  a `502` leaves the question in history with no answer. The assistant turn is
  written after execution, with the `actions` array attached.
- **History is read before the new user message is stored**, so the model is not
  handed the current turn twice. `GET /api/chat/history` is oldest-first;
  `actions` is `null` for user turns and an array for assistant turns.
- **Empty or whitespace-only `message`** is rejected by Pydantic (`min_length=1`
  after stripping), max 4000 characters. That surfaces as FastAPI's validation
  error, which the Backend API Engineer's exception handler maps into the
  standard envelope — this router does not invent an error code for it, because
  `API_CONTRACT.md` §0 does not define one.
- `GET /api/chat/history?limit=` accepts 1–500, default 50.

---

## 6. Live mode

Per the `cerebras` skill: LiteLLM → OpenRouter → Cerebras.

```python
MODEL = "openrouter/openai/gpt-oss-120b"
EXTRA_BODY = {"provider": {"order": ["cerebras"]}}
reasoning_effort = "low"
response_format = ChatResponse   # Pydantic structured output
```

- `litellm` is imported lazily, inside the live call path only.
- **One retry.** On a transport error or unparseable output the call is repeated
  once with an appended corrective system message. A second failure raises
  `LLMError`, which becomes `502 LLM_ERROR` carrying the underlying reason — the
  layer never fabricates a reply to cover a failure.
- A missing `OPENROUTER_API_KEY` fails before any network call, with a message
  that says so.
- Parsing tolerates a fenced ```json block or JSON embedded in prose, and
  coerces `null` arrays to `[]`.

---

## 7. Known gaps and assumptions

1. **Watchlist mutation is implemented twice.** `API_CONTRACT.md` §7 freezes a
   shared service for trades but not for the watchlist, so `executor.py` calls
   `app.db` plus the market source directly, replicating the rules in §4. If the
   Backend API Engineer publishes an `app.services.watchlist`, the executor
   should switch to it. Behaviour is identical today; the risk is future drift.
2. **Row shape.** `DATA_LAYER.md` §4 permits "TypedDicts or dataclasses" and
   §7 does not pin `TradeResult.trade`. Every read goes through
   `app/llm/_rows.py`, which handles mappings, `sqlite3.Row` and plain objects,
   so either choice works.
3. **`app.db` and `app.services.trading` are resolved at call time** via
   `importlib` (`_deps.py`), not imported at module scope. This let the chat
   layer be built and tested before those modules existed, and it is what lets
   the unit tests install in-memory fakes.
