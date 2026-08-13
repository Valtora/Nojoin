import React, { forwardRef } from 'react';
import DatePicker, { DatePickerProps } from 'react-datepicker';
import { Calendar } from 'lucide-react';

import { cn } from '@/lib/cn';

interface ModernDatePickerProps extends Omit<DatePickerProps, 'onChange' | 'selectsRange' | 'selectsMultiple'> {
  onChange: (date: Date | null) => void;
  label?: string;
  className?: string;
  inputClassName?: string;
  placeholder?: string;
  error?: string;
}

interface CustomInputProps extends React.DetailedHTMLProps<React.ButtonHTMLAttributes<HTMLButtonElement>, HTMLButtonElement> {
  placeholder?: string;
}

const CustomInput = forwardRef<HTMLButtonElement, CustomInputProps>(
  ({ value, onClick, className, placeholder }, ref) => (
    <button
      type="button"
      onClick={onClick}
      ref={ref}
      className={cn(
        "flex h-10 w-full items-center justify-between rounded-lg border border-control-border bg-control-bg px-3 py-2 text-sm text-foreground",
        "focus-visible:outline-2 focus-visible:outline-offset-0 focus-visible:outline-focus-ring",
        "disabled:cursor-not-allowed disabled:border-control-disabled-border disabled:bg-control-disabled-bg disabled:text-control-disabled-fg",
        !value && "text-control-placeholder",
        className
      )}
    >
      <span className="truncate">{value || placeholder || "Select date"}</span>
      <Calendar aria-hidden="true" className="ml-2 h-4 w-4 text-contrast-icon-muted" />
    </button>
  )
);

CustomInput.displayName = "CustomInput";

/**
 * Caps the calendar to the space on the side the popover was actually placed,
 * so it scrolls inside itself rather than off the top of a short window.
 *
 * floating-ui ships `size` for this, but importing it here would bind a second
 * copy of the library: react-datepicker resolves its own nested
 * @floating-ui/react at 0.27 while the tree hoists 0.26 for Headless UI, and
 * middleware from the wrong instance is not something to rely on. A middleware
 * is a plain object, and the rule is one line once the placement is known.
 *
 * It reads the trigger's own rect rather than the rects floating-ui passes,
 * because those are expressed relative to the offset parent while this needs
 * window coordinates. It runs last, so the placement it sees is final. Writing
 * a custom property rather than a height keeps the value where the stylesheet
 * can use it and leaves the popover's own box to the library.
 */
const boundToViewport = {
  name: "nojoinBoundToViewport",
  fn({
    placement,
    elements,
  }: {
    placement: string;
    elements: {
      // Widened from DOMRect: floating-ui's reference may be a virtual element,
      // whose rect type is structural rather than a DOMRect.
      reference: { getBoundingClientRect: () => { top: number; bottom: number } };
      floating: HTMLElement;
    };
  }) {
    const anchor = elements.reference.getBoundingClientRect();
    const gap = 16;
    const available = placement.startsWith("top")
      ? anchor.top - gap
      : window.innerHeight - anchor.bottom - gap;
    elements.floating.style.setProperty(
      "--nj-picker-max-height",
      `${Math.max(200, Math.round(available))}px`,
    );
    return {};
  },
};

export default function ModernDatePicker({
  label,
  className,
  inputClassName,
  error,
  onChange,
  selected,
  placeholderText,
  ...props
}: ModernDatePickerProps) {
  return (
    <div className={cn("w-full", className)}>
      {label && (
        <label className="mb-1.5 block text-sm font-medium text-contrast-muted">
          {label}
        </label>
      )}
      <div className="relative">
        {/* @ts-expect-error: react-datepicker types are strict/broken in v8 */}
        <DatePicker
          selected={selected}
          onChange={(date: Date | null) => onChange(date)}
          customInput={<CustomInput placeholder={placeholderText} className={inputClassName} />}
          wrapperClassName="w-full"
          // Position against the viewport, not the nearest scroll box. Every
          // modal panel hides its own overflow and scrolls its body, so an
          // absolutely positioned popper is a child of that box: the library
          // measures the free space inside it, flips the calendar upwards for
          // want of room, and the panel then clips whatever crosses its header.
          // The fixed strategy takes the popper out of that box; globals.css
          // bounds it against the viewport instead.
          popperProps={{ strategy: "fixed" }}
          popperModifiers={[boundToViewport]}
          // react-datepicker renders its own DOM, so the calendar is themed by
          // overriding its classes here and in the vendor block in globals.css
          // rather than by composing tokens the way the rest of the UI does.
          calendarClassName="!bg-surface-float !border-surface-float-border !font-sans !text-foreground !rounded-lg !shadow-float"
          dayClassName={(date) =>
            cn(
              "!rounded-md hover:!bg-action-tint",
              selected && date.getTime() === selected.getTime()
                ? "!bg-action !text-action-on hover:!bg-action-hover"
                : "!text-foreground"
            )
          }
          placeholderText={placeholderText}
          {...props}
        />
      </div>
      {error && <p className="mt-1.5 text-xs text-danger-text">{error}</p>}
    </div>
  );
}
