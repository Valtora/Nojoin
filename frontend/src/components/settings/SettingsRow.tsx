import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

import { useScopeBadge } from "./SettingsScopeBadge";

interface SettingsRowProps {
  /** Registry entry id, used as the anchor a search result scrolls to. */
  id?: string;
  label: ReactNode;
  /** Id for the label, so a group of controls can be named by it. */
  labelId?: string;
  description?: ReactNode;
  /** Id for the description, so a control can be described by it. */
  descriptionId?: string;
  /**
   * Id of the row's single control. The label then becomes a real `<label>`
   * for it, and the description defaults to `${controlId}-help`, so the control
   * only needs `aria-describedby` pointing there.
   */
  controlId?: string;
  /** Status badge next to the label. A scope badge from the registry follows it. */
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
  labelId,
  description,
  descriptionId,
  controlId,
  badge,
  className,
  controlClassName,
  children,
}: SettingsRowProps) {
  const scopeBadge = useScopeBadge(id);

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
            {controlId ? (
              <label
                id={labelId}
                htmlFor={controlId}
                className="text-sm font-medium text-foreground"
              >
                {label}
              </label>
            ) : (
              <span id={labelId} className="text-sm font-medium text-foreground">
                {label}
              </span>
            )}
            {badge}
            {scopeBadge}
          </div>

          {description && (
            // A div, so a description can hold a list or a disclosure.
            <div
              id={descriptionId ?? (controlId ? `${controlId}-help` : undefined)}
              className="mt-1 text-xs leading-5 contrast-helper"
            >
              {description}
            </div>
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
