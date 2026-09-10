"use client";

import { useEffect, useRef, useState } from "react";
import type { IChartApi, ISeriesApi, UTCTimestamp } from "lightweight-charts";

import { CHART, chartFontFamily } from "@/lib/colors";
import { formatClock, formatPrice } from "@/lib/format";
import { toChartPoints } from "@/lib/series";
import { useMarket } from "@/state/market";
import { useTerminal } from "@/state/terminal";
import { Delta } from "./Delta";
import { Panel } from "./Panel";

/** Below this many accumulated ticks the chart refits on every update. */
const FIT_UNTIL = 150;

interface Readout {
  x: number;
  price: number;
  time: number;
}

/**
 * The selected instrument, plotted from the ticks accumulated since page load.
 * One series, one hue: blue is identity here, so gain and loss colours stay
 * reserved for the P&L panels.
 */
export function PriceChart() {
  const { frame, seriesFor, pulses } = useMarket();
  const { selected, watchlist } = useTerminal();

  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Area"> | null>(null);
  // Last plotted time, so a non-monotonic tick is dropped rather than thrown on.
  const lastTimeRef = useRef(0);
  const [readout, setReadout] = useState<Readout | null>(null);
  const [ready, setReady] = useState(false);

  const tick = selected ? frame[selected] : undefined;
  const entry = watchlist.find((row) => row.ticker === selected);
  const sessionOpen = entry?.session_open ?? null;
  const price = tick?.price ?? entry?.price ?? null;
  const sessionPercent =
    price != null && sessionOpen != null && sessionOpen !== 0
      ? ((price - sessionOpen) / sessionOpen) * 100
      : (entry?.session_change_percent ?? null);

  // Mount the chart once. lightweight-charts is imported lazily so it is never
  // evaluated during the static prerender.
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    let disposed = false;

    void (async () => {
      const { AreaSeries, CrosshairMode, LineStyle, createChart } = await import(
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
        grid: {
          vertLines: { color: CHART.grid },
          horzLines: { color: CHART.grid },
        },
        rightPriceScale: { borderColor: CHART.grid, scaleMargins: { top: 0.12, bottom: 0.08 } },
        timeScale: {
          borderColor: CHART.grid,
          timeVisible: true,
          secondsVisible: true,
          rightOffset: 3,
          barSpacing: 6,
        },
        crosshair: {
          mode: CrosshairMode.Normal,
          vertLine: { color: CHART.crosshair, width: 1, style: LineStyle.Dotted, labelBackgroundColor: CHART.grid },
          horzLine: { color: CHART.crosshair, width: 1, style: LineStyle.Dotted, labelBackgroundColor: CHART.grid },
        },
        handleScale: { axisPressedMouseMove: false },
      });

      const series = chart.addSeries(AreaSeries, {
        lineColor: CHART.series,
        lineWidth: 2,
        topColor: CHART.seriesFillTop,
        bottomColor: CHART.seriesFillBottom,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerRadius: 4,
        crosshairMarkerBorderColor: CHART.surface,
        crosshairMarkerBackgroundColor: CHART.series,
      });

      chart.subscribeCrosshairMove((param) => {
        const value = param.seriesData.get(series) as { value?: number } | undefined;
        if (!param.point || value?.value == null || param.time == null) {
          setReadout(null);
          return;
        }
        setReadout({ x: param.point.x, price: value.value, time: param.time as number });
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
    };
  }, []);

  // Reload the whole series when the instrument changes.
  useEffect(() => {
    const series = seriesRef.current;
    if (!series || !selected || !ready) return;
    const points = toChartPoints(seriesFor(selected));
    series.setData(points.map((point) => ({ time: point.time as UTCTimestamp, value: point.value })));
    lastTimeRef.current = points.length ? points[points.length - 1].time : 0;
    chartRef.current?.timeScale().fitContent();
  }, [selected, ready, seriesFor]);

  // Append each new tick. `pulses` changes only when the price actually moved.
  useEffect(() => {
    const series = seriesRef.current;
    if (!series || !selected || !ready) return;
    const latest = frame[selected];
    if (!latest) return;

    // lightweight-charts throws "Cannot update oldest data" on a point older
    // than the last one plotted, and the throw happens inside this effect
    // with no error boundary above it, so React unmounts the tree and the
    // whole terminal goes blank. Not hypothetical: in Massive mode the
    // timestamp is the exchange sip_timestamp, which is not monotonic across
    // polls, and the degraded grouped-daily path publishes wall-clock now --
    // two different clocks feeding one series. PnlChart already guards this
    // way; PriceChart did not.
    const time = Math.floor(latest.timestamp);
    if (time < lastTimeRef.current) return;
    lastTimeRef.current = time;

    series.update({
      time: time as UTCTimestamp,
      value: latest.price,
    });
    // Until there are enough bars to fill the pane, keep refitting: a handful
    // of points pinned to the right edge reads as a broken chart.
    if (seriesFor(selected).length < FIT_UNTIL) chartRef.current?.timeScale().fitContent();
  }, [frame, selected, ready, pulses, seriesFor]);

  return (
    <Panel
      label={selected ?? "No instrument"}
      emphasis
      testId="price-chart"
      meta={
        <>
          <span className="num font-mono text-[15px] text-ink" data-testid="price-chart-last">
            {formatPrice(price)}
          </span>
          <Delta value={sessionPercent} testId="price-chart-change" className="text-[11px]" />
        </>
      }
      bodyClassName="relative"
    >
      <div ref={containerRef} className="absolute inset-0" data-testid="price-chart-canvas" />

      {!selected || seriesFor(selected).length < 2 ? (
        <p
          className="pointer-events-none absolute inset-0 flex items-center justify-center text-ink-mute"
          data-testid="price-chart-empty"
        >
          {selected
            ? `Collecting ticks for ${selected}…`
            : "Pick an instrument from the watchlist to chart it."}
        </p>
      ) : null}

      {readout ? (
        <div
          className="pointer-events-none absolute top-2 left-2 flex gap-3 border border-line-strong bg-rail/95 px-2 py-1 text-[11px]"
          data-testid="price-chart-readout"
        >
          <span className="num font-mono text-ink">{formatPrice(readout.price)}</span>
          <span className="num text-ink-mute">{formatClock(readout.time)}</span>
        </div>
      ) : null}
    </Panel>
  );
}
