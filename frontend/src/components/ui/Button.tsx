import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";
import { Loader2 } from "lucide-react";

import { cn } from "@/lib/cn";
import { DISABLED_LOOK, loadingButtonProps } from "./loadingButton";
import { ButtonBase } from "./nativeBases";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
export type ButtonSize = "sm" | "md" | "lg";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  /**
   * Shows a spinner and ignores clicks, while the button stays focusable. The
   * label stays, so the control does not resize. Outranks `disabled`.
   */
  loading?: boolean;
  /** Rendered before the label. Suppressed while loading, since the spinner takes its place. */
  iconLeft?: ReactNode;
  iconRight?: ReactNode;
  /** Stretches to the container width, which is the usual mobile treatment. */
  fullWidth?: boolean;
}

/**
 * Every variant is a flat fill or a bordered surface. None of them raise a
 * shadow: affordance comes from the background and border stepping, which is
 * what lets the same component read correctly in a theme that has no shadows.
 */
const VARIANTS: Record<ButtonVariant, string> = {
  primary:
    "bg-action text-action-on border border-transparent hover:bg-action-hover active:bg-action-active",
  secondary:
    "bg-surface-card text-foreground border border-control-border hover:bg-surface-inset active:bg-surface-inset",
  ghost:
    "bg-transparent text-contrast-muted border border-transparent hover:bg-surface-inset hover:text-foreground",
  danger:
    "bg-danger text-danger-on border border-transparent hover:bg-danger-hover active:bg-danger-active",
};

const SIZES: Record<ButtonSize, string> = {
  // Heights clear the 40px touch target from 44px down only at sm, which is
  // reserved for dense toolbars where the row itself is the target.
  sm: "h-8 gap-1.5 px-3 text-xs",
  md: "h-10 gap-2 px-4 text-sm",
  lg: "h-12 gap-2 px-6 text-base",
};

const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    variant = "secondary",
    size = "md",
    loading = false,
    iconLeft,
    iconRight,
    fullWidth = false,
    disabled,
    onClick,
    className,
    children,
    type = "button",
    ...rest
  },
  ref,
) {
  return (
    <ButtonBase
      ref={ref}
      type={type}
      {...rest}
      {...loadingButtonProps(loading, disabled, onClick)}
      chrome={variant === "ghost" ? "plain" : "action"}
      className={cn(
        "inline-flex items-center justify-center rounded-lg font-semibold whitespace-nowrap",
        "transition-colors duration-150",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring",
        DISABLED_LOOK,
        VARIANTS[variant],
        SIZES[size],
        fullWidth && "w-full",
        className,
      )}
    >
      {loading ? (
        <Loader2 aria-hidden="true" className="h-4 w-4 shrink-0 animate-spin" />
      ) : (
        iconLeft
      )}
      {children}
      {loading ? null : iconRight}
    </ButtonBase>
  );
});

export default Button;
