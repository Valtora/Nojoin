"use client";

import { useEffect, useMemo, useState } from "react";
import {
  addMonths,
  eachDayOfInterval,
  endOfMonth,
  endOfWeek,
  format,
  isSameMonth,
  isToday,
  startOfMonth,
  startOfWeek,
} from "date-fns";
import {
  CalendarRange,
  ChevronLeft,
  ChevronRight,
  LayoutGrid,
  List,
} from "lucide-react";

const WEEK_DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
type CalendarViewMode = "month" | "agenda";

export default function DashboardUpcomingMeetingsCard() {
  const [now, setNow] = useState<Date>(() => new Date());
  const [viewedMonth, setViewedMonth] = useState<Date>(() =>
    startOfMonth(new Date()),
  );
  const [viewMode, setViewMode] = useState<CalendarViewMode>("month");

  useEffect(() => {
    const updateNow = () => {
      setNow(new Date());
    };

    updateNow();
    const interval = window.setInterval(updateNow, 30000);

    return () => {
      window.clearInterval(interval);
    };
  }, []);

  const monthDays = useMemo(
    () =>
      eachDayOfInterval({
        start: startOfWeek(startOfMonth(viewedMonth), { weekStartsOn: 1 }),
        end: endOfWeek(endOfMonth(viewedMonth), { weekStartsOn: 1 }),
      }),
    [viewedMonth],
  );
  const isViewingCurrentMonth = isSameMonth(viewedMonth, now);
  const viewedMonthLabel = format(viewedMonth, "MMMM yyyy");

  return (
    <div className="rounded-[2rem] border border-white/60 bg-white/82 p-6 shadow-xl shadow-orange-950/5 backdrop-blur dark:border-white/10 dark:bg-gray-950/62 dark:shadow-black/20">
      <div className="mt-2 flex items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          <div className="rounded-2xl bg-orange-100 p-2 text-orange-700 dark:bg-orange-500/10 dark:text-orange-300">
            <CalendarRange className="h-5 w-5" />
          </div>
          <div>
            <h2 className="text-2xl font-semibold text-gray-950 dark:text-white">
              Calendar
            </h2>
            <p
              className="mt-1 text-sm text-gray-600 dark:text-gray-300"
              suppressHydrationWarning
            >
              {format(now, "EEEE, d MMMM yyyy")}
            </p>
          </div>
        </div>

        <div className="pt-1 text-right">
          <div
            className="text-2xl font-semibold tracking-tight text-gray-950 dark:text-white"
            suppressHydrationWarning
          >
            {format(now, "HH:mm")}
          </div>
        </div>
      </div>

      <div className="mt-6 rounded-[1.75rem] border border-white/70 bg-white/75 p-4 shadow-inner shadow-orange-950/5 dark:border-white/10 dark:bg-gray-900/60">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="text-xs font-semibold uppercase tracking-[0.18em] text-gray-500 dark:text-gray-400">
              This month
            </div>
            <div
              className="mt-1 text-lg font-semibold text-gray-950 dark:text-white"
              suppressHydrationWarning
            >
              {format(now, "MMMM yyyy")}
            </div>
          </div>

          <div className="inline-flex items-center rounded-full border border-gray-200 bg-white/85 p-1 text-sm shadow-sm dark:border-white/10 dark:bg-white/5">
            <button
              type="button"
              onClick={() => setViewMode("month")}
              aria-pressed={viewMode === "month"}
              className={`inline-flex items-center gap-2 rounded-full px-3 py-2 font-medium transition-colors ${
                viewMode === "month"
                  ? "bg-orange-600 text-white shadow-sm shadow-orange-600/20"
                  : "text-gray-600 hover:text-gray-900 dark:text-gray-300 dark:hover:text-white"
              }`}
            >
              <LayoutGrid className="h-4 w-4" />
              <span className="hidden sm:inline">Month</span>
            </button>
            <button
              type="button"
              onClick={() => setViewMode("agenda")}
              aria-pressed={viewMode === "agenda"}
              className={`inline-flex items-center gap-2 rounded-full px-3 py-2 font-medium transition-colors ${
                viewMode === "agenda"
                  ? "bg-orange-600 text-white shadow-sm shadow-orange-600/20"
                  : "text-gray-600 hover:text-gray-900 dark:text-gray-300 dark:hover:text-white"
              }`}
            >
              <List className="h-4 w-4" />
              <span className="hidden sm:inline">Agenda</span>
            </button>
          </div>
        </div>

        <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
          <div className="min-h-6 text-sm font-medium text-gray-500 dark:text-gray-400">
            {isViewingCurrentMonth ? (
              "Browse past and future months."
            ) : (
              <span suppressHydrationWarning>Viewing {viewedMonthLabel}</span>
            )}
          </div>

          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setViewedMonth((currentMonth) => addMonths(currentMonth, -1))}
              aria-label={`View ${format(addMonths(viewedMonth, -1), "MMMM yyyy")}`}
              className="inline-flex h-10 w-10 items-center justify-center rounded-full border border-gray-200 bg-white/85 text-gray-600 shadow-sm transition-colors hover:border-orange-200 hover:bg-orange-50 hover:text-orange-700 dark:border-white/10 dark:bg-white/5 dark:text-gray-300 dark:hover:border-orange-500/20 dark:hover:bg-orange-500/10 dark:hover:text-orange-300"
            >
              <ChevronLeft className="h-4 w-4" />
            </button>
            <button
              type="button"
              onClick={() => setViewedMonth((currentMonth) => addMonths(currentMonth, 1))}
              aria-label={`View ${format(addMonths(viewedMonth, 1), "MMMM yyyy")}`}
              className="inline-flex h-10 w-10 items-center justify-center rounded-full border border-gray-200 bg-white/85 text-gray-600 shadow-sm transition-colors hover:border-orange-200 hover:bg-orange-50 hover:text-orange-700 dark:border-white/10 dark:bg-white/5 dark:text-gray-300 dark:hover:border-orange-500/20 dark:hover:bg-orange-500/10 dark:hover:text-orange-300"
            >
              <ChevronRight className="h-4 w-4" />
            </button>
          </div>
        </div>

        {viewMode === "month" ? (
          <div className="mt-5 rounded-[1.5rem] border border-white/70 bg-white/70 p-4 dark:border-white/10 dark:bg-gray-900/60">
            <div className="grid grid-cols-7 gap-2 text-center">
              {WEEK_DAYS.map((day) => (
                <div
                  key={day}
                  className="text-xs font-medium text-gray-400 dark:text-gray-500"
                >
                  {day}
                </div>
              ))}

              {monthDays.map((day) => {
                const inCurrentMonth = isSameMonth(day, viewedMonth);
                const isCurrentDay = isToday(day);

                return (
                  <div
                    key={day.toISOString()}
                    className={`flex h-10 items-center justify-center rounded-2xl text-sm font-medium transition-colors ${
                      isCurrentDay
                        ? inCurrentMonth
                          ? "bg-orange-600 text-white shadow-lg shadow-orange-600/25"
                          : "border border-orange-200 bg-orange-50 text-orange-700 dark:border-orange-500/20 dark:bg-orange-500/10 dark:text-orange-200"
                        : inCurrentMonth
                          ? "bg-gray-950/[0.04] text-gray-700 dark:bg-white/5 dark:text-gray-200"
                          : "text-gray-300 dark:text-gray-600"
                    }`}
                  >
                    {format(day, "d")}
                  </div>
                );
              })}
            </div>
          </div>
        ) : (
          <div className="mt-5 rounded-[1.5rem] border border-dashed border-orange-200 bg-orange-50/70 p-5 dark:border-orange-500/20 dark:bg-orange-500/10">
            <div className="text-sm font-semibold text-gray-950 dark:text-white">
              Agenda
            </div>
            <p className="mt-2 text-sm text-gray-600 dark:text-gray-300">
              <span suppressHydrationWarning>
                No calendar connected for {viewedMonthLabel}.
              </span>{" "}
              Events will appear here in chronological order once calendar data is available.
            </p>
          </div>
        )}
      </div>

      {viewMode === "month" && (
        <div className="mt-4 rounded-[1.5rem] border border-dashed border-orange-200 bg-orange-50/70 p-4 text-sm text-gray-600 dark:border-orange-500/20 dark:bg-orange-500/10 dark:text-gray-300">
          No calendar connected.
        </div>
      )}
    </div>
  );
}
