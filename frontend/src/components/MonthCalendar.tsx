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
    <div className="rounded-lg border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 p-2">
      <div className="flex items-center justify-between mb-2">
        <button
          type="button"
          onClick={() => onMonthChange(startOfMonth(addMonths(month, -1)))}
          className="p-1 rounded hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-600 dark:text-gray-300"
          aria-label="Previous month"
        >
          <ChevronLeft className="h-4 w-4" />
        </button>
        <span className="text-xs font-medium text-gray-700 dark:text-gray-200">
          {format(month, "MMMM yyyy")}
        </span>
        <button
          type="button"
          onClick={() => onMonthChange(startOfMonth(addMonths(month, 1)))}
          className="p-1 rounded hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-600 dark:text-gray-300"
          aria-label="Next month"
        >
          <ChevronRight className="h-4 w-4" />
        </button>
      </div>

      <div className="grid grid-cols-7 gap-0.5">
        {WEEKDAY_LABELS.map((label) => (
          <div
            key={label}
            className="text-center text-[10px] font-medium text-gray-400 dark:text-gray-500 py-0.5"
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
                  ? "bg-orange-500 text-white font-semibold"
                  : inMonth
                    ? "text-gray-700 dark:text-gray-200 hover:bg-orange-100 dark:hover:bg-orange-900/30"
                    : "text-gray-300 dark:text-gray-600 hover:bg-gray-100 dark:hover:bg-gray-700"
              }`}
            >
              {format(day, "d")}
              {isMarked && (
                <span
                  className={`absolute bottom-0.5 left-1/2 -translate-x-1/2 h-1 w-1 rounded-full ${
                    isSelected ? "bg-white" : "bg-orange-500"
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
