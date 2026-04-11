"use client";

import { useEffect, useMemo, useState } from "react";
import {
  eachDayOfInterval,
  endOfMonth,
  endOfWeek,
  format,
  isSameMonth,
  isToday,
  startOfMonth,
  startOfWeek,
} from "date-fns";
import { CalendarRange, Clock3 } from "lucide-react";

const WEEK_DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export default function DashboardUpcomingMeetingsCard() {
  const [now, setNow] = useState<Date>(() => new Date());

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
        start: startOfWeek(startOfMonth(now), { weekStartsOn: 1 }),
        end: endOfWeek(endOfMonth(now), { weekStartsOn: 1 }),
      }),
    [now],
  );

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
        <div className="flex items-center justify-between gap-3">
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

          <div className="inline-flex items-center gap-2 rounded-full border border-gray-200 bg-white/80 px-3 py-1.5 text-xs font-medium text-gray-600 dark:border-white/10 dark:bg-white/5 dark:text-gray-300">
            <Clock3 className="h-4 w-4" />
            <span suppressHydrationWarning>{format(now, "EEE d MMM")}</span>
          </div>
        </div>

        <div className="mt-4 grid grid-cols-7 gap-2 text-center">
          {WEEK_DAYS.map((day) => (
            <div
              key={day}
              className="text-xs font-medium text-gray-400 dark:text-gray-500"
            >
              {day}
            </div>
          ))}

          {monthDays.map((day) => {
            const inCurrentMonth = isSameMonth(day, now);
            const isCurrentDay = isToday(day);

            return (
              <div
                key={day.toISOString()}
                className={`flex h-10 items-center justify-center rounded-2xl text-sm font-medium transition-colors ${
                  isCurrentDay
                    ? "bg-orange-600 text-white shadow-lg shadow-orange-600/25"
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

      <div className="mt-4 rounded-[1.5rem] border border-dashed border-orange-200 bg-orange-50/70 p-4 text-sm text-gray-600 dark:border-orange-500/20 dark:bg-orange-500/10 dark:text-gray-300">
        No calendar connected.
      </div>
    </div>
  );
}
