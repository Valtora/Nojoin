"use client";

import { ChevronRight, Loader2 } from "lucide-react";

import { cn } from "@/lib/cn";

/** The shared furniture of the analytics tab.
 *
 * The tab's own content region is already a card, so a panel inside it is the
 * third level of a two-level surface stack. It therefore gets spacing and a
 * rule rather than another fill and border, which is what DESIGN.md's nesting
 * table prescribes and what the tab did not do.
 */

export const Section = ({
  title,
  hint,
  className,
  children,
}: {
  title: string;
  hint?: string;
  className?: string;
  children: React.ReactNode;
}) => (
  <section className={cn("flex min-w-0 flex-col", className)}>
    <h3 className="text-sm font-semibold text-foreground">{title}</h3>
    {hint && <p className="mt-0.5 text-xs text-contrast-helper">{hint}</p>}
    <div className="mt-3">{children}</div>
  </section>
);

/** A row of sections, ruled off from the one above it. */
export const Band = ({
  className,
  children,
}: {
  className?: string;
  children: React.ReactNode;
}) => (
  <div
    className={cn(
      "grid gap-x-6 gap-y-5 border-t border-surface-divider pt-5 first:border-t-0 first:pt-0",
      className,
    )}
  >
    {children}
  </div>
);

/** How a figure was arrived at, one click away from the figure itself.
 *
 * Every one of these paragraphs is load-bearing -- each is the disclosure that
 * keeps a figure from claiming more than it measures -- and none of them is
 * read twice. Collapsed, they stay attached to the figure they qualify and
 * stop the surface reading as an essay. A native details element rather than a
 * tooltip, because a hover target discloses nothing on a phone.
 */
export const Note = ({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) => (
  <details className="group mt-3">
    <summary className="inline-flex cursor-pointer list-none items-center gap-1 rounded-sm text-xs text-contrast-helper transition-colors hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring [&::-webkit-details-marker]:hidden">
      <ChevronRight
        className="h-3.5 w-3.5 shrink-0 transition-transform group-open:rotate-90"
        aria-hidden="true"
      />
      {label}
    </summary>
    <div className="mt-2 space-y-2 border-l-2 border-surface-divider pl-3 text-xs leading-relaxed text-contrast-helper">
      {children}
    </div>
  </details>
);

/** One measured figure in the strip at the top of the tab. */
export const StatTile = ({ label, value }: { label: string; value: string }) => (
  <div className="rounded-surface-subtle bg-surface-inset px-3 py-2">
    <p className="text-xs text-contrast-helper">{label}</p>
    <p className="mt-0.5 text-lg font-semibold tabular-nums text-foreground">
      {value}
    </p>
  </div>
);

/** A tier that has not been produced yet, with the way to produce it.
 *
 * Shared by the two on-request tiers so their empty, error and busy states
 * cannot drift apart. Neither is a failure: both cost something to run, so not
 * having run is the normal resting state.
 */
export const Prompt = ({
  message,
  actionLabel,
  actionIcon,
  onAction,
  busy,
}: {
  message: string;
  actionLabel?: string;
  actionIcon?: React.ReactNode;
  onAction?: () => void;
  busy?: boolean;
}) => (
  <div className="flex flex-col items-start gap-2 rounded-surface-subtle bg-surface-inset px-3 py-3">
    <p className="text-xs text-contrast-helper">{message}</p>
    {actionLabel && onAction && (
      <button
        type="button"
        onClick={onAction}
        disabled={busy}
        className="inline-flex items-center gap-2 rounded-lg bg-action px-3 py-1.5 text-sm font-medium text-action-on transition-colors hover:bg-action-hover disabled:opacity-60"
      >
        {busy ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
        ) : (
          actionIcon
        )}
        {actionLabel}
      </button>
    )}
  </div>
);

/** Work that has been asked for and is under way. */
export const Working = ({ message }: { message: string }) => (
  <p
    className="flex items-start gap-2 text-xs text-contrast-helper"
    role="status"
  >
    <Loader2
      className="mt-0.5 h-3.5 w-3.5 shrink-0 animate-spin text-action-text"
      aria-hidden="true"
    />
    {message}
  </p>
);

/** A tier whose stored figures describe a transcript that has since changed. */
export const StaleBanner = ({
  message,
  actionLabel,
  onAction,
}: {
  message: string;
  actionLabel: string;
  onAction: () => void;
}) => (
  <p className="rounded-surface-subtle border border-status-warning-border bg-status-warning-bg px-3 py-2 text-xs text-status-warning-fg">
    {message}{" "}
    <button
      type="button"
      onClick={onAction}
      className="font-medium underline underline-offset-2 hover:no-underline"
    >
      {actionLabel}
    </button>
  </p>
);
