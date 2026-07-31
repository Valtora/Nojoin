import { forwardRef, type HTMLAttributes } from "react";

import { cn } from "@/lib/cn";

export type CardPadding = "none" | "sm" | "md" | "lg";

interface CardProps extends HTMLAttributes<HTMLDivElement> {
  /**
   * Switches the hover treatment on. A clickable card tints its background and
   * strengthens its border; it does not lift. Shadow-on-hover is the affordance
   * this design replaced, and it never worked in dark mode anyway, where there
   * is no resting shadow for a hover state to differ from.
   */
  interactive?: boolean;
  padding?: CardPadding;
}

const PADDING: Record<CardPadding, string> = {
  none: "",
  sm: "p-3",
  md: "p-4 sm:p-5",
  lg: "p-5 sm:p-6",
};

/**
 * The canonical resting surface, generalised from the settings card that this
 * whole design language was derived from: a solid fill, a 1px hairline, and a
 * 4%-alpha shadow that the dark theme resolves to `none` so surfaces separate
 * by lightness instead.
 */
const Card = forwardRef<HTMLDivElement, CardProps>(function Card(
  { interactive = false, padding = "md", className, children, ...rest },
  ref,
) {
  return (
    <div
      ref={ref}
      className={cn(
        "rounded-surface-panel border border-surface-border bg-surface-card shadow-card",
        PADDING[padding],
        interactive &&
          "cursor-pointer transition-colors duration-150 hover:border-action-border hover:bg-action-tint",
        interactive &&
          "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring",
        className,
      )}
      {...rest}
    >
      {children}
    </div>
  );
});

export default Card;
