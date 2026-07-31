import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

interface SettingsBlockProps {
  /** Registry entry id, used as the anchor a search result scrolls to. */
  id?: string;
  /** Optional heading for the block, matching a row's label weight. */
  label?: ReactNode;
  description?: ReactNode;
  /** Header-right slot, for a control that acts on the whole block. */
  aside?: ReactNode;
  /**
   * Give the content a subtly inset background. For readouts that benefit from
   * being visually recessed — meters, consoles, previews. It steps in the same
   * direction as the card does from the page, so depth stays legible.
   */
  inset?: boolean;
  className?: string;
  contentClassName?: string;
  children: ReactNode;
}

/**
 * The escape hatch from the row model, for content that is not
 * label-plus-control: live meters, waveforms, tables, log consoles, wizards.
 *
 * It spans the card's full width and draws no border of its own, so composite
 * UI has a defined home instead of each component improvising a nested panel —
 * which is how the previous three-level surface stack accumulated.
 */
export default function SettingsBlock({
  id,
  label,
  description,
  aside,
  inset = false,
  className,
  contentClassName,
  children,
}: SettingsBlockProps) {
  const hasHeader = Boolean(label || description || aside);

  return (
    <div id={id} className={cn("settings-cell px-5 py-4 sm:px-6 scroll-mt-24", className)}>
      {hasHeader && (
        <div className="mb-3 flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between sm:gap-4">
          <div className="min-w-0">
            {label && (
              <span className="text-sm font-medium text-foreground">
                {label}
              </span>
            )}
            {description && (
              <p className="mt-1 text-xs leading-5 contrast-helper">
                {description}
              </p>
            )}
          </div>
          {aside && <div className="shrink-0">{aside}</div>}
        </div>
      )}

      <div
        className={cn(
          inset && "settings-inset rounded-xl p-4",
          contentClassName,
        )}
      >
        {children}
      </div>
    </div>
  );
}
