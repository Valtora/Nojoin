import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

interface SettingsRowProps {
  /** Registry entry id, used as the anchor a search result scrolls to. */
  id?: string;
  label: ReactNode;
  description?: ReactNode;
  /** Optional leading icon, sized by the caller. */
  icon?: ReactNode;
  /** Trailing badge next to the label, for status or scope. */
  badge?: ReactNode;
  /**
   * Stack the control beneath the label instead of beside it. For controls that
   * need the full width: textareas, editors, multi-line pickers.
   */
  stacked?: boolean;
  className?: string;
  controlClassName?: string;
  children?: ReactNode;
}

/**
 * The unit a settings page is made of: label and description on the left, the
 * control on the right, hairline separated from its neighbours by the parent
 * card. It draws no border and no background of its own, which is what keeps a
 * page to two surface levels.
 */
export default function SettingsRow({
  id,
  label,
  description,
  icon,
  badge,
  stacked = false,
  className,
  controlClassName,
  children,
}: SettingsRowProps) {
  return (
    <div
      id={id}
      className={cn(
        "settings-cell px-5 py-4 sm:px-6",
        stacked
          ? "space-y-3"
          : "flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between sm:gap-6",
        "scroll-mt-24",
        className,
      )}
    >
      <div className={cn("min-w-0", !stacked && "sm:max-w-md")}>
        <div className="flex flex-wrap items-center gap-2">
          {icon}
          <span className="text-sm font-medium text-gray-900 dark:text-white">
            {label}
          </span>
          {badge}
        </div>

        {description && (
          <p className="mt-1 text-xs leading-5 contrast-helper">{description}</p>
        )}
      </div>

      {children && (
        <div
          className={cn(
            stacked ? "w-full" : "w-full shrink-0 sm:w-auto sm:min-w-56 sm:max-w-sm",
            controlClassName,
          )}
        >
          {children}
        </div>
      )}
    </div>
  );
}
