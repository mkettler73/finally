"use client";

import { useEffect, useRef, useState } from "react";
import type { AutoscaleInfo, IChartApi, ISeriesApi, UTCTimestamp } from "lightweight-charts";

import { CHART, chartFontFamily } from "@/lib/colors";
import { formatClock, formatUsd } from "@/lib/format";
import { snapshotsToChartPoints } from "@/lib/series";
import { useMarket } from "@/state/market";
import { useTerminal } from "@/state/terminal";
import { Delta } from "./Delta";
import { Panel } from "./Panel";

/**
 * Total portfolio value over time. This is the one chart with a polarity to
 * show, so it is drawn as a baseline series split at the starting cash: above
 * the line is gain, below it is loss, and the line itself is the neutral
 * midpoint of the diverging scale.
 */
export function PnlChart() {
  const { frame } = useMarket();
  const { snapshots, portfolio } = useTerminal();

  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Baseline"> | null>(null);
  const lastTimeRef = useRef(0);
  const [ready, setReady] = useState(false);
  const [readout, setReadout] = useState<{ value: number; time: number } | null>(null);

  const startingCash = portfolio.starting_cash || 10000;

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    let disposed = false;

    void (async () => {
      const { BaselineSeries, CrosshairMode, LineStyle, createChart } = await import(
        "lightweight-charts"
      );
      if (disposed || !containerRef.current) return;

      const chart = createChart(containerRef.current, {
        autoSize: true,
        layout: {
          background: { color: CHART.surface },
          textColor: CHART.ink,
          fontFamily: chartFontFamily(),
          fontSize: 10,
          attributionLogo: false,
        },
        grid: { vertLines: { visible: false }, horzLines: { color: CHART.grid } },
        rightPriceScale: { borderColor: CHART.grid, scaleMargins: { top: 0.16, bottom: 0.12 } },
        timeScale: { borderColor: CHART.grid, timeVisible: true, secondsVisible: true },
        crosshair: {
          mode: CrosshairMode.Normal,
          vertLine: { color: CHART.crosshair, width: 1, style: LineStyle.Dotted, labelVisible: false },
          horzLine: { color: CHART.crosshair, width: 1, style: LineStyle.Dotted, labelBackgroundColor: CHART.grid },
        },
        handleScale: { axisPressedMouseMove: false },
      });

      const series = chart.addSeries(BaselineSeries, {
        baseValue: { type: "price", price: startingCash },
        lineWidth: 2,
        topLineColor: CHART.up,
        topFillColor1: CHART.upFill,
        topFillColor2: "rgba(31, 191, 146, 0.02)",
        bottomLineColor: CHART.down,
        bottomFillColor1: "rgba(224, 74, 46, 0.02)",
        bottomFillColor2: CHART.downFill,
        priceLineVisible: false,
        lastValueVisible: false,
        priceFormat: { type: "price", precision: 2, minMove: 0.01 },
        // A portfolio that has not moved yet is genuinely flat. Without a floor
        // the autoscale zooms into cent-level noise and the axis reads as
        // 9,999.94 to 10,000.06, which looks like a fault rather than calm.
        autoscaleInfoProvider: (original: () => AutoscaleInfo | null) => {
          const info = original();
          const floor = Math.max(startingCash * 0.005, 5);
          const low = Math.min(info?.priceRange?.minValue ?? startingCash, startingCash - floor);
          const high = Math.max(info?.priceRange?.maxValue ?? startingCash, startingCash + floor);
          return { ...info, priceRange: { minValue: low, maxValue: high } };
        },
      });

      chart.subscribeCrosshairMove((param) => {
        const point = param.seriesData.get(series) as { value?: number } | undefined;
        if (!param.point || point?.value == null || param.time == null) {
          setReadout(null);
          return;
        }
        setReadout({ value: point.value, time: param.time as number });
      });

      chartRef.current = chart;
      seriesRef.current = series;
      setReady(true);
    })();

    return () => {
      disposed = true;
      chartRef.current?.remove();
      chartRef.current = null;
      seriesRef.current = null;
      // Must reset: this effect re-runs when startingCash changes, and the
      // setData effect below is gated on `ready`. Leaving it true means the
      // rebuilt chart never gets its data and renders permanently empty.
      // Latent while starting_cash is a constant 10000; live the moment it
      // becomes configurable.
      setReady(false);
      lastTimeRef.current = 0;
    };
  }, [startingCash]);

  useEffect(() => {
    const series = seriesRef.current;
    if (!series || !ready) return;
    const points = snapshotsToChartPoints(snapshots);
    if (!points.length) return;
    series.setData(points.map((point) => ({ time: point.time as UTCTimestamp, value: point.value })));
    lastTimeRef.current = points[points.length - 1].time;
    chartRef.current?.timeScale().fitContent();
  }, [snapshots, ready]);

  // Extend the line with the live mark-to-market value between snapshots, so the
  // panel moves with the market instead of stepping every thirty seconds.
  useEffect(() => {
    const series = seriesRef.current;
    if (!series || !ready || !portfolio.total_value) return;
    const time = Math.floor(Date.now() / 1000);
    if (time < lastTimeRef.current) return; // guard against clock skew
    lastTimeRef.current = time;
    series.update({ time: time as UTCTimestamp, value: portfolio.total_value });
  }, [frame, portfolio.total_value, ready]);

  return (
    <Panel
      label="Portfolio value"
      testId="pnl-chart"
      meta={
        <>
          <span className="num font-mono text-[13px] text-ink">
            {formatUsd(portfolio.total_value)}
          </span>
          <Delta value={portfolio.total_return_percent} className="text-[11px]" />
        </>
      }
      bodyClassName="relative"
    >
      <div ref={containerRef} className="absolute inset-0" data-testid="pnl-chart-canvas" />
      {snapshots.length === 0 ? (
        <p
          className="pointer-events-none absolute inset-0 flex items-center justify-center text-ink-mute"
          data-testid="pnl-chart-empty"
        >
          No snapshots yet.
        </p>
      ) : null}
      {readout ? (
        <div
          className="pointer-events-none absolute top-2 left-2 flex gap-3 border border-line-strong bg-rail/95 px-2 py-1 text-[11px]"
          data-testid="pnl-chart-readout"
        >
          <span className="num font-mono text-ink">{formatUsd(readout.value)}</span>
          <span className="num text-ink-mute">{formatClock(readout.time)}</span>
        </div>
      ) : null}
    </Panel>
  );
}
