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
