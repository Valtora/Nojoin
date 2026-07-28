"use client";

import { useEffect, useId, useState, type ReactNode } from "react";
import { ChevronRight } from "lucide-react";

import { cn } from "@/lib/cn";

interface SettingsAdvancedProps {
  /**
   * Settings inside that differ from their shipped default. Surfaced on the
   * collapsed header so a changed value never hides silently — the case where
   * a disclosure causes confusion months later.
   */
  changedCount?: number;
  /**
   * Opens the block and keeps it open. Set when a search result lives inside,
   * so search can never point at something the page then refuses to show.
   */
  forceOpen?: boolean;
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
  forceOpen = false,
  className,
  children,
}: SettingsAdvancedProps) {
  const [open, setOpen] = useState(forceOpen);
  const contentId = useId();

  useEffect(() => {
    if (forceOpen) {
      setOpen(true);
    }
  }, [forceOpen]);

  return (
    // A group, not a container: the disclosure toggles sibling cards into view
    // rather than wrapping them, so opening Advanced never produces a card
    // inside a card.
    <section className={cn("space-y-4", className)}>
      <button
        type="button"
        onClick={() => setOpen((previous) => !previous)}
        aria-expanded={open}
        aria-controls={contentId}
        className="settings-card flex w-full items-center justify-between gap-3 px-5 py-4 text-left transition-colors hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-orange-500 sm:px-6 dark:hover:bg-gray-900/60"
      >
        <span className="flex items-center gap-2">
          <ChevronRight
            className={cn(
              "h-4 w-4 contrast-icon-muted transition-transform",
              open && "rotate-90",
            )}
            aria-hidden="true"
          />
          <span className="text-sm font-medium text-gray-900 dark:text-white">
            Advanced
          </span>
        </span>

        {changedCount > 0 && (
          <span className="text-xs font-medium text-orange-700 dark:text-orange-300">
            {changedCount} changed
          </span>
        )}
      </button>

      {open && (
        <div id={contentId} className="space-y-4">
          {children}
        </div>
      )}
    </section>
  );
}
