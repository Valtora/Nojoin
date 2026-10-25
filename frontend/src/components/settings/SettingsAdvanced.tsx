"use client";

import { ButtonBase } from "@/components/ui/nativeBases";
import { useId, useState, type ReactNode } from "react";
import { useOnDepsChange } from "@/lib/useOnDepsChange";
import { AlertTriangle, ChevronRight } from "lucide-react";

import { cn } from "@/lib/cn";

interface SettingsAdvancedProps {
  /**
   * Settings inside that differ from their shipped default. Surfaced on the
   * collapsed header so a changed value never hides silently — the case where
   * a disclosure causes confusion months later.
   */
  changedCount?: number;
  /** Settings inside in a state that stops something working. */
  attentionCount?: number;
  /**
   * Opens the block whenever the value changes to a positive number. Bumped
   * when a search result or an in-page link points inside, so neither can land
   * on something the page then refuses to show, even after the block was
   * closed again.
   */
  openRequest?: number;
  className?: string;
  children: ReactNode;
}

/**
 * The Advanced gate: one collapsible block at the foot of a category.
 *
 * Deliberately not persisted. It starts collapsed on every visit, because the
 * alternative — remembered state — is invisible, differs per device, and makes
 * two people looking at the same installation see different pages.
 */
export default function SettingsAdvanced({
  changedCount = 0,
  attentionCount = 0,
  openRequest = 0,
  className,
  children,
}: SettingsAdvancedProps) {
  const [open, setOpen] = useState(openRequest > 0);
  const contentId = useId();

  useOnDepsChange([openRequest], () => {
    if (openRequest > 0) setOpen(true);
  });

  return (
    // A group, not a container: the disclosure toggles sibling cards into view
    // rather than wrapping them, so opening Advanced never produces a card
    // inside a card.
    <section className={cn("space-y-4", className)}>
      <ButtonBase
        data-ui-surface="card"
        type="button"
        onClick={() => setOpen((previous) => !previous)}
        aria-expanded={open}
        aria-controls={contentId}
        className="settings-card flex w-full items-center justify-between gap-3 px-5 py-4 text-left transition-colors hover:bg-surface-inset focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring sm:px-6"
      >
        <span className="flex items-center gap-2">
          <ChevronRight
            className={cn(
              "h-4 w-4 contrast-icon-muted transition-transform",
              open && "rotate-90",
            )}
            aria-hidden="true"
          />
          <span className="text-sm font-medium text-foreground">
            Advanced
          </span>
        </span>

        {(attentionCount > 0 || changedCount > 0) && (
          <span className="flex flex-wrap items-center justify-end gap-x-3 gap-y-1 text-xs font-medium">
            {attentionCount > 0 && (
              <span className="inline-flex items-center gap-1 text-status-warning-fg">
                <AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" />
                {attentionCount === 1
                  ? "1 needs attention"
                  : `${attentionCount} need attention`}
              </span>
            )}
            {changedCount > 0 && (
              <span className="text-action-text">{changedCount} changed</span>
            )}
          </span>
        )}
      </ButtonBase>

      {open && (
        <div id={contentId} className="space-y-4">
          {children}
        </div>
      )}
    </section>
  );
}
