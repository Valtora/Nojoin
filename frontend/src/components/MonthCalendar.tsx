"use client";

import {
  addMonths,
  eachDayOfInterval,
  endOfMonth,
  endOfWeek,
  format,
  isSameMonth,
  startOfMonth,
  startOfWeek,
} from "date-fns";
import { ChevronLeft, ChevronRight } from "lucide-react";

interface MonthCalendarProps {
  month: Date;
  markedDays: Set<string>;
  selectedDay: string | null;
  onSelectDay: (day: string) => void;
  onMonthChange: (month: Date) => void;
}

const WEEKDAY_LABELS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"];

export default function MonthCalendar({
  month,
  markedDays,
  selectedDay,
  onSelectDay,
  onMonthChange,
}: MonthCalendarProps) {
  const days = eachDayOfInterval({
    start: startOfWeek(startOfMonth(month), { weekStartsOn: 1 }),
    end: endOfWeek(endOfMonth(month), { weekStartsOn: 1 }),
  });

  return (
    <div className="rounded-lg border border-control-border bg-surface-card p-2">
      <div className="flex items-center justify-between mb-2">
        <button
          type="button"
          onClick={() => onMonthChange(startOfMonth(addMonths(month, -1)))}
          className="p-1 rounded hover:bg-surface-inset text-contrast-helper"
          aria-label="Previous month"
        >
          <ChevronLeft className="h-4 w-4" />
        </button>
        <span className="text-xs font-medium text-contrast-muted">
          {format(month, "MMMM yyyy")}
        </span>
        <button
          type="button"
          onClick={() => onMonthChange(startOfMonth(addMonths(month, 1)))}
          className="p-1 rounded hover:bg-surface-inset text-contrast-helper"
          aria-label="Next month"
        >
          <ChevronRight className="h-4 w-4" />
        </button>
      </div>

      <div className="grid grid-cols-7 gap-0.5">
        {WEEKDAY_LABELS.map((label) => (
          <div
            key={label}
            className="text-center text-[10px] font-medium text-contrast-icon-muted py-0.5"
          >
            {label}
          </div>
        ))}

        {days.map((day) => {
          const dayKey = format(day, "yyyy-MM-dd");
          const inMonth = isSameMonth(day, month);
          const isMarked = markedDays.has(dayKey);
          const isSelected = selectedDay === dayKey;

          return (
            <button
              key={dayKey}
              type="button"
              onClick={() => onSelectDay(dayKey)}
              className={`relative aspect-square flex items-center justify-center rounded text-xs transition-colors ${
                isSelected
                  ? "bg-action text-foreground font-semibold"
                  : inMonth
                    ? "text-contrast-muted hover:bg-action-tint"
                    : "text-contrast-icon-muted hover:bg-surface-inset"
              }`}
            >
              {format(day, "d")}
              {isMarked && (
                <span
                  className={`absolute bottom-0.5 left-1/2 -translate-x-1/2 h-1 w-1 rounded-full ${
                    isSelected ? "bg-surface-card" : "bg-action"
                  }`}
                />
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}
