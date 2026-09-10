"use client";

import { useState } from "react";

import { Chat } from "@/components/Chat";
import { Header } from "@/components/Header";
import { Heatmap } from "@/components/Heatmap";
import { PnlChart } from "@/components/PnlChart";
import { Positions } from "@/components/Positions";
import { PriceChart } from "@/components/PriceChart";
import { TradeBar } from "@/components/TradeBar";
import { Watchlist } from "@/components/Watchlist";
import { installMockBackend } from "@/lib/mockBackend";
import { MarketProvider } from "@/state/market";
import { TerminalProvider } from "@/state/terminal";

// A no-op unless NEXT_PUBLIC_MOCK_API=1. Runs at import, before anything
// mounts, so the providers' first fetch already sees the patched client.
installMockBackend();

export default function TerminalPage() {
  const [chatCollapsed, setChatCollapsed] = useState(false);

  return (
    <MarketProvider>
      <TerminalProvider>
        {/*
          The whole terminal is one grid with a 1px gap over a dark ground, so
          the seams between panels read as hairlines. No panel carries a border,
          a radius or a shadow of its own.
        */}
        <div
          className="grid h-full gap-px bg-void"
          style={{
            gridTemplateColumns: `264px minmax(0, 1fr) ${chatCollapsed ? "34px" : "clamp(300px, 24vw, 380px)"}`,
            gridTemplateRows: "auto minmax(0, 1fr) auto",
          }}
          data-testid="terminal-root"
        >
          <div className="col-span-3">
            <Header />
          </div>

          <Watchlist />

          <div
            className="grid min-h-0 min-w-0 gap-px bg-void"
            style={{
              gridTemplateRows: "minmax(0, 1.5fr) minmax(0, 1.05fr) minmax(0, 0.85fr)",
            }}
          >
            <PriceChart />
            <div className="grid min-h-0 grid-cols-2 gap-px bg-void">
              <Heatmap />
              <PnlChart />
            </div>
            <Positions />
          </div>

          {/* The assistant runs the full height of the right edge. */}
          <div className="row-span-2 grid min-h-0 grid-rows-1">
            <Chat collapsed={chatCollapsed} onToggle={() => setChatCollapsed((open) => !open)} />
          </div>

          <div className="col-span-2">
            <TradeBar />
          </div>
        </div>
      </TerminalProvider>
    </MarketProvider>
  );
}
