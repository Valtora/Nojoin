"use client";

import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import DatePicker, { type ReactDatePickerCustomHeaderProps } from "react-datepicker";
import { addDays, format, isSameDay, isSameMonth } from "date-fns";
import { Calendar, ChevronLeft, ChevronRight, Clock3, X } from "lucide-react";
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

import {
  formatTimeZoneDate,
  fromTimeZoneDate,
  resolveTimeZone,
  toTimeZoneDate,
} from "@/lib/timezone";

function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

const DEFAULT_HOUR = 17;
const DEFAULT_MINUTE = 0;
const TIME_PRESETS = [
  { label: "9 AM", value: "09:00" },
  { label: "Noon", value: "12:00" },
  { label: "5 PM", value: "17:00" },
  { label: "6 PM", value: "18:00" },
];

type PickerPosition =
  | {
      mode: "popover";
      top: number;
      left: number;
    }
  | {
      mode: "sheet";
    };

interface TaskDeadlineTimeInputProps {
  value?: string;
  onChange?: (time: string) => void;
  timeZoneLabel: string;
}

interface TaskDeadlinePickerProps {
  value: Date | null;
  onChange: (date: Date | null) => Promise<boolean | void> | boolean | void;
  timeZone?: string;
  disabled?: boolean;
  className?: string;
  inputClassName?: string;
  placeholderText?: string;
}

function applyTime(date: Date, hours: number, minutes: number): Date {
  const next = new Date(date);
  next.setHours(hours, minutes, 0, 0);
  return next;
}

function buildSuggestedDeadline(reference = new Date()): Date {
  return applyTime(reference, DEFAULT_HOUR, DEFAULT_MINUTE);
}

function preserveTime(baseDate: Date, reference: Date | null): Date {
  if (!reference) {
    return buildSuggestedDeadline(baseDate);
  }

  return applyTime(baseDate, reference.getHours(), reference.getMinutes());
}

function TaskDeadlineTimeInput({
  value = "",
  onChange,
  timeZoneLabel,
}: TaskDeadlineTimeInputProps) {
  return (
    <div className="space-y-3 rounded-[1.1rem] border border-gray-200 bg-gray-50/90 px-3 py-3 dark:border-gray-700 dark:bg-gray-900/80">
      <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.18em] text-gray-500 dark:text-gray-400">
        <Clock3 className="h-3.5 w-3.5" />
        Time
      </div>

      <div className="flex items-center gap-2">
        <input
          type="time"
          step={900}
          value={value}
          onChange={(event) => onChange?.(event.target.value)}
          className="h-10 min-w-0 flex-1 rounded-xl border border-gray-300 bg-white px-3 text-sm font-medium text-gray-900 outline-none transition-colors focus:border-orange-500 dark:border-gray-600 dark:bg-gray-950 dark:text-gray-100"
        />
        <span className="text-xs text-gray-500 dark:text-gray-400">{timeZoneLabel}</span>
      </div>

      <div className="flex flex-wrap gap-2">
        {TIME_PRESETS.map((preset) => {
          const isActive = value === preset.value;

          return (
            <button
              key={preset.value}
              type="button"
              onClick={() => onChange?.(preset.value)}
              className={cn(
                "rounded-full border px-3 py-1.5 text-xs font-semibold transition-colors",
                isActive
                  ? "border-orange-500 bg-orange-500 text-white"
                  : "border-gray-300 bg-white text-gray-700 hover:border-orange-300 hover:text-orange-700 dark:border-gray-600 dark:bg-gray-950 dark:text-gray-200 dark:hover:border-orange-400 dark:hover:text-orange-200",
              )}
            >
              {preset.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}

export default function TaskDeadlinePicker({
  value,
  onChange,
  timeZone,
  disabled = false,
  className,
  inputClassName,
  placeholderText = "Add deadline",
}: TaskDeadlinePickerProps) {
  const panelId = useId();
  const buttonRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const [mounted, setMounted] = useState(false);
  const [isOpen, setIsOpen] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [position, setPosition] = useState<PickerPosition | null>(null);
  const resolvedTimeZone = resolveTimeZone(timeZone);
  const zonedValue = value ? toTimeZoneDate(value, resolvedTimeZone) : null;
  const zonedNow = toTimeZoneDate(new Date(), resolvedTimeZone);
  const [draftValue, setDraftValue] = useState<Date | null>(zonedValue);

  useEffect(() => {
    setMounted(true);
  }, []);

  useEffect(() => {
    if (!isOpen) {
      setDraftValue(zonedValue ? new Date(zonedValue) : null);
    }
  }, [zonedValue, isOpen]);

  useEffect(() => {
    if (disabled) {
      setIsOpen(false);
      setPosition(null);
    }
  }, [disabled]);

  const closePicker = () => {
    setIsOpen(false);
    setPosition(null);
    buttonRef.current?.focus();
  };

  useEffect(() => {
    if (!isOpen) {
      return;
    }

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closePicker();
      }
    };

    document.addEventListener("keydown", handleKeyDown);

    return () => {
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [isOpen]);

  useLayoutEffect(() => {
    if (!mounted || !isOpen || !buttonRef.current || !panelRef.current) {
      return;
    }

    const updatePosition = () => {
      if (!buttonRef.current || !panelRef.current) {
        return;
      }

      if (window.innerWidth < 640) {
        setPosition({ mode: "sheet" });
        return;
      }

      const viewportPadding = 16;
      const offset = 12;
      const triggerRect = buttonRef.current.getBoundingClientRect();
      const panelRect = panelRef.current.getBoundingClientRect();

      let left = triggerRect.left;
      if (left + panelRect.width > window.innerWidth - viewportPadding) {
        left = window.innerWidth - panelRect.width - viewportPadding;
      }
      left = Math.max(viewportPadding, left);

      let top = triggerRect.bottom + offset;
      if (top + panelRect.height > window.innerHeight - viewportPadding) {
        top = triggerRect.top - panelRect.height - offset;
      }
      top = Math.max(viewportPadding, top);

      setPosition({ mode: "popover", top, left });
    };

    const frame = window.requestAnimationFrame(updatePosition);
    window.addEventListener("resize", updatePosition);
    window.addEventListener("scroll", updatePosition, true);

    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener("resize", updatePosition);
      window.removeEventListener("scroll", updatePosition, true);
    };
  }, [mounted, isOpen, draftValue]);

  const resolvedDraft = draftValue ?? buildSuggestedDeadline(zonedNow);
  const draftInstant = draftValue
    ? fromTimeZoneDate(draftValue, resolvedTimeZone)
    : null;
  const triggerLabel = value
    ? formatTimeZoneDate(value, resolvedTimeZone, "EEE d MMM, h:mm aa")
    : placeholderText;
  const hasSavedDeadline = Boolean(value);
  const quickDates = [
    { label: "Today", date: preserveTime(zonedNow, resolvedDraft) },
    { label: "Tomorrow", date: preserveTime(addDays(zonedNow, 1), resolvedDraft) },
    { label: "Next week", date: preserveTime(addDays(zonedNow, 7), resolvedDraft) },
  ];
  const saveDisabled =
    isSaving ||
    disabled ||
    (Boolean(value) && draftInstant?.getTime() === value?.getTime());

  const handleOpen = () => {
    if (disabled) {
      return;
    }

    setDraftValue(zonedValue ? new Date(zonedValue) : buildSuggestedDeadline(zonedNow));
    setIsOpen(true);
  };

  const handleSubmit = async (nextValue: Date | null) => {
    setIsSaving(true);

    try {
      const nextInstant = nextValue
        ? fromTimeZoneDate(nextValue, resolvedTimeZone)
        : null;
      const result = await onChange(nextInstant);
      if (result !== false) {
        closePicker();
      }
    } finally {
      setIsSaving(false);
    }
  };

  const overlay =
    mounted && isOpen
      ? createPortal(
          <div className="fixed inset-0 z-[1000]">
            <div
              aria-hidden="true"
              className="absolute inset-0 bg-transparent"
              onClick={closePicker}
            />

            <div
              ref={panelRef}
              id={panelId}
              role="dialog"
              aria-modal="true"
              className={cn(
                "fixed max-h-[calc(100vh-2rem)] overflow-auto rounded-[1.75rem] border border-white/70 bg-white/95 p-4 shadow-[0_24px_80px_rgba(15,23,42,0.22)] backdrop-blur dark:border-white/10 dark:bg-gray-950/95",
                position?.mode === "sheet"
                  ? "bottom-4 left-4 right-4"
                  : "w-[22rem] max-w-[calc(100vw-2rem)]",
              )}
              style={
                position?.mode === "popover"
                  ? { top: position.top, left: position.left }
                  : position?.mode === "sheet"
                    ? undefined
                    : { visibility: "hidden" }
              }
            >
              <div className="flex items-start justify-between gap-4">
                <div>
                  <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-gray-500 dark:text-gray-400">
                    Deadline
                  </div>
                  <div className="mt-1 text-sm font-semibold text-gray-950 dark:text-white">
                    {format(resolvedDraft, "EEEE, d MMMM 'at' h:mm aa")}
                  </div>
                </div>

                <button
                  type="button"
                  onClick={closePicker}
                  className="inline-flex h-9 w-9 items-center justify-center rounded-full border border-gray-200 bg-white/80 text-gray-500 transition-colors hover:border-orange-200 hover:text-orange-700 dark:border-gray-700 dark:bg-gray-900/80 dark:text-gray-300 dark:hover:border-orange-500/30 dark:hover:text-orange-200"
                  aria-label="Close deadline picker"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>

              <div className="mt-4 flex flex-wrap gap-2">
                {quickDates.map((option) => {
                  const isActive = isSameDay(option.date, resolvedDraft);

                  return (
                    <button
                      key={option.label}
                      type="button"
                      onClick={() => setDraftValue(option.date)}
                      className={cn(
                        "rounded-full border px-3 py-1.5 text-xs font-semibold transition-colors",
                        isActive
                          ? "border-orange-500 bg-orange-500 text-white"
                          : "border-gray-300 bg-white text-gray-700 hover:border-orange-300 hover:text-orange-700 dark:border-gray-600 dark:bg-gray-900 dark:text-gray-200 dark:hover:border-orange-400 dark:hover:text-orange-200",
                      )}
                    >
                      {option.label}
                    </button>
                  );
                })}
              </div>

              <div className="mt-4 rounded-[1.25rem] border border-gray-200 bg-white/80 p-3 dark:border-gray-700 dark:bg-gray-900/70">
                <DatePicker
                  inline
                  selected={resolvedDraft}
                  onChange={(date) => {
                    if (date) {
                      setDraftValue(date);
                    }
                  }}
                  shouldCloseOnSelect={false}
                  showTimeInput
                  customTimeInput={<TaskDeadlineTimeInput timeZoneLabel={resolvedTimeZone} />}
                  calendarStartDay={1}
                  renderCustomHeader={({
                    monthDate,
                    decreaseMonth,
                    increaseMonth,
                    prevMonthButtonDisabled,
                    nextMonthButtonDisabled,
                  }: ReactDatePickerCustomHeaderProps) => (
                    <div className="mb-3 flex items-center justify-between gap-3 px-1">
                      <button
                        type="button"
                        onClick={decreaseMonth}
                        disabled={prevMonthButtonDisabled}
                        className="inline-flex h-9 w-9 items-center justify-center rounded-full border border-gray-200 bg-white text-gray-600 transition-colors hover:border-orange-200 hover:text-orange-700 disabled:cursor-not-allowed disabled:opacity-40 dark:border-gray-700 dark:bg-gray-950 dark:text-gray-300 dark:hover:border-orange-500/30 dark:hover:text-orange-200"
                        aria-label="Previous month"
                      >
                        <ChevronLeft className="h-4 w-4" />
                      </button>

                      <div className="text-sm font-semibold text-gray-950 dark:text-white">
                        {format(monthDate, "MMMM yyyy")}
                      </div>

                      <button
                        type="button"
                        onClick={increaseMonth}
                        disabled={nextMonthButtonDisabled}
                        className="inline-flex h-9 w-9 items-center justify-center rounded-full border border-gray-200 bg-white text-gray-600 transition-colors hover:border-orange-200 hover:text-orange-700 disabled:cursor-not-allowed disabled:opacity-40 dark:border-gray-700 dark:bg-gray-950 dark:text-gray-300 dark:hover:border-orange-500/30 dark:hover:text-orange-200"
                        aria-label="Next month"
                      >
                        <ChevronRight className="h-4 w-4" />
                      </button>
                    </div>
                  )}
                  calendarClassName="task-deadline-calendar"
                  weekDayClassName={() => "task-deadline-weekday"}
                  dayClassName={(date) =>
                    cn(
                      "task-deadline-day !mx-0 !my-0 !flex !h-9 !w-9 items-center justify-center !rounded-full text-sm transition-colors",
                      isSameDay(date, resolvedDraft)
                        ? "!bg-orange-600 !text-white hover:!bg-orange-700"
                        : "text-gray-700 hover:!bg-orange-100 dark:text-gray-100 dark:hover:!bg-orange-500/15",
                      !isSameMonth(date, resolvedDraft) &&
                        "!text-gray-400 dark:!text-gray-600",
                    )
                  }
                />
              </div>

              <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
                {hasSavedDeadline ? (
                  <button
                    type="button"
                    onClick={() => void handleSubmit(null)}
                    disabled={isSaving || disabled}
                    className="text-sm font-medium text-gray-600 transition-colors hover:text-rose-600 disabled:cursor-not-allowed disabled:opacity-50 dark:text-gray-300 dark:hover:text-rose-300"
                  >
                    Remove deadline
                  </button>
                ) : (
                  <div />
                )}

                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={closePicker}
                    disabled={isSaving}
                    className="rounded-full border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-gray-700 transition-colors hover:border-orange-200 hover:text-orange-700 disabled:cursor-not-allowed disabled:opacity-50 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-200 dark:hover:border-orange-500/30 dark:hover:text-orange-200"
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    onClick={() => void handleSubmit(resolvedDraft)}
                    disabled={saveDisabled}
                    className="rounded-full bg-orange-600 px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-orange-700 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {hasSavedDeadline ? "Update deadline" : "Set deadline"}
                  </button>
                </div>
              </div>
            </div>
          </div>,
          document.body,
        )
      : null;

  return (
    <div className={cn("w-full", className)}>
      <button
        ref={buttonRef}
        type="button"
        onClick={() => {
          if (isOpen) {
            closePicker();
            return;
          }

          handleOpen();
        }}
        disabled={disabled}
        aria-haspopup="dialog"
        aria-expanded={isOpen}
        aria-controls={isOpen ? panelId : undefined}
        className={cn(
          "flex h-10 w-full items-center justify-between gap-2 rounded-full border px-3 py-2 text-sm ring-offset-white transition-colors focus:outline-none focus:ring-2 focus:ring-orange-500 focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50 dark:ring-offset-gray-950",
          inputClassName,
          hasSavedDeadline
            ? "border-solid border-orange-200 bg-orange-50/90 text-orange-900 dark:border-orange-500/20 dark:bg-orange-500/10 dark:text-orange-100"
            : "text-gray-500 dark:text-gray-400",
        )}
      >
        <span className="truncate">{triggerLabel}</span>
        <Calendar className="h-4 w-4 shrink-0 opacity-60" />
      </button>

      {overlay}
    </div>
  );
}