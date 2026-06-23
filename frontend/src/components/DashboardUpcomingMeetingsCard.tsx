"use client";

import { addMonths, format, isSameDay, isSameMonth, startOfDay } from "date-fns";
import {
  CalendarRange,
  ChevronLeft,
  ChevronRight,
  LayoutGrid,
  Loader2,
  List,
} from "lucide-react";

import { formatTimeZoneDate } from "@/lib/timezone";

import {
  AgendaEventCard,
  DashboardRecordingCard,
  DayTimelineAllDayChip,
  DayTimelineEventCard,
} from "./upcomingMeetings/CalendarCards";
import {
  MAX_VISIBLE_DOTS,
  TIMELINE_HOUR_HEIGHT,
  WEEK_DAYS,
  formatHourLabel,
  getCalendarColourPresentation,
} from "./upcomingMeetings/calendarUtils";
import { useCalendarDashboard } from "./upcomingMeetings/useCalendarDashboard";

export default function DashboardUpcomingMeetingsCard() {
  const {
    now,
    activeTimeZone,
    viewedMonth,
    viewMode,
    setViewMode,
    selectedDay,
    setSelectedDay,
    calendarLoading,
    calendarRefreshing,
    currentDay,
    monthDays,
    isViewingCurrentMonth,
    viewedMonthLabel,
    dayMarkerColours,
    nextEventHelper,
    footerText,
    monthAgendaItems,
    monthHasContent,
    selectedDayEvents,
    selectedDayRecordings,
    selectedDayHasContent,
    selectedDayLabel,
    selectedDayState,
    selectedDayTimeline,
    mobileNowDividerIndex,
    isViewingToday,
    handleJumpToToday,
    handlePreviousMonth,
    handleNextMonth,
  } = useCalendarDashboard();

  return (
    <div className="density-surface border border-orange-100 bg-white shadow-xl shadow-orange-900/10 backdrop-blur dark:border-gray-700/70 dark:bg-gray-900/85 dark:shadow-black/30">
      <div className="mt-2 flex items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          <div className="rounded-2xl bg-orange-100 p-2 text-orange-700 dark:bg-orange-500/10 dark:text-orange-300">
            <CalendarRange className="h-5 w-5" />
          </div>
          <div>
            <h2 className="density-heading-section text-2xl font-semibold text-gray-950 dark:text-white">
              Calendar
            </h2>
            <p
              className="mt-1 text-sm text-gray-600 dark:text-gray-300"
              suppressHydrationWarning
            >
              {formatTimeZoneDate(now, activeTimeZone, "EEEE, d MMMM yyyy")}
            </p>
            {nextEventHelper && (
              <p className="mt-1 text-xs font-medium text-orange-700 dark:text-orange-300">
                {nextEventHelper}
              </p>
            )}
          </div>
        </div>

        <div className="pt-1 text-right">
          <div
            className="text-2xl font-semibold tracking-tight text-gray-950 dark:text-white"
            suppressHydrationWarning
          >
            {formatTimeZoneDate(now, activeTimeZone, "HH:mm")}
          </div>
          <div className="mt-1 text-xs text-gray-500 dark:text-gray-400">
            {activeTimeZone}
          </div>
        </div>
      </div>

      <div className="density-surface-subtle mt-6 border border-gray-200 bg-white p-4 shadow-inner shadow-orange-950/5 dark:border-gray-700/70 dark:bg-gray-800/70">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="text-xs font-semibold uppercase tracking-[0.18em] text-gray-500 dark:text-gray-400">
              {isViewingCurrentMonth ? "This month" : "Viewing"}
            </div>
            <div
              className="mt-1 text-lg font-semibold text-gray-950 dark:text-white"
              suppressHydrationWarning
            >
              {viewedMonthLabel}
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
            {calendarLoading ? (
              <span className="inline-flex items-center gap-2">
                <Loader2 className="h-4 w-4 animate-spin" />
                Loading {viewedMonthLabel}
              </span>
            ) : (
              <span className="inline-flex items-center gap-2">
                {isViewingCurrentMonth ? (
                  <span>Browse past and future months.</span>
                ) : (
                  <span suppressHydrationWarning>Viewing {viewedMonthLabel}</span>
                )}
                {calendarRefreshing && <Loader2 className="h-4 w-4 animate-spin" />}
              </span>
            )}
          </div>

          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={handleJumpToToday}
              disabled={isViewingToday}
              className="inline-flex h-10 items-center justify-center rounded-full border border-gray-200 bg-white/85 px-4 text-sm font-medium text-gray-700 shadow-sm transition-colors hover:border-orange-200 hover:bg-orange-50 hover:text-orange-700 disabled:cursor-default disabled:opacity-60 dark:border-white/10 dark:bg-white/5 dark:text-gray-200 dark:hover:border-orange-500/20 dark:hover:bg-orange-500/10 dark:hover:text-orange-300"
            >
              Today
            </button>
            <button
              type="button"
              onClick={handlePreviousMonth}
              aria-label={`View ${format(addMonths(viewedMonth, -1), "MMMM yyyy")}`}
              className="inline-flex h-10 w-10 items-center justify-center rounded-full border border-gray-200 bg-white/85 text-gray-600 shadow-sm transition-colors hover:border-orange-200 hover:bg-orange-50 hover:text-orange-700 dark:border-white/10 dark:bg-white/5 dark:text-gray-300 dark:hover:border-orange-500/20 dark:hover:bg-orange-500/10 dark:hover:text-orange-300"
            >
              <ChevronLeft className="h-4 w-4" />
            </button>
            <button
              type="button"
              onClick={handleNextMonth}
              aria-label={`View ${format(addMonths(viewedMonth, 1), "MMMM yyyy")}`}
              className="inline-flex h-10 w-10 items-center justify-center rounded-full border border-gray-200 bg-white/85 text-gray-600 shadow-sm transition-colors hover:border-orange-200 hover:bg-orange-50 hover:text-orange-700 dark:border-white/10 dark:bg-white/5 dark:text-gray-300 dark:hover:border-orange-500/20 dark:hover:bg-orange-500/10 dark:hover:text-orange-300"
            >
              <ChevronRight className="h-4 w-4" />
            </button>
          </div>
        </div>

        {viewMode === "month" ? (
          <div className="density-surface-panel mt-5 border border-gray-200 bg-white p-4 dark:border-gray-700/70 dark:bg-gray-800/70">
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
                const isCurrentDay = isSameDay(day, currentDay);
                const isSelectedDay = Boolean(selectedDay && isSameDay(day, selectedDay));
                const dayColours = dayMarkerColours.get(format(day, "yyyy-MM-dd")) || [];
                const visibleDotColours = dayColours.slice(0, MAX_VISIBLE_DOTS);
                const extraDots = dayColours.length - visibleDotColours.length;
                const dayClasses = `flex min-h-[3.5rem] flex-col items-center justify-center rounded-2xl px-1 py-2 text-sm font-medium transition-colors ${
                  isCurrentDay
                    ? inCurrentMonth
                      ? "bg-orange-600 text-white shadow-lg shadow-orange-600/25"
                      : "border border-orange-200 bg-orange-50 text-orange-700 dark:border-orange-500/20 dark:bg-orange-500/10 dark:text-orange-200"
                    : isSelectedDay && inCurrentMonth
                      ? "border border-orange-300 bg-orange-50 text-orange-700 shadow-sm shadow-orange-600/10 dark:border-orange-500/30 dark:bg-orange-500/10 dark:text-orange-200"
                      : inCurrentMonth
                        ? "bg-gray-950/[0.04] text-gray-700 hover:border-orange-200 hover:bg-orange-50 hover:text-orange-700 dark:bg-white/5 dark:text-gray-200 dark:hover:border-orange-500/20 dark:hover:bg-orange-500/10 dark:hover:text-orange-200"
                        : "text-gray-300 dark:text-gray-600"
                }`;

                return (
                  inCurrentMonth ? (
                    <button
                      key={day.toISOString()}
                      type="button"
                      onClick={() => setSelectedDay(startOfDay(day))}
                      className={dayClasses}
                    >
                      <span>{format(day, "d")}</span>
                      {dayColours.length > 0 && (
                        <div className="mt-1 flex items-center gap-1">
                          {visibleDotColours.map((colour, index) => {
                            const dot = getCalendarColourPresentation(colour);
                            return (
                              <span
                                key={`${day.toISOString()}-dot-${index}`}
                                className={`h-1.5 w-1.5 rounded-full border border-white/60 dark:border-gray-950/40 ${dot.className}`}
                                style={dot.style}
                              />
                            );
                          })}
                          {extraDots > 0 && (
                            <span className={`text-[10px] font-semibold ${
                              isCurrentDay
                                ? "text-white/90"
                                : "text-orange-600 dark:text-orange-300"
                            }`}>
                              +{extraDots}
                            </span>
                          )}
                        </div>
                      )}
                    </button>
                  ) : (
                    <div
                      key={day.toISOString()}
                      className={dayClasses}
                    >
                      <span>{format(day, "d")}</span>
                    </div>
                  )
                );
              })}
            </div>
          </div>
        ) : (
          <div className="density-surface-panel mt-5 border border-gray-200 bg-white p-5 shadow-inner shadow-orange-950/5 dark:border-gray-700/70 dark:bg-gray-800/70">
            <div className="text-sm font-semibold text-gray-950 dark:text-white">
              Agenda
            </div>
            {calendarLoading ? (
              <div className="mt-3 inline-flex items-center gap-2 text-sm text-gray-600 dark:text-gray-300">
                <Loader2 className="h-4 w-4 animate-spin" />
                Loading agenda...
              </div>
            ) : monthAgendaItems.length ? (
              <div className="mt-4 space-y-3">
                {monthAgendaItems.map((item) => (
                  item.kind === "event" ? (
                    <AgendaEventCard key={`event-${item.event.id}`} event={item.event} timeZone={activeTimeZone} />
                  ) : (
                    <DashboardRecordingCard
                      key={`recording-${item.recording.id}`}
                      recording={item.recording}
                      timeZone={activeTimeZone}
                      showDate={true}
                    />
                  )
                ))}
              </div>
            ) : (
              <p className="mt-2 text-sm text-gray-600 dark:text-gray-300">
                <span suppressHydrationWarning>{footerText}</span>
              </p>
            )}
          </div>
        )}
      </div>

      {viewMode === "month" && (
        <div className="density-surface-panel mt-4 border border-gray-200 bg-white p-4 text-sm text-gray-600 shadow-inner shadow-orange-950/5 dark:border-gray-700/70 dark:bg-gray-800/70 dark:text-gray-300">
          {calendarLoading ? (
            <span className="inline-flex items-center gap-2">
              <Loader2 className="h-4 w-4 animate-spin" />
              Loading day agenda...
            </span>
          ) : selectedDay && selectedDayLabel && monthHasContent ? (
            <div>
              <div>
                <div className="text-sm font-semibold text-gray-950 dark:text-white">
                  {selectedDayLabel}
                </div>
                <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                  {selectedDayState === "today"
                    ? "Live day view"
                    : "Day agenda"} in {activeTimeZone}
                </p>
              </div>
              {selectedDayHasContent ? (
                <div className="mt-4 space-y-4">
                  {selectedDayTimeline?.allDayEvents.length ? (
                    <div>
                      <div className="text-xs font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">
                        All-day events
                      </div>
                      <div className="mt-3 space-y-3">
                        {selectedDayTimeline.allDayEvents.map((event) => (
                          <DayTimelineAllDayChip key={event.id} event={event} />
                        ))}
                      </div>
                    </div>
                  ) : null}

                  {selectedDayTimeline?.timedEvents.length ? (
                    <div>
                      <div className="text-xs font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">
                        Timed agenda
                      </div>

                      <div className="mt-3 space-y-3 md:hidden">
                        {selectedDayTimeline.timedEvents.map((event, index) => (
                          <div key={event.event.id} className="space-y-3">
                            {mobileNowDividerIndex === index && (
                              <div className="h-px w-full bg-orange-400/80 dark:bg-orange-400/50" />
                            )}
                            <DayTimelineEventCard
                              event={event.event}
                              timeZone={activeTimeZone}
                              status={event.status}
                              layout="stacked"
                            />
                          </div>
                        ))}
                        {mobileNowDividerIndex === selectedDayTimeline.timedEvents.length && (
                          <div className="h-px w-full bg-orange-400/80 dark:bg-orange-400/50" />
                        )}
                      </div>

                      <div className="mt-3 hidden md:block">
                        <div className="grid grid-cols-[4rem_minmax(0,1fr)] gap-3">
                          <div className="relative" style={{ height: `${selectedDayTimeline.height}px` }}>
                            {Array.from(
                              { length: selectedDayTimeline.endHour - selectedDayTimeline.startHour },
                              (_, index) => selectedDayTimeline.startHour + index,
                            ).map((hour, index) => (
                              <div
                                key={`timeline-label-${hour}`}
                                className="absolute right-0 pr-1 text-xs font-medium text-gray-400 dark:text-gray-500"
                                style={{ top: `${index * TIMELINE_HOUR_HEIGHT}px` }}
                              >
                                {formatHourLabel(hour)}
                              </div>
                            ))}
                            <div className="absolute bottom-0 right-0 pr-1 text-xs font-medium text-gray-400 dark:text-gray-500">
                              {formatHourLabel(selectedDayTimeline.endHour)}
                            </div>
                          </div>

                          <div
                            className="density-surface-panel relative overflow-hidden border border-gray-200 bg-white px-2 dark:border-gray-700/70 dark:bg-gray-800/80"
                            style={{ height: `${selectedDayTimeline.height}px` }}
                          >
                            {Array.from(
                              { length: selectedDayTimeline.endHour - selectedDayTimeline.startHour },
                              (_, index) => selectedDayTimeline.startHour + index,
                            ).map((hour, index) => (
                              <div
                                key={`timeline-line-${hour}`}
                                className="absolute inset-x-0 border-t border-gray-200/80 dark:border-white/10"
                                style={{ top: `${index * TIMELINE_HOUR_HEIGHT}px` }}
                              />
                            ))}
                            <div className="absolute inset-x-0 bottom-0 border-t border-gray-200/80 dark:border-white/10" />

                            {selectedDayTimeline.nowOffset !== null && (
                              <div
                                className="absolute inset-x-0 z-20 border-t-2 border-orange-500"
                                style={{ top: `${selectedDayTimeline.nowOffset}px` }}
                              />
                            )}

                            {selectedDayTimeline.timedEvents.map((event) => (
                              <div
                                key={event.event.id}
                                className="absolute px-1"
                                style={{
                                  top: `${event.top}px`,
                                  height: `${event.height}px`,
                                  left: `${(event.column / event.columns) * 100}%`,
                                  width: `${100 / event.columns}%`,
                                  zIndex: event.status === "live" ? 30 : 10 + event.column,
                                }}
                              >
                                <DayTimelineEventCard
                                  event={event.event}
                                  timeZone={activeTimeZone}
                                  status={event.status}
                                  layout="timeline"
                                  visualHeight={event.height}
                                />
                              </div>
                            ))}
                          </div>
                        </div>
                      </div>
                    </div>
                  ) : selectedDayEvents.length ? (
                    <p className="text-sm text-gray-600 dark:text-gray-300">
                      No timed events on {format(selectedDay, "EEE d MMM")}.
                    </p>
                  ) : null}

                  {selectedDayRecordings.length ? (
                    <div>
                      <div className="text-xs font-semibold uppercase tracking-[0.16em] text-orange-700 dark:text-orange-300">
                        Recorded meetings
                      </div>
                      <div className="mt-3 space-y-3">
                        {selectedDayRecordings.map((recording) => (
                          <DashboardRecordingCard
                            key={recording.id}
                            recording={recording}
                            timeZone={activeTimeZone}
                            showDate={false}
                          />
                        ))}
                      </div>
                    </div>
                  ) : null}
                </div>
              ) : (
                <p className="mt-2 text-sm text-gray-600 dark:text-gray-300">
                  No events or meetings on {format(selectedDay, "EEE d MMM")}.
                </p>
              )}
            </div>
          ) : (
            footerText
          )}
        </div>
      )}
    </div>
  );
}
