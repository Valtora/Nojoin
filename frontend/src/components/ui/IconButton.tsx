import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";
import { Loader2 } from "lucide-react";

import { cn } from "@/lib/cn";
import type { ButtonVariant } from "./Button";

export type IconButtonSize = "sm" | "md" | "lg";

interface IconButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children"> {
  /** Required: the button has no text, so this is the only name it has. */
  "aria-label": string;
  icon: ReactNode;
  variant?: ButtonVariant;
  size?: IconButtonSize;
  loading?: boolean;
}

const VARIANTS: Record<ButtonVariant, string> = {
  primary:
    "bg-action text-action-on border border-transparent hover:bg-action-hover active:bg-action-active",
  secondary:
    "bg-surface-card text-foreground border border-control-border hover:bg-surface-inset",
  ghost:
    "bg-transparent text-contrast-icon-muted border border-transparent hover:bg-surface-inset hover:text-foreground",
  danger:
    "bg-transparent text-danger-text border border-transparent hover:bg-surface-inset hover:text-danger-text-hover",
};

/**
 * Sizes are expressed as a fixed box rather than as padding, because the
 * smaller ones have to keep a touch target the icon inside them does not fill.
 * `sm` renders a 16px glyph inside a 40px box: the glyph is small, the target
 * is not, which is the fix for the card and toolbar actions that were
 * previously unhittable on a phone.
 */
const SIZES: Record<IconButtonSize, string> = {
  sm: "h-10 w-10 [&_svg]:h-4 [&_svg]:w-4",
  md: "h-11 w-11 [&_svg]:h-5 [&_svg]:w-5",
  lg: "h-12 w-12 [&_svg]:h-6 [&_svg]:w-6",
};

const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  {
    icon,
    variant = "ghost",
    size = "md",
    loading = false,
    disabled,
    className,
    type = "button",
    ...rest
  },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={cn(
        "inline-flex shrink-0 items-center justify-center rounded-lg",
        "transition-colors duration-150",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring",
        "disabled:cursor-not-allowed disabled:opacity-60",
        VARIANTS[variant],
        SIZES[size],
        className,
      )}
      {...rest}
    >
      {loading ? <Loader2 aria-hidden="true" className="animate-spin" /> : icon}
    </button>
  );
});

export default IconButton;
