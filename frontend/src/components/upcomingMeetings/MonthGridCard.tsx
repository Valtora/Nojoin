"use client";

import { addMonths, format, isSameDay, isSameMonth } from "date-fns";
import { CalendarRange, ChevronLeft, ChevronRight, Loader2 } from "lucide-react";

import Button from "@/components/ui/Button";
import IconButton from "@/components/ui/IconButton";

import {
  MAX_VISIBLE_DOTS,
  WEEK_DAYS,
  getCalendarColourPresentation,
} from "./calendarUtils";
import type { CalendarDashboard } from "./useCalendarDashboard";

/**
 * The month at a glance: one dot per source with something on that day, and a
 * day click that points the agenda module at that day.
 *
 * This used to be one half of a card with a Month/Agenda toggle, where picking
 * one view hid the other. It is now a module in its own right, and the toggle
 * is gone: choosing a day here is what the agenda beside it responds to.
 */
export default function MonthGridCard({
  calendar,
}: {
  calendar: CalendarDashboard;
}) {
  const {
    viewedMonth,
    selectedDay,
    calendarLoading,
    calendarRefreshing,
    currentDay,
    monthDays,
    viewedMonthLabel,
    dayMarkerColours,
    isViewingToday,
    handleJumpToToday,
    handleSelectDay,
    handlePreviousMonth,
    handleNextMonth,
  } = calendar;

  return (
    <div className="density-surface flex h-full min-h-0 flex-col border border-surface-border bg-surface-card shadow-card">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <CalendarRange className="h-5 w-5 shrink-0 text-action-text" />
        <h2 className="text-base font-semibold text-foreground">Calendar</h2>
        <span
          className="text-sm font-medium text-contrast-helper"
          suppressHydrationWarning
        >
          {viewedMonthLabel}
        </span>
        {(calendarLoading || calendarRefreshing) && (
          <Loader2
            aria-label="Loading calendar"
            className="h-4 w-4 animate-spin text-contrast-icon-muted"
          />
        )}

        <div className="ml-auto flex items-center gap-1">
          <Button
            size="sm"
            variant="secondary"
            onClick={handleJumpToToday}
            disabled={isViewingToday}
          >
            Today
          </Button>
          <IconButton
            size="sm"
            aria-label={`View ${format(addMonths(viewedMonth, -1), "MMMM yyyy")}`}
            icon={<ChevronLeft />}
            onClick={handlePreviousMonth}
          />
          <IconButton
            size="sm"
            aria-label={`View ${format(addMonths(viewedMonth, 1), "MMMM yyyy")}`}
            icon={<ChevronRight />}
            onClick={handleNextMonth}
          />
        </div>
      </div>

      <div className="mt-4 grid grid-cols-7 gap-2 text-center">
        {WEEK_DAYS.map((day) => (
          <div key={day} className="text-xs font-medium text-contrast-icon-muted">
            {day}
          </div>
        ))}
      </div>

      {/* The grid takes whatever height the column has left, so the module fills
          its column instead of ending short and leaving a dead corner beneath
          it. The rows are capped so that on a very tall viewport the cells stop
          growing rather than becoming a wall of empty boxes. */}
      <div className="mt-2 min-h-0 flex-1">
        <div className="grid h-full max-h-[34rem] grid-cols-7 gap-2 text-center [grid-auto-rows:minmax(3.25rem,1fr)]">
          {monthDays.map((day) => {
            const inCurrentMonth = isSameMonth(day, viewedMonth);
            const isCurrentDay = isSameDay(day, currentDay);
            const isSelectedDay = Boolean(selectedDay && isSameDay(day, selectedDay));
            const dayColours = dayMarkerColours.get(format(day, "yyyy-MM-dd")) || [];
            const visibleDotColours = dayColours.slice(0, MAX_VISIBLE_DOTS);
            const extraDots = dayColours.length - visibleDotColours.length;
            const dayClasses = `flex flex-col items-center justify-center rounded-lg px-1 py-2 text-sm font-medium transition-colors ${
              isCurrentDay
                ? inCurrentMonth
                  ? "bg-action text-action-on"
                  : "border border-action-border bg-action-tint text-action-text"
                : isSelectedDay && inCurrentMonth
                  ? "border border-action-border bg-action-tint text-action-text"
                  : inCurrentMonth
                    ? "bg-surface-inset text-contrast-muted hover:bg-action-tint hover:text-action-text"
                    : "text-contrast-icon-muted"
            }`;

            const dayContent = (
              <>
                <span>{format(day, "d")}</span>
                {dayColours.length > 0 && (
                  <div className="mt-1 flex items-center gap-1">
                    {visibleDotColours.map((colour, index) => {
                      const dot = getCalendarColourPresentation(colour);
                      return (
                        <span
                          key={`${day.toISOString()}-dot-${index}`}
                          className={`h-1.5 w-1.5 rounded-full border border-surface-border/40 ${dot.className}`}
                          style={dot.style}
                        />
                      );
                    })}
                    {extraDots > 0 && (
                      <span
                        className={`text-[10px] font-semibold ${
                          isCurrentDay ? "text-foreground" : "text-action-text"
                        }`}
                      >
                        +{extraDots}
                      </span>
                    )}
                  </div>
                )}
              </>
            );

            return inCurrentMonth ? (
              <button
                key={day.toISOString()}
                type="button"
                aria-pressed={isSelectedDay}
                onClick={() => handleSelectDay(day)}
                className={`${dayClasses} focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring`}
              >
                {dayContent}
              </button>
            ) : (
              // Adjacent-month days are padding: they carry no markers and are
              // not selectable, so the grid reads as one month rather than three.
              <div key={day.toISOString()} className={dayClasses}>
                <span>{format(day, "d")}</span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
