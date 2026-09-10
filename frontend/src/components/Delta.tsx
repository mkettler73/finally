import { formatSignedPercent, formatSignedUsd, signOf } from "@/lib/format";

const GLYPH = { up: "▲", down: "▼", flat: "–" } as const;
const TONE = { up: "text-up", down: "text-down", flat: "text-ink-mute" } as const;

/**
 * A signed figure with its direction spelled out three ways: colour, an
 * explicit + or -, and a triangle. Direction is never carried by colour alone,
 * which is what makes the green/red pair safe for colourblind readers.
 */
export function Delta({
  value,
  kind = "percent",
  digits = 2,
  showGlyph = true,
  className = "",
  testId,
}: {
  value: number | null | undefined;
  kind?: "percent" | "usd";
  digits?: number;
  showGlyph?: boolean;
  className?: string;
  testId?: string;
}) {
  const sign = signOf(value);
  const text =
    kind === "usd" ? formatSignedUsd(value) : formatSignedPercent(value, digits);

  return (
    <span
      className={`num inline-flex items-center gap-1 ${TONE[sign]} ${className}`}
      data-testid={testId}
      data-direction={sign}
    >
      {showGlyph ? (
        <span aria-hidden className="text-[8px] leading-none">
          {GLYPH[sign]}
        </span>
      ) : null}
      {text}
    </span>
  );
}
