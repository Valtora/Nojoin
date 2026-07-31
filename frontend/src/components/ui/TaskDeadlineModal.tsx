"use client";

import { useEffect, useId, useState } from "react";
import DatePicker, { type ReactDatePickerCustomHeaderProps } from "react-datepicker";
import { addDays, format, isSameDay, isSameMonth } from "date-fns";
import { ChevronLeft, ChevronRight, Clock3 } from "lucide-react";
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

import Button from "./Button";
import IconButton from "./IconButton";
import Modal from "./Modal";

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
    <div className="space-y-3 rounded-surface-panel border border-surface-border bg-surface-inset px-3 py-3">
      <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.18em] text-contrast-helper">
        <Clock3 className="h-3.5 w-3.5" />
        Time
      </div>

      <div className="flex items-center gap-2">
        <input
          type="time"
          step={900}
          value={value}
          onChange={(event) => onChange?.(event.target.value)}
          className="h-10 min-w-0 flex-1 rounded-lg border border-control-border bg-control-bg px-3 text-sm font-medium text-foreground transition-colors focus-visible:outline-2 focus-visible:outline-offset-0 focus-visible:outline-focus-ring"
        />
        <span className="text-xs text-contrast-helper">{timeZoneLabel}</span>
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
                  ? "border-action bg-action text-action-on"
                  : "border-control-border bg-surface-card text-contrast-muted hover:border-action-border hover:text-action-text",
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

    const nextZonedValue = value ? toTimeZoneDate(value, resolvedTimeZone) : null;
    const nextZonedNow = toTimeZoneDate(new Date(), resolvedTimeZone);

    setDraftValue(
      nextZonedValue
        ? new Date(nextZonedValue)
        : buildSuggestedDeadline(nextZonedNow),
    );
  }, [isOpen, resolvedTimeZone, value, valueTimestamp]);

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

  if (!mounted) {
    return null;
  }

  return (
    <Modal
      open={isOpen}
      onClose={handleRequestClose}
      dismissible={!isSaving}
      size="lg"
      className="max-h-[calc(100dvh-2rem)]"
      title={
        <span className="min-w-0">
          <span className="block text-[11px] font-semibold uppercase tracking-[0.18em] text-contrast-helper">
            Task deadline
          </span>
          <span id={titleId} className="mt-1 block text-lg font-semibold text-foreground">
            {hasSavedDeadline ? "Edit deadline" : "Set deadline"}
          </span>
          <span className="mt-1 block truncate text-sm font-normal text-contrast-helper">
            {taskTitle}
          </span>
          <span className="mt-3 block text-sm font-semibold text-foreground">
            {format(resolvedDraft, "EEEE, d MMMM 'at' h:mm aa")}
          </span>
        </span>
      }
      footer={
        <div className="flex w-full flex-wrap items-center justify-between gap-3">
          {hasSavedDeadline ? (
            <Button
              variant="ghost"
              onClick={() => void handleSubmit(null)}
              disabled={isSaving}
              className="hover:text-danger-text"
            >
              Remove deadline
            </Button>
          ) : (
            <div />
          )}

          <div className="flex items-center gap-2">
            <Button variant="secondary" onClick={handleRequestClose} disabled={isSaving}>
              Cancel
            </Button>
            <Button
              variant="primary"
              onClick={() => void handleSubmit(resolvedDraft)}
              disabled={saveDisabled}
              loading={isSaving}
            >
              {hasSavedDeadline ? "Update deadline" : "Set deadline"}
            </Button>
          </div>
        </div>
      }
    >
      <div className="flex flex-wrap gap-2">
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
                  ? "border-action bg-action text-action-on"
                  : "border-control-border bg-surface-card text-contrast-muted hover:border-action-border hover:text-action-text",
              )}
            >
              {option.label}
            </button>
          );
        })}
      </div>

      <div className="mt-4 rounded-surface-panel border border-surface-border bg-surface-inset p-3">
        <DatePicker
          inline
          selected={resolvedDraft}
          onChange={(date: Date | null) => {
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
              <IconButton
                size="sm"
                variant="secondary"
                onClick={decreaseMonth}
                disabled={prevMonthButtonDisabled || isSaving}
                aria-label="Previous month"
                icon={<ChevronLeft aria-hidden="true" />}
                className="rounded-full"
              />

              <div className="text-sm font-semibold text-foreground">
                {format(monthDate, "MMMM yyyy")}
              </div>

              <IconButton
                size="sm"
                variant="secondary"
                onClick={increaseMonth}
                disabled={nextMonthButtonDisabled || isSaving}
                aria-label="Next month"
                icon={<ChevronRight aria-hidden="true" />}
                className="rounded-full"
              />
            </div>
          )}
          calendarClassName="task-deadline-calendar"
          weekDayClassName={() => "task-deadline-weekday"}
          dayClassName={(date) =>
            cn(
              "task-deadline-day !mx-0 !my-0 !flex !h-9 !w-9 items-center justify-center !rounded-full text-sm transition-colors",
              isSameDay(date, resolvedDraft)
                ? "!bg-action !text-action-on hover:!bg-action-hover"
                : "text-contrast-muted hover:!bg-action-tint",
              !isSameMonth(date, resolvedDraft) && "!text-contrast-icon-muted",
            )
          }
        />
      </div>
    </Modal>
  );
}
