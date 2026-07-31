"use client";

import { addMonths, format, isSameDay, isSameMonth, startOfDay } from "date-fns";
import {
  CalendarRange,
  ChevronLeft,
  ChevronRight,
  History,
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
    agendaPastItems,
    agendaUpcomingItems,
    agendaShowsPastItems,
    canTogglePastAgendaItems,
    handleTogglePastAgendaItems,
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
    <div className="density-surface border border-action-border bg-surface-card shadow-card">
      <div className="mt-2 flex items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          <div className="rounded-2xl bg-action-tint p-2 text-action-text">
            <CalendarRange className="h-5 w-5" />
          </div>
          <div>
            <h2 className="density-heading-section text-2xl font-semibold text-foreground">
              Calendar
            </h2>
            <p
              className="mt-1 text-sm text-contrast-helper"
              suppressHydrationWarning
            >
              {formatTimeZoneDate(now, activeTimeZone, "EEEE, d MMMM yyyy")}
            </p>
            {nextEventHelper && (
              <p className="mt-1 text-xs font-medium text-action-text">
                {nextEventHelper}
              </p>
            )}
          </div>
        </div>

        <div className="pt-1 text-right">
          <div
            className="text-2xl font-semibold tracking-tight text-foreground"
            suppressHydrationWarning
          >
            {formatTimeZoneDate(now, activeTimeZone, "HH:mm")}
          </div>
          <div className="mt-1 text-xs text-contrast-helper">
            {activeTimeZone}
          </div>
        </div>
      </div>

      <div className="density-surface-subtle mt-6 border border-surface-border bg-surface-card p-4 shadow-inner">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="text-xs font-semibold uppercase tracking-[0.18em] text-contrast-helper">
              {isViewingCurrentMonth ? "This month" : "Viewing"}
            </div>
            <div
              className="mt-1 text-lg font-semibold text-foreground"
              suppressHydrationWarning
            >
              {viewedMonthLabel}
            </div>
          </div>

          <div className="inline-flex items-center rounded-full border border-surface-border bg-surface-card p-1 text-sm shadow-card">
            <button
              type="button"
              onClick={() => setViewMode("month")}
              aria-pressed={viewMode === "month"}
              className={`inline-flex items-center gap-2 rounded-full px-3 py-2 font-medium transition-colors ${
                viewMode === "month"
                  ? "bg-action text-foreground shadow-card"
                  : "text-contrast-helper hover:text-foreground"
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
                  ? "bg-action text-foreground shadow-card"
                  : "text-contrast-helper hover:text-foreground"
              }`}
            >
              <List className="h-4 w-4" />
              <span className="hidden sm:inline">Agenda</span>
            </button>
          </div>
        </div>

        <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
          <div className="min-h-6 text-sm font-medium text-contrast-helper">
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
              className="inline-flex h-10 items-center justify-center rounded-full border border-surface-border bg-surface-card px-4 text-sm font-medium text-contrast-muted shadow-card transition-colors hover:border-action-border hover:bg-action-tint hover:text-action-text disabled:cursor-default disabled:opacity-60"
            >
              Today
            </button>
            <button
              type="button"
              onClick={handlePreviousMonth}
              aria-label={`View ${format(addMonths(viewedMonth, -1), "MMMM yyyy")}`}
              className="inline-flex h-10 w-10 items-center justify-center rounded-full border border-surface-border bg-surface-card text-contrast-helper shadow-card transition-colors hover:border-action-border hover:bg-action-tint hover:text-action-text"
            >
              <ChevronLeft className="h-4 w-4" />
            </button>
            <button
              type="button"
              onClick={handleNextMonth}
              aria-label={`View ${format(addMonths(viewedMonth, 1), "MMMM yyyy")}`}
              className="inline-flex h-10 w-10 items-center justify-center rounded-full border border-surface-border bg-surface-card text-contrast-helper shadow-card transition-colors hover:border-action-border hover:bg-action-tint hover:text-action-text"
            >
              <ChevronRight className="h-4 w-4" />
            </button>
          </div>
        </div>

        {viewMode === "month" ? (
          <div className="density-surface-panel mt-5 border border-surface-border bg-surface-card p-4">
            <div className="grid grid-cols-7 gap-2 text-center">
              {WEEK_DAYS.map((day) => (
                <div
                  key={day}
                  className="text-xs font-medium text-contrast-icon-muted"
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
                      ? "bg-action text-foreground shadow-float"
                      : "border border-action-border bg-action-tint text-action-text"
                    : isSelectedDay && inCurrentMonth
                      ? "border border-action-border bg-action-tint text-action-text shadow-card"
                      : inCurrentMonth
                        ? "bg-surface-inset text-contrast-muted hover:border-action-border hover:bg-action-tint hover:text-action-text bg-surface-card"
                        : "text-contrast-icon-muted"
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
                                className={`h-1.5 w-1.5 rounded-full border border-surface-border/40 ${dot.className}`}
                                style={dot.style}
                              />
                            );
                          })}
                          {extraDots > 0 && (
                            <span className={`text-[10px] font-semibold ${
                              isCurrentDay
                                ? "text-foreground"
                                : "text-action-text"
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
          <div className="density-surface-panel mt-5 border border-surface-border bg-surface-card p-5 shadow-inner">
            <div className="text-sm font-semibold text-foreground">
              Agenda
            </div>
            {calendarLoading ? (
              <div className="mt-3 inline-flex items-center gap-2 text-sm text-contrast-helper">
                <Loader2 className="h-4 w-4 animate-spin" />
                Loading agenda...
              </div>
            ) : monthAgendaItems.length ? (
              <div className="mt-4 space-y-3">
                {canTogglePastAgendaItems && (
                  <button
                    type="button"
                    onClick={handleTogglePastAgendaItems}
                    className="inline-flex items-center gap-2 rounded-full border border-surface-border bg-surface-card px-3 py-1.5 text-xs font-medium text-contrast-helper shadow-card transition-colors hover:border-action-border hover:bg-action-tint hover:text-action-text"
                  >
                    <History className="h-3.5 w-3.5" />
                    {agendaShowsPastItems
                      ? "Hide past events"
                      : `Show ${agendaPastItems.length} past ${agendaPastItems.length === 1 ? "event" : "events"}`}
                  </button>
                )}
                {(agendaShowsPastItems
                  ? [...agendaPastItems, ...agendaUpcomingItems]
                  : agendaUpcomingItems
                ).map((item) => (
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
                {!agendaShowsPastItems && agendaUpcomingItems.length === 0 && (
                  <p className="text-sm text-contrast-helper">
                    <span suppressHydrationWarning>
                      No upcoming events in {viewedMonthLabel}.
                    </span>
                  </p>
                )}
              </div>
            ) : (
              <p className="mt-2 text-sm text-contrast-helper">
                <span suppressHydrationWarning>{footerText}</span>
              </p>
            )}
          </div>
        )}
      </div>

      {viewMode === "month" && (
        <div className="density-surface-panel mt-4 border border-surface-border bg-surface-card p-4 text-sm text-contrast-helper shadow-inner">
          {calendarLoading ? (
            <span className="inline-flex items-center gap-2">
              <Loader2 className="h-4 w-4 animate-spin" />
              Loading day agenda...
            </span>
          ) : selectedDay && selectedDayLabel && monthHasContent ? (
            <div>
              <div>
                <div className="text-sm font-semibold text-foreground">
                  {selectedDayLabel}
                </div>
                <p className="mt-1 text-xs text-contrast-helper">
                  {selectedDayState === "today"
                    ? "Live day view"
                    : "Day agenda"} in {activeTimeZone}
                </p>
              </div>
              {selectedDayHasContent ? (
                <div className="mt-4 space-y-4">
                  {selectedDayTimeline?.allDayEvents.length ? (
                    <div>
                      <div className="text-xs font-semibold uppercase tracking-[0.16em] text-contrast-helper">
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
                      <div className="text-xs font-semibold uppercase tracking-[0.16em] text-contrast-helper">
                        Timed agenda
                      </div>

                      <div className="mt-3 space-y-3 md:hidden">
                        {selectedDayTimeline.timedEvents.map((event, index) => (
                          <div key={event.event.id} className="space-y-3">
                            {mobileNowDividerIndex === index && (
                              <div className="h-px w-full bg-action" />
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
                          <div className="h-px w-full bg-action" />
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
                                className="absolute right-0 pr-1 text-xs font-medium text-contrast-icon-muted"
                                style={{ top: `${index * TIMELINE_HOUR_HEIGHT}px` }}
                              >
                                {formatHourLabel(hour)}
                              </div>
                            ))}
                            <div className="absolute bottom-0 right-0 pr-1 text-xs font-medium text-contrast-icon-muted">
                              {formatHourLabel(selectedDayTimeline.endHour)}
                            </div>
                          </div>

                          <div
                            className="density-surface-panel relative overflow-hidden border border-surface-border bg-surface-card px-2"
                            style={{ height: `${selectedDayTimeline.height}px` }}
                          >
                            {Array.from(
                              { length: selectedDayTimeline.endHour - selectedDayTimeline.startHour },
                              (_, index) => selectedDayTimeline.startHour + index,
                            ).map((hour, index) => (
                              <div
                                key={`timeline-line-${hour}`}
                                className="absolute inset-x-0 border-t border-surface-border"
                                style={{ top: `${index * TIMELINE_HOUR_HEIGHT}px` }}
                              />
                            ))}
                            <div className="absolute inset-x-0 bottom-0 border-t border-surface-border" />

                            {selectedDayTimeline.nowOffset !== null && (
                              <div
                                className="absolute inset-x-0 z-20 border-t-2 border-action"
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
                                  continuesBefore={event.continuesBefore}
                                  continuesAfter={event.continuesAfter}
                                />
                              </div>
                            ))}
                          </div>
                        </div>
                      </div>
                    </div>
                  ) : selectedDayEvents.length ? (
                    <p className="text-sm text-contrast-helper">
                      No timed events on {format(selectedDay, "EEE d MMM")}.
                    </p>
                  ) : null}

                  {selectedDayRecordings.length ? (
                    <div>
                      <div className="text-xs font-semibold uppercase tracking-[0.16em] text-action-text">
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
                <p className="mt-2 text-sm text-contrast-helper">
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
