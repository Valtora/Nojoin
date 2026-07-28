import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

interface SettingsCardProps {
  /**
   * Registry entry id. Doubles as the anchor a search result scrolls to, so it
   * should match the id in settingsRegistry.ts where one exists.
   */
  id?: string;
  title: string;
  description?: ReactNode;
  badge?: ReactNode;
  /** Header-right slot for a control that acts on the whole card. */
  headerAside?: ReactNode;
  className?: string;
  contentClassName?: string;
  children: ReactNode;
}

/**
 * One card per section, and nothing nests inside it.
 *
 * The card is the only container a settings page draws: its children are rows
 * and blocks separated by hairlines, never further cards. Width is deliberately
 * not configurable — the page owns the column, so every card on a page shares
 * its edges.
 */
export default function SettingsCard({
  id,
  title,
  description,
  badge,
  headerAside,
  className,
  contentClassName,
  children,
}: SettingsCardProps) {
  return (
    <section id={id} className={cn("settings-card scroll-mt-24", className)}>
      <div className="flex flex-col gap-3 p-5 sm:flex-row sm:items-start sm:justify-between sm:gap-4 sm:p-6">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-base font-semibold text-gray-900 dark:text-white">
              {title}
            </h2>
            {badge}
          </div>

          {description && (
            <p className="mt-1.5 text-sm leading-6 contrast-helper">
              {description}
            </p>
          )}
        </div>

        {headerAside && <div className="shrink-0">{headerAside}</div>}
      </div>

      <div className={cn("settings-card-body border-t settings-divider", contentClassName)}>
        {children}
      </div>
    </section>
  );
}
