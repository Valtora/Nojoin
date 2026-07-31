import { forwardRef, useId, type InputHTMLAttributes, type ReactNode } from "react";

import { cn } from "@/lib/cn";

export type FieldSize = "sm" | "md" | "lg";

export const FIELD_SIZES: Record<FieldSize, string> = {
  sm: "h-8 px-2.5 text-xs",
  md: "h-10 px-3 text-sm",
  lg: "h-12 px-4 text-base",
};

/**
 * The chrome every field shares. Kept here rather than in each component so an
 * Input, a Select and a textarea cannot drift apart, which is what happened to
 * the SELECT_CLASS constants this replaces.
 */
export const fieldChrome = (size: FieldSize, invalid: boolean) =>
  cn(
    "w-full rounded-lg border bg-control-bg text-foreground",
    "placeholder:text-control-placeholder",
    "transition-colors duration-150",
    "focus-visible:outline-2 focus-visible:outline-offset-0 focus-visible:outline-focus-ring",
    "disabled:cursor-not-allowed disabled:border-control-disabled-border",
    "disabled:bg-control-disabled-bg disabled:text-control-disabled-fg",
    invalid ? "border-danger-text" : "border-control-border",
    FIELD_SIZES[size],
  );

interface InputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "size"> {
  label?: ReactNode;
  /** Shown under the field. Replaced by `error` when there is one. */
  hint?: ReactNode;
  error?: ReactNode;
  fieldSize?: FieldSize;
  /** Rendered inside the field's leading edge, e.g. a search glyph. */
  iconLeft?: ReactNode;
}

const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { label, hint, error, fieldSize = "md", iconLeft, className, id, ...rest },
  ref,
) {
  const generatedId = useId();
  const inputId = id ?? generatedId;
  const messageId = `${inputId}-message`;
  const message = error ?? hint;

  return (
    <div className="w-full">
      {label && (
        <label htmlFor={inputId} className="mb-1.5 block text-sm font-medium text-contrast-muted">
          {label}
        </label>
      )}
      <div className="relative">
        {iconLeft && (
          <span
            aria-hidden="true"
            className="pointer-events-none absolute inset-y-0 left-3 flex items-center text-contrast-icon-muted [&_svg]:h-4 [&_svg]:w-4"
          >
            {iconLeft}
          </span>
        )}
        <input
          ref={ref}
          id={inputId}
          aria-invalid={error ? true : undefined}
          aria-describedby={message ? messageId : undefined}
          className={cn(fieldChrome(fieldSize, Boolean(error)), iconLeft && "pl-9", className)}
          {...rest}
        />
      </div>
      {message && (
        <p
          id={messageId}
          className={cn("mt-1.5 text-xs", error ? "text-danger-text" : "text-contrast-helper")}
        >
          {message}
        </p>
      )}
    </div>
  );
});

export default Input;
