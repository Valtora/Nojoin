import { forwardRef, useId, type ReactNode, type SelectHTMLAttributes } from "react";
import { ChevronDown } from "lucide-react";

import { cn } from "@/lib/cn";
import { fieldChrome, type FieldSize } from "./Input";

interface SelectProps extends Omit<SelectHTMLAttributes<HTMLSelectElement>, "size"> {
  label?: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  fieldSize?: FieldSize;
}

/**
 * A native select wearing the shared field chrome. Native rather than a custom
 * listbox because the mobile pass wants the platform picker on a phone, and
 * because a native select needs no focus management to be reachable. The
 * built-in arrow is suppressed so the glyph matches the rest of the icon set.
 */
const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { label, hint, error, fieldSize = "md", className, id, children, ...rest },
  ref,
) {
  const generatedId = useId();
  const selectId = id ?? generatedId;
  const messageId = `${selectId}-message`;
  const message = error ?? hint;

  return (
    <div className="w-full">
      {label && (
        <label htmlFor={selectId} className="mb-1.5 block text-sm font-medium text-contrast-muted">
          {label}
        </label>
      )}
      <div className="relative">
        <select
          ref={ref}
          id={selectId}
          aria-invalid={error ? true : undefined}
          aria-describedby={message ? messageId : undefined}
          className={cn(
            fieldChrome(fieldSize, Boolean(error)),
            "cursor-pointer appearance-none pr-9",
            className,
          )}
          {...rest}
        >
          {children}
        </select>
        <ChevronDown
          aria-hidden="true"
          className="pointer-events-none absolute inset-y-0 right-3 my-auto h-4 w-4 text-contrast-icon-muted"
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

export default Select;
