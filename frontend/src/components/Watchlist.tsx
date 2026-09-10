"use client";

import { useState, type FormEvent } from "react";

import { ApiError } from "@/lib/api";
import { formatPrice, isValidTicker, normalizeTicker, signOf } from "@/lib/format";
import { useMarket } from "@/state/market";
import { useTerminal } from "@/state/terminal";
import { Delta } from "./Delta";
import { Panel } from "./Panel";
import { Sparkline } from "./Sparkline";

/** Ticks drawn in a sparkline. Beyond this the 58px line is just noise. */
const SPARK_POINTS = 90;

export function Watchlist() {
  const { frame, pulses, seriesFor } = useMarket();
  const { watchlist, selected, select, addTicker, removeTicker, loaded } = useTerminal();

  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const onAdd = async (event: FormEvent) => {
    event.preventDefault();
    const ticker = normalizeTicker(draft);
    if (!isValidTicker(ticker)) {
      setError("Enter a ticker of 1-10 letters.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await addTicker(ticker);
      setDraft("");
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Could not add that ticker.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel
      label="Watchlist"
      testId="watchlist"
      meta={<span className="num text-[10px] text-ink-mute">{watchlist.length}</span>}
      bodyClassName="flex flex-col"
    >
      <div className="min-h-0 flex-1 overflow-y-auto" data-testid="watchlist-rows">
        {watchlist.length === 0 ? (
          <p className="px-3 py-4 text-ink-mute" data-testid="watchlist-empty">
            {loaded ? "Add a ticker below to start watching it." : "Loading instruments…"}
          </p>
        ) : (
          watchlist.map((entry) => (
            <WatchlistRow
              key={entry.ticker}
              ticker={entry.ticker}
              sessionOpen={entry.session_open}
              fallbackPrice={entry.price}
              fallbackSessionPercent={entry.session_change_percent}
              selected={selected === entry.ticker}
              pulse={pulses[entry.ticker] ?? 0}
              direction={frame[entry.ticker]?.direction ?? "flat"}
              livePrice={frame[entry.ticker]?.price ?? null}
              history={seriesFor(entry.ticker)}
              onSelect={select}
              onRemove={removeTicker}
            />
          ))
        )}
      </div>

      <form
        onSubmit={onAdd}
        className="shrink-0 border-t border-line bg-rail px-2 py-2"
        data-testid="watchlist-add-form"
      >
        <div className="flex gap-1.5">
          <input
            value={draft}
            onChange={(event) => setDraft(event.target.value.toUpperCase())}
            placeholder="Add ticker"
            aria-label="Ticker to add to the watchlist"
            maxLength={10}
            className="num min-w-0 flex-1 rounded-sm border border-line-strong bg-panel px-2 py-1 font-mono text-ink placeholder:font-sans placeholder:text-ink-mute focus:border-accent focus:outline-none"
            data-testid="watchlist-add-input"
          />
          <button
            type="submit"
            disabled={busy}
            className="rounded-sm bg-submit px-3 py-1 text-[11px] font-semibold tracking-wide text-white transition-colors hover:bg-submit-hot disabled:opacity-50"
            data-testid="watchlist-add-submit"
          >
            Add
          </button>
        </div>
        {error ? (
          <p className="mt-1.5 text-[11px] text-down" role="alert" data-testid="watchlist-add-error">
            {error}
          </p>
        ) : null}
      </form>
    </Panel>
  );
}

function WatchlistRow({
  ticker,
  sessionOpen,
  fallbackPrice,
  fallbackSessionPercent,
  selected,
  pulse,
  direction,
  livePrice,
  history,
  onSelect,
  onRemove,
}: {
  ticker: string;
  sessionOpen: number | null;
  fallbackPrice: number | null;
  fallbackSessionPercent: number | null;
  selected: boolean;
  pulse: number;
  direction: "up" | "down" | "flat";
  livePrice: number | null;
  history: readonly { t: number; p: number }[];
  onSelect: (ticker: string) => void;
  onRemove: (ticker: string) => Promise<void>;
}) {
  const price = livePrice ?? fallbackPrice;
  // Recomputed every frame on purpose: the history buffer is mutated in place,
  // so its identity never changes and any memo keyed on it would freeze the
  // line at whatever it held on first render.
  const values = history.slice(-SPARK_POINTS).map((point) => point.p);

  // The stream carries tick-over-tick change; the day figure is the move from
  // the session open, recomputed live so it does not go stale between fetches.
  const sessionPercent =
    price != null && sessionOpen != null && sessionOpen !== 0
      ? ((price - sessionOpen) / sessionOpen) * 100
      : fallbackSessionPercent;

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={() => onSelect(ticker)}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onSelect(ticker);
        }
      }}
      aria-pressed={selected}
      aria-label={`${ticker} at ${formatPrice(price)}`}
      className={`group grid h-[34px] cursor-pointer grid-cols-[3px_minmax(0,1fr)_58px_auto] items-center gap-2 border-b border-line/60 pr-1.5 transition-colors ${
        selected ? "bg-raised" : "hover:bg-raised/60"
      }`}
      data-testid="watchlist-row"
      data-ticker={ticker}
      data-selected={selected}
    >
      {/* The one bold move in the interface: an amber rail marks the selection. */}
      <span
        aria-hidden
        className={`h-full ${selected ? "bg-accent" : "bg-transparent"}`}
      />

      <span className="truncate pl-1.5 font-mono text-[12px] font-medium tracking-tight text-ink">
        {ticker}
      </span>

      <Sparkline values={values} width={58} direction={signOf(sessionPercent)} testId="watchlist-sparkline" />

      <div className="flex w-[104px] flex-col items-end leading-tight">
        <span
          key={pulse}
          data-flash={direction === "flat" ? undefined : direction}
          data-testid="watchlist-price"
          data-ticker={ticker}
          className="num rounded-[2px] px-1 font-mono text-[12px] text-ink"
        >
          {formatPrice(price)}
        </span>
        <Delta
          value={sessionPercent}
          showGlyph={false}
          className="px-1 text-[10.5px]"
          testId="watchlist-change"
        />
      </div>

      <button
        type="button"
        onClick={(event) => {
          event.stopPropagation();
          void onRemove(ticker);
        }}
        aria-label={`Remove ${ticker} from the watchlist`}
        className="opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100"
        data-testid="watchlist-remove"
        data-ticker={ticker}
      >
        <span aria-hidden className="px-1 text-ink-mute hover:text-down">
          &times;
        </span>
      </button>
    </div>
  );
}
