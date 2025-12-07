import React, { forwardRef } from 'react';
import DatePicker, { DatePickerProps } from 'react-datepicker';
import { Calendar } from 'lucide-react';
import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

// Utility for merging tailwind classes
function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

interface ModernDatePickerProps extends Omit<DatePickerProps, 'onChange' | 'selectsRange' | 'selectsMultiple'> {
  onChange: (date: Date | null) => void;
  label?: string;
  className?: string;
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
        "flex h-10 w-full items-center justify-between rounded-md border border-gray-300 bg-white px-3 py-2 text-sm ring-offset-white placeholder:text-gray-500 focus:outline-none focus:ring-2 focus:ring-orange-500 focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100 dark:ring-offset-gray-950 dark:placeholder:text-gray-400 dark:focus:ring-orange-500",
        !value && "text-gray-500 dark:text-gray-400",
        className
      )}
    >
      <span className="truncate">{value || placeholder || "Select date"}</span>
      <Calendar className="ml-2 h-4 w-4 opacity-50" />
    </button>
  )
);

CustomInput.displayName = "CustomInput";

export default function ModernDatePicker({
  label,
  className,
  error,
  onChange,
  selected,
  placeholderText,
  ...props
}: ModernDatePickerProps) {
  return (
    <div className={cn("w-full", className)}>
      {label && (
        <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">
          {label}
        </label>
      )}
      <div className="relative">
        {/* @ts-expect-error: react-datepicker types are strict/broken in v8 */}
        <DatePicker
          selected={selected}
          onChange={(date: any) => onChange(date)}
          customInput={<CustomInput placeholder={placeholderText} />}
          wrapperClassName="w-full"
          calendarClassName="!bg-white dark:!bg-gray-800 !border-gray-200 dark:!border-gray-700 !font-sans !text-gray-900 dark:!text-gray-100 !rounded-lg !shadow-lg"
          dayClassName={(date) =>
            cn(
              "hover:!bg-orange-100 dark:hover:!bg-orange-900/30 !rounded-md",
              selected && date.getTime() === selected.getTime()
                ? "!bg-orange-600 !text-white hover:!bg-orange-700"
                : "dark:!text-gray-100"
            )
          }
          placeholderText={placeholderText}
          {...props}
        />
      </div>
      {error && <p className="mt-1 text-xs text-red-500">{error}</p>}
    </div>
  );
}
