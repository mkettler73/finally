"use client";

import { useEffect, useState } from "react";

import { formatUsd } from "@/lib/format";
import { useMarket, type ConnectionStatus } from "@/state/market";
import { useTerminal } from "@/state/terminal";
import { Delta } from "./Delta";

const STATUS_COPY: Record<ConnectionStatus, { label: string; dot: string; text: string }> = {
  live: { label: "Live", dot: "bg-up", text: "text-up" },
  connecting: { label: "Reconnecting", dot: "bg-accent", text: "text-accent" },
  down: { label: "Disconnected", dot: "bg-down", text: "text-down" },
};

/**
 * The account, read left to right in one line. Field names sit inline beside
 * their figures rather than stacked above them: a terminal header is a readout,
 * not a set of stat cards.
 */
export function Header() {
  const { status } = useMarket();
  const { portfolio, loaded } = useTerminal();

  return (
    <header
      className="flex h-11 items-stretch bg-rail pr-4 pl-4"
      data-testid="header"
    >
      <div className="flex items-center gap-4 pr-5">
        <span className="text-[15px] font-semibold tracking-[0.16em] text-ink">FINALLY</span>
        <span aria-hidden className="h-4 w-px bg-accent" />
      </div>

      <Field label="Total value" testId="header-total-value">
        <span className="num font-mono text-[17px] text-ink">
          {loaded ? formatUsd(portfolio.total_value) : "—"}
        </span>
        <Delta
          value={loaded ? portfolio.total_return_percent : null}
          className="text-[11.5px]"
          testId="header-return"
        />
      </Field>

      <Field label="Cash" testId="header-cash">
        <span className="num font-mono text-[13px] text-ink-dim">
          {loaded ? formatUsd(portfolio.cash_balance) : "—"}
        </span>
      </Field>

      <Field label="Unrealised" testId="header-unrealized">
        <Delta
          value={loaded ? portfolio.total_unrealized_pnl : null}
          kind="usd"
          className="font-mono text-[13px]"
        />
      </Field>

      <div className="ml-auto flex items-center gap-4 pl-5">
        <ConnectionIndicator status={status} />
        <span
          aria-hidden
          className="h-4 w-px bg-line-strong"
        />
        <Clock />
      </div>
    </header>
  );
}

function Field({
  label,
  children,
  testId,
}: {
  label: string;
  children: React.ReactNode;
  testId: string;
}) {
  return (
    <div
      className="flex items-baseline gap-2.5 border-l border-line py-3 pr-5 pl-5"
      data-testid={testId}
    >
      <span className="text-[11px] text-ink-mute">{label}</span>
      {children}
    </div>
  );
}

/**
 * Connection state, spelled out as well as coloured. `EventSource` reconnects
 * on its own, so "Reconnecting" is a status report rather than a prompt to act.
 */
function ConnectionIndicator({ status }: { status: ConnectionStatus }) {
  const { label, dot, text } = STATUS_COPY[status];
  return (
    <div
      className="flex items-center gap-1.5"
      role="status"
      aria-live="polite"
      data-testid="connection-status"
      data-state={status}
    >
      <span
        aria-hidden
        className={`h-1.5 w-1.5 rounded-full ${dot} ${status === "connecting" ? "animate-pulse" : ""}`}
      />
      <span className={`text-[11px] ${text}`}>{label}</span>
    </div>
  );
}

/** Rendered only after mount, so the static export and the client agree. */
function Clock() {
  const [now, setNow] = useState<string | null>(null);

  useEffect(() => {
    const update = () => setNow(new Date().toLocaleTimeString("en-GB", { hour12: false }));
    update();
    const id = window.setInterval(update, 1000);
    return () => window.clearInterval(id);
  }, []);

  return (
    <span className="num font-mono text-[12px] text-ink-mute" data-testid="header-clock">
      {now ?? "--:--:--"}
    </span>
  );
}
