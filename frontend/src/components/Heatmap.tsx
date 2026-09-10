"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { CHART, pnlFill, pnlInk, rampCapFor } from "@/lib/colors";
import { formatPercent, formatQuantity, formatSignedPercent, formatSignedUsd, formatUsd } from "@/lib/format";
import { squarify } from "@/lib/treemap";
import type { Position } from "@/lib/types";
import { useTerminal } from "@/state/terminal";
import { Panel } from "./Panel";

/** A cell must clear these before its label is drawn, or the text collides. */
const LABEL_MIN_WIDTH = 42;
const LABEL_MIN_HEIGHT = 24;
const VALUE_MIN_HEIGHT = 40;

/**
 * Positions sized by portfolio weight and coloured by unrealised P&L. The
 * colour scale diverges from a neutral grey at break-even; the signed
 * percentage is drawn inside each cell, so the ranking is readable without
 * relying on hue.
 */
export function Heatmap() {
  const { portfolio, select, selected } = useTerminal();
  const containerRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [hovered, setHovered] = useState<Position | null>(null);

  useEffect(() => {
    const element = containerRef.current;
    if (!element || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      setSize({ width, height });
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const cap = useMemo(
    () => rampCapFor(portfolio.positions.map((position) => position.unrealized_pnl_percent)),
    [portfolio.positions],
  );

  const cells = useMemo(
    () =>
      squarify(
        portfolio.positions.map((position) => ({
          value: position.market_value,
          datum: position,
        })),
        size.width,
        size.height,
      ),
    [portfolio.positions, size.width, size.height],
  );

  return (
    <Panel
      label="Allocation"
      testId="heatmap"
      meta={
        <>
          {portfolio.positions.length > 0 ? <ScaleLegend cap={cap} /> : null}
          <span className="num text-[10px] text-ink-mute">
            {formatUsd(portfolio.positions_value)} invested
          </span>
        </>
      }
      bodyClassName="relative"
    >
      <div ref={containerRef} className="absolute inset-0">
        {portfolio.positions.length === 0 ? (
          <p
            className="flex h-full items-center justify-center px-4 text-center text-ink-mute"
            data-testid="heatmap-empty"
          >
            Buy something and it will show up here, sized by weight.
          </p>
        ) : (
          <svg
            width={size.width}
            height={size.height}
            className="block"
            role="img"
            aria-label="Portfolio allocation by weight, coloured by unrealised profit and loss"
          >
            {cells.map(({ x, y, width, height, datum }) => {
              const fill = pnlFill(datum.unrealized_pnl_percent, cap);
              const ink = pnlInk(datum.unrealized_pnl_percent, cap);
              const isSelected = selected === datum.ticker;
              return (
                <g
                  key={datum.ticker}
                  onMouseEnter={() => setHovered(datum)}
                  onMouseLeave={() => setHovered(null)}
                  onClick={() => select(datum.ticker)}
                  className="cursor-pointer"
                  data-testid="heatmap-cell"
                  data-ticker={datum.ticker}
                  data-weight={datum.weight.toFixed(2)}
                  data-pnl-direction={
                    datum.unrealized_pnl > 0 ? "up" : datum.unrealized_pnl < 0 ? "down" : "flat"
                  }
                >
                  {/* Inset by 1px a side, giving a 2px surface gap between cells. */}
                  <rect
                    x={x + 1}
                    y={y + 1}
                    width={Math.max(0, width - 2)}
                    height={Math.max(0, height - 2)}
                    fill={fill}
                    stroke={isSelected ? CHART.accent : "transparent"}
                    strokeWidth={isSelected ? 2 : 0}
                  />
                  {width >= LABEL_MIN_WIDTH && height >= LABEL_MIN_HEIGHT ? (
                    <text
                      x={x + 7}
                      y={y + 17}
                      fill={ink}
                      className="font-mono text-[11px] font-medium"
                      style={{ pointerEvents: "none" }}
                    >
                      {datum.ticker}
                    </text>
                  ) : null}
                  {width >= LABEL_MIN_WIDTH && height >= VALUE_MIN_HEIGHT ? (
                    <text
                      x={x + 7}
                      y={y + 31}
                      fill={ink}
                      opacity={0.85}
                      className="font-mono text-[10px]"
                      style={{ pointerEvents: "none" }}
                    >
                      {formatSignedPercent(datum.unrealized_pnl_percent)}
                    </text>
                  ) : null}
                </g>
              );
            })}
          </svg>
        )}

        {hovered ? (
          <div
            className="pointer-events-none absolute right-2 bottom-8 w-[186px] border border-line-strong bg-rail/97 px-2.5 py-2"
            data-testid="heatmap-tooltip"
          >
            <p className="font-mono text-[12px] text-ink">{hovered.ticker}</p>
            <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-[11px]">
              <dt className="text-ink-mute">Weight</dt>
              <dd className="num text-right text-ink-dim">{formatPercent(hovered.weight)}</dd>
              <dt className="text-ink-mute">Shares</dt>
              <dd className="num text-right text-ink-dim">{formatQuantity(hovered.quantity)}</dd>
              <dt className="text-ink-mute">Value</dt>
              <dd className="num text-right text-ink-dim">{formatUsd(hovered.market_value)}</dd>
              <dt className="text-ink-mute">P&amp;L</dt>
              <dd
                className="num text-right"
                style={{ color: hovered.unrealized_pnl >= 0 ? CHART.up : CHART.down }}
              >
                {formatSignedUsd(hovered.unrealized_pnl)}
              </dd>
            </dl>
          </div>
        ) : null}
      </div>
    </Panel>
  );
}

/**
 * The diverging scale, labelled with the cap actually in use so the fills can
 * be read without hovering.
 */
function ScaleLegend({ cap }: { cap: number }) {
  const bound = cap >= 1 ? cap.toFixed(0) : cap.toFixed(2);
  return (
    <div
      className="num flex items-center gap-1.5 text-[9.5px] text-ink-mute"
      data-testid="heatmap-legend"
    >
      <span>-{bound}%</span>
      <span
        aria-hidden
        className="h-1.5 w-14"
        style={{
          background: `linear-gradient(90deg, ${pnlFill(-cap, cap)}, ${CHART.neutral}, ${pnlFill(cap, cap)})`,
        }}
      />
      <span>+{bound}%</span>
    </div>
  );
}
