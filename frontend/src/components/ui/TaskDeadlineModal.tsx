"use client";

import { useEffect, useId, useState } from "react";
import { createPortal } from "react-dom";
import DatePicker, { type ReactDatePickerCustomHeaderProps } from "react-datepicker";
import { addDays, format, isSameDay, isSameMonth } from "date-fns";
import { ChevronLeft, ChevronRight, Clock3, X } from "lucide-react";
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

import {
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

interface TaskDeadlineTimeInputProps {
  value?: string;
  onChange?: (time: string) => void;
  timeZoneLabel: string;
}

interface TaskDeadlineModalProps {
  isOpen: boolean;
  taskTitle: string;
  value: Date | null;
  timeZone?: string;
  isSaving?: boolean;
  error?: string | null;
  onClose: () => void;
  onSave: (date: Date | null) => Promise<boolean | void> | boolean | void;
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

export default function TaskDeadlineModal({
  isOpen,
  taskTitle,
  value,
  timeZone,
  isSaving = false,
  error,
  onClose,
  onSave,
}: TaskDeadlineModalProps) {
  const [mounted, setMounted] = useState(false);
  const [draftValue, setDraftValue] = useState<Date | null>(null);
  const titleId = useId();
  const valueTimestamp = value?.getTime() ?? null;
  const resolvedTimeZone = resolveTimeZone(timeZone);
  const zonedNow = toTimeZoneDate(new Date(), resolvedTimeZone);
  const zonedValue = value ? toTimeZoneDate(value, resolvedTimeZone) : null;

  useEffect(() => {
    setMounted(true);
    return () => setMounted(false);
  }, []);

  useEffect(() => {
    if (!isOpen) {
      setDraftValue(null);
      return;
    }

    setDraftValue(
      zonedValue ? new Date(zonedValue) : buildSuggestedDeadline(zonedNow),
    );
  }, [isOpen, valueTimestamp, resolvedTimeZone]);

  useEffect(() => {
    if (!isOpen || isSaving) {
      return;
    }

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
      }
    };

    document.addEventListener("keydown", handleKeyDown);

    return () => {
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [isOpen, isSaving, onClose]);

  const resolvedDraft =
    draftValue ??
    (zonedValue ? new Date(zonedValue) : buildSuggestedDeadline(zonedNow));
  const draftInstant = draftValue
    ? fromTimeZoneDate(draftValue, resolvedTimeZone)
    : null;
  const hasSavedDeadline = Boolean(value);
  const quickDates = [
    { label: "Today", date: preserveTime(zonedNow, resolvedDraft) },
    { label: "Tomorrow", date: preserveTime(addDays(zonedNow, 1), resolvedDraft) },
    { label: "Next week", date: preserveTime(addDays(zonedNow, 7), resolvedDraft) },
  ];
  const saveDisabled =
    isSaving || (valueTimestamp !== null && draftInstant?.getTime() === valueTimestamp);

  const handleRequestClose = () => {
    if (!isSaving) {
      onClose();
    }
  };

  const handleSubmit = async (nextValue: Date | null) => {
    if (isSaving) {
      return;
    }

    const nextInstant = nextValue
      ? fromTimeZoneDate(nextValue, resolvedTimeZone)
      : null;
    const result = await onSave(nextInstant);

    if (result !== false) {
      onClose();
    }
  };

  if (!mounted || !isOpen) {
    return null;
  }

  return createPortal(
    <div className="fixed inset-0 z-[10000] flex items-center justify-center bg-black/50 p-4 backdrop-blur-sm">
      <button
        type="button"
        aria-label="Close deadline modal"
        onClick={handleRequestClose}
        disabled={isSaving}
        className="absolute inset-0 cursor-default"
      />

      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="relative w-full max-w-xl max-h-[calc(100vh-2rem)] overflow-auto rounded-[1.75rem] border border-white/70 bg-white/95 p-5 shadow-[0_24px_80px_rgba(15,23,42,0.22)] backdrop-blur dark:border-white/10 dark:bg-gray-950/95"
      >
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-gray-500 dark:text-gray-400">
              Task deadline
            </div>
            <h3
              id={titleId}
              className="mt-1 text-lg font-semibold text-gray-950 dark:text-white"
            >
              {hasSavedDeadline ? "Edit deadline" : "Set deadline"}
            </h3>
            <div className="mt-1 truncate text-sm text-gray-600 dark:text-gray-300">
              {taskTitle}
            </div>
            <div className="mt-3 text-sm font-semibold text-gray-950 dark:text-white">
              {format(resolvedDraft, "EEEE, d MMMM 'at' h:mm aa")}
            </div>
          </div>

          <button
            type="button"
            onClick={handleRequestClose}
            disabled={isSaving}
            className="inline-flex h-9 w-9 items-center justify-center rounded-full border border-gray-200 bg-white/80 text-gray-500 transition-colors hover:border-orange-200 hover:text-orange-700 disabled:cursor-not-allowed disabled:opacity-50 dark:border-gray-700 dark:bg-gray-900/80 dark:text-gray-300 dark:hover:border-orange-500/30 dark:hover:text-orange-200"
            aria-label="Close deadline modal"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {error && (
          <div className="mt-4 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-300">
            {error}
          </div>
        )}

        <div className="mt-4 flex flex-wrap gap-2">
          {quickDates.map((option) => {
            const isActive = isSameDay(option.date, resolvedDraft);

            return (
              <button
                key={option.label}
                type="button"
                onClick={() => setDraftValue(option.date)}
                disabled={isSaving}
                className={cn(
                  "rounded-full border px-3 py-1.5 text-xs font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-50",
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
                  disabled={prevMonthButtonDisabled || isSaving}
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
                  disabled={nextMonthButtonDisabled || isSaving}
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

        <div className="mt-5 flex flex-wrap items-center justify-between gap-3">
          {hasSavedDeadline ? (
            <button
              type="button"
              onClick={() => void handleSubmit(null)}
              disabled={isSaving}
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
              onClick={handleRequestClose}
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
  );
}