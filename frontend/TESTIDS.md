# FinAlly frontend — selector contract

Every `data-testid` the terminal exposes, plus the data attributes that carry
state. This is the interface between the frontend and the Playwright suite in
`test/`: **these names are stable**, and the frontend engineer changes them only
by agreement with the Integration Tester.

Prefer asserting on a data attribute (`data-direction`, `data-flash`,
`data-state`) over a CSS class or a colour — the attributes are the contract,
the styling is not.

## Shell

| Test ID | Element | Notes |
|---|---|---|
| `terminal-root` | The whole page grid | Present once the app has mounted. |
| `header` | Header rail | |
| `header-total-value` | Total portfolio value + return | Live; marked to the stream every tick. Renders `—` until the first `GET /api/portfolio` resolves. |
| `header-return` | Total return | Also `data-direction="up\|down\|flat"`. |
| `header-cash` | Cash balance | `—` before load. |
| `header-unrealized` | Total unrealised P&L | |
| `header-clock` | Wall clock | `--:--:--` before mount, then `HH:MM:SS`. |
| `connection-status` | Stream indicator | **`data-state="live\|connecting\|down"`**, and the visible text is `Live` / `Reconnecting` / `Disconnected`. `connecting` is the initial state and also what a recoverable `EventSource` error produces. |

## Watchlist

| Test ID | Element | Notes |
|---|---|---|
| `watchlist` | Panel | |
| `watchlist-rows` | Scrolling list | |
| `watchlist-row` | One instrument | `data-ticker`, `data-selected="true\|false"`. Clicking it selects the instrument for the main chart. |
| `watchlist-price` | Price cell | `data-ticker`, and **`data-flash="up\|down"`** for ~500ms after a tick that moved the price. The attribute is absent when the price did not move. |
| `watchlist-change` | Day change % | `data-direction`. Recomputed live from `session_open`, so it does not match the stream's tick-over-tick `change_percent`. |
| `watchlist-sparkline` | `<svg>` | `data-points` = number of ticks accumulated since page load. `0` before the first frame. |
| `watchlist-remove` | Remove button | `data-ticker`. Hidden until the row is hovered or focused — Playwright's `click()` handles this, but a visibility assertion will not. |
| `watchlist-empty` | Empty state | Only when the watchlist has no entries. |
| `watchlist-add-form` / `watchlist-add-input` / `watchlist-add-submit` | Add control | Input uppercases as you type. |
| `watchlist-add-error` | Add failure | Renders the API's `detail.message` verbatim, or a local validation message for a malformed ticker (no request is sent in that case). |

## Charts

| Test ID | Element | Notes |
|---|---|---|
| `price-chart` | Panel; its header shows the selected ticker | |
| `price-chart-canvas` | lightweight-charts mount point | Canvas — assert on the surrounding testids, not on pixels. |
| `price-chart-last` / `price-chart-change` | Last price and day change | |
| `price-chart-empty` | Placeholder | Shown with no selection, or with fewer than two accumulated ticks. |
| `price-chart-readout` | Crosshair readout | Only while hovering the plot. |
| `pnl-chart` | Portfolio value panel | Baseline series split at `starting_cash`. |
| `pnl-chart-canvas`, `pnl-chart-empty`, `pnl-chart-readout` | As above | `pnl-chart-empty` only when `GET /api/portfolio/history` returned no snapshots. |

## Allocation heatmap

| Test ID | Element | Notes |
|---|---|---|
| `heatmap` | Panel | |
| `heatmap-cell` | One position | `data-ticker`, `data-weight` (percent, 2dp), **`data-pnl-direction="up\|down\|flat"`**. Clicking selects the instrument. |
| `heatmap-legend` | Diverging scale | Absent when there are no positions. The bounds adapt to the largest move in the book, with a 0.5% floor. |
| `heatmap-tooltip` | Hover detail | |
| `heatmap-empty` | Empty state | |

## Positions

| Test ID | Element | Notes |
|---|---|---|
| `positions` / `positions-table` | Panel and table | |
| `positions-row` | One holding | `data-ticker`. Sorted by market value descending. Clicking selects the instrument. |
| `positions-price` | Last price cell | `data-ticker` and `data-flash`, exactly as the watchlist. |
| `positions-pnl` | Unrealised P&L in dollars | `data-direction`. |
| `positions-pnl-percent` | Unrealised return | `data-direction`. |
| `positions-total-pnl` | Panel-header total | |
| `positions-empty` | Empty state | |

## Trade bar

| Test ID | Element | Notes |
|---|---|---|
| `trade-bar` | The bar | |
| `trade-ticker` | Ticker field | Prefilled from the current selection until the field is edited by hand; thereafter it stays put. Uppercases as you type. |
| `trade-quantity` | Quantity field | Accepts fractional shares. Cleared after a filled order. |
| `trade-buy` / `trade-sell` | Market order buttons | No confirmation dialog. Disabled while an order is in flight; the label reads `Buying…` / `Selling…`. |
| `trade-estimate` | Live cost estimate | `No price yet`, or `<price> a share`, or `<price> a share — $<total>` once a quantity is entered. |
| `trade-status` | Fill confirmation | Present only on success. |
| `trade-error` | Rejection | The API's `detail.message` verbatim, or a local validation message. `trade-status` and `trade-error` are mutually exclusive. |

## Assistant

| Test ID | Element | Notes |
|---|---|---|
| `chat-panel` | Sidebar | `data-collapsed="true\|false"`. Collapsed, it renders only `chat-toggle`. |
| `chat-toggle` | Collapse / expand | |
| `chat-messages` | Scrolling transcript | |
| `chat-message` | One turn | `data-role="user\|assistant"`. |
| `chat-action` | An executed action chip | **`data-status="ok\|error"`**, `data-type="trade\|watchlist"`. The text is the backend's `actions[].detail` **verbatim**, including error wording — do not expect the frontend to rephrase it. |
| `chat-loading` | Pending indicator | Present between send and reply; `chat-input` is disabled meanwhile. |
| `chat-form` / `chat-input` / `chat-send` | Composer | Send is disabled for an empty draft and while a reply is pending. |
| `chat-empty` | Empty state with prompts | |
| `chat-suggestion` | A starter prompt | Clicking one fills the composer; it does not send. |

## Timing notes for the E2E suite

- The stream emits roughly twice a second. A flash assertion should follow a
  price change closely; `data-flash` is not cleared on a timer, it simply stops
  being re-applied, so read it right after a tick.
- The default selection is the first watchlist entry, and it is applied on the
  render after `GET /api/watchlist` resolves — wait for
  `[data-selected="true"]` rather than asserting immediately after load.
- Sparklines start empty. `data-points` reaching 2 is the earliest point at
  which a line is drawn.
- Every figure is formatted to 2dp on the client from an unrounded float, so
  assert on the rendered string (`$10,018.50`), not on a computed number.
