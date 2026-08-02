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
  className?: string;
  controlClassName?: string;
  children?: ReactNode;
}

/**
 * The unit a settings page is made of: label and description on the left, the
 * control on the right, hairline separated from its neighbours by the parent
 * card. It draws no border and no background of its own, which is what keeps a
 * page to two surface levels.
 *
 * The row is its own container: rows land inside provider tiles and grid
 * columns whose width the viewport cannot see, so the beside-layout keys off
 * the row's own width. Below the threshold the row stacks, which is also the
 * phone layout, so narrow columns and small screens share one mechanism.
 */
export default function SettingsRow({
  id,
  label,
  description,
  icon,
  badge,
  className,
  controlClassName,
  children,
}: SettingsRowProps) {
  return (
    <div
      id={id}
      // Padding stays on the viewport scale: it must line up with the card
      // gutter, which globals.css steps at the same sm breakpoint.
      className={cn(
        "@container settings-cell px-5 py-4 sm:px-6 scroll-mt-24",
        className,
      )}
    >
      {/* 26rem = the control's 14rem floor + the 1.5rem gap + at least
          10.5rem for the label before the two sit side by side. */}
      <div className="flex flex-col gap-3 @min-[26rem]:flex-row @min-[26rem]:items-start @min-[26rem]:justify-between @min-[26rem]:gap-6">
        <div className="min-w-0 @min-[26rem]:max-w-md">
          <div className="flex flex-wrap items-center gap-2">
            {icon}
            <span className="text-sm font-medium text-foreground">
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
              "w-full shrink-0 @min-[26rem]:w-auto @min-[26rem]:min-w-56 @min-[26rem]:max-w-sm",
              controlClassName,
            )}
          >
            {children}
          </div>
        )}
      </div>
    </div>
  );
}
