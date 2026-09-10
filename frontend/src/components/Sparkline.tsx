import { CHART } from "@/lib/colors";
import { sparklinePath } from "@/lib/series";

/**
 * Price action since page load, drawn beside each watchlist row. It fills in
 * progressively as stream frames arrive, so the empty state is a baseline
 * rather than a blank cell.
 */
export function Sparkline({
  values,
  direction = "flat",
  width = 64,
  height = 20,
  testId,
}: {
  values: readonly number[];
  /**
   * Which way the row reads overall. Passed in rather than derived from the
   * points so the line and the percentage beside it never disagree.
   */
  direction?: "up" | "down" | "flat";
  width?: number;
  height?: number;
  testId?: string;
}) {
  const shape = sparklinePath(values, width, height);
  const stroke = shape
    ? direction === "up"
      ? CHART.up
      : direction === "down"
        ? CHART.down
        : CHART.flat
    : CHART.flat;

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={shape ? `Price action since page load, trending ${direction}` : "Awaiting prices"}
      data-testid={testId}
      data-points={values.length}
      className="overflow-visible"
    >
      {shape ? (
        <>
          <path
            d={shape.path}
            fill="none"
            stroke={stroke}
            strokeWidth={1.5}
            strokeLinecap="round"
            strokeLinejoin="round"
          />
          <circle cx={shape.lastX} cy={shape.lastY} r={1.75} fill={stroke} />
        </>
      ) : (
        <line
          x1={0}
          y1={height / 2}
          x2={width}
          y2={height / 2}
          stroke={CHART.grid}
          strokeWidth={1}
          strokeDasharray="2 3"
        />
      )}
    </svg>
  );
}
