import type { ReactNode } from "react";

/**
 * A terminal panel. Panels sit flush against one another on the page grid and
 * are separated by a single hairline, so a panel carries no border, no radius
 * and no shadow of its own — only its header rail.
 */
export function Panel({
  label,
  meta,
  children,
  testId,
  bodyClassName = "",
  emphasis = false,
}: {
  label: string;
  meta?: ReactNode;
  children: ReactNode;
  testId?: string;
  bodyClassName?: string;
  /** Set on a panel whose subject is the headline, not the panel's function. */
  emphasis?: boolean;
}) {
  return (
    <section className="flex min-h-0 min-w-0 flex-col bg-panel" data-testid={testId}>
      <header className="flex h-7 shrink-0 items-center gap-3 border-b border-line bg-rail px-3">
        <h2
          className={
            emphasis
              ? "font-mono text-[13px] font-medium tracking-tight text-ink"
              : "panel-label"
          }
        >
          {label}
        </h2>
        {meta ? <div className="ml-auto flex items-center gap-3">{meta}</div> : null}
      </header>
      <div className={`min-h-0 flex-1 ${bodyClassName}`}>{children}</div>
    </section>
  );
}
