"use client";

import { format } from "date-fns";
import { CalendarClock, History, LayoutGrid, Loader2 } from "lucide-react";

import Button from "@/components/ui/Button";
import { formatTimeZoneDate } from "@/lib/timezone";

import {
  AgendaEventCard,
  DashboardRecordingCard,
  DayTimelineAllDayChip,
  DayTimelineEventCard,
} from "./CalendarCards";
import { TIMELINE_HOUR_HEIGHT, formatHourLabel } from "./calendarUtils";
import type { CalendarDashboard } from "./useCalendarDashboard";

/**
 * What is happening, as a list rather than as a grid.
 *
 * This was the other half of the calendar card, reachable only by a toggle that
 * hid the month grid. It is now permanent, and it absorbed the day view that
 * used to sit underneath the grid: picking a day in the grid scopes this module
 * to that day, and the header returns it to the whole month.
 *
 * It opens on today, because a live day timeline is the more useful default on
 * a dashboard than a month's worth of list.
 */
export default function AgendaCard({
  calendar,
}: {
  calendar: CalendarDashboard;
}) {
  const {
    now,
    activeTimeZone,
    agendaMode,
    selectedDay,
    calendarLoading,
    viewedMonthLabel,
    nextEventHelper,
    footerText,
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
    handleShowMonthAgenda,
  } = calendar;

  const dayInFocus = agendaMode === "day" ? selectedDay : null;
  const scopeLabel = dayInFocus ? selectedDayLabel : viewedMonthLabel;
  const visibleAgendaItems = agendaShowsPastItems
    ? [...agendaPastItems, ...agendaUpcomingItems]
    : agendaUpcomingItems;

  return (
    <div className="density-surface flex h-full min-h-0 flex-col border border-surface-border bg-surface-card shadow-card">
      <div className="flex items-start gap-3">
        <CalendarClock className="mt-0.5 h-5 w-5 shrink-0 text-action-text" />
        <div className="min-w-0 flex-1">
          <h2 className="text-base font-semibold text-foreground">Agenda</h2>
          <p
            className="mt-0.5 text-sm text-contrast-helper"
            suppressHydrationWarning
          >
            {scopeLabel}
            {dayInFocus && selectedDayState === "today" ? " (today)" : ""}
          </p>
          {nextEventHelper && (
            <p className="mt-0.5 text-xs font-medium text-action-text">
              {nextEventHelper}
            </p>
          )}
        </div>

        <div className="shrink-0 text-right">
          <div
            className="text-xl font-semibold tracking-tight text-foreground"
            suppressHydrationWarning
          >
            {formatTimeZoneDate(now, activeTimeZone, "HH:mm")}
          </div>
          <div className="mt-0.5 text-xs text-contrast-helper">
            {activeTimeZone}
          </div>
        </div>
      </div>

      {(dayInFocus || canTogglePastAgendaItems) && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {dayInFocus ? (
            <Button
              size="sm"
              variant="ghost"
              iconLeft={<LayoutGrid className="h-3.5 w-3.5" />}
              onClick={handleShowMonthAgenda}
            >
              Whole month
            </Button>
          ) : (
            canTogglePastAgendaItems && (
              <Button
                size="sm"
                variant="ghost"
                iconLeft={<History className="h-3.5 w-3.5" />}
                onClick={handleTogglePastAgendaItems}
              >
                {agendaShowsPastItems
                  ? "Hide past events"
                  : `Show ${agendaPastItems.length} past ${agendaPastItems.length === 1 ? "event" : "events"}`}
              </Button>
            )
          )}
        </div>
      )}

      {/* A container rather than a viewport query: this module is full width on
          a phone and a third of the workspace at 1600px, so the timeline has to
          answer to how wide the module actually is. */}
      <div className="@container mt-4 min-h-0 flex-1 overflow-y-auto">
        {calendarLoading ? (
          <div className="inline-flex items-center gap-2 text-sm text-contrast-helper">
            <Loader2 className="h-4 w-4 animate-spin" />
            Loading agenda
          </div>
        ) : dayInFocus ? (
          selectedDayHasContent ? (
            <div className="space-y-4">
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

                  <div className="mt-3 space-y-3 @min-[34rem]:hidden">
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

                  <div className="mt-3 hidden @min-[34rem]:block">
                    <div className="grid grid-cols-[4rem_minmax(0,1fr)] gap-3">
                      <div
                        className="relative"
                        style={{ height: `${selectedDayTimeline.height}px` }}
                      >
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

                      {/* No surface of its own: the hour rules are the structure,
                          and a filled panel here would be a card inside a card. */}
                      <div
                        className="relative overflow-hidden"
                        style={{ height: `${selectedDayTimeline.height}px` }}
                      >
                        {Array.from(
                          { length: selectedDayTimeline.endHour - selectedDayTimeline.startHour },
                          (_, index) => selectedDayTimeline.startHour + index,
                        ).map((hour, index) => (
                          <div
                            key={`timeline-line-${hour}`}
                            className="absolute inset-x-0 border-t border-surface-divider"
                            style={{ top: `${index * TIMELINE_HOUR_HEIGHT}px` }}
                          />
                        ))}
                        <div className="absolute inset-x-0 bottom-0 border-t border-surface-divider" />

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
                  No timed events on {format(dayInFocus, "EEE d MMM")}.
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
            // An empty day says so, but a month with nothing in it at all is
            // usually a calendar that is not connected yet, and that needs the
            // explanation rather than the shrug.
            <p className="text-sm text-contrast-helper">
              {monthHasContent ? (
                `Nothing on ${format(dayInFocus, "EEE d MMM")}.`
              ) : (
                <span suppressHydrationWarning>{footerText}</span>
              )}
            </p>
          )
        ) : visibleAgendaItems.length ? (
          <div className="space-y-3">
            {visibleAgendaItems.map((item) =>
              item.kind === "event" ? (
                <AgendaEventCard
                  key={`event-${item.event.id}`}
                  event={item.event}
                  timeZone={activeTimeZone}
                />
              ) : (
                <DashboardRecordingCard
                  key={`recording-${item.recording.id}`}
                  recording={item.recording}
                  timeZone={activeTimeZone}
                  showDate
                />
              ),
            )}
          </div>
        ) : (
          <p className="text-sm text-contrast-helper">
            <span suppressHydrationWarning>{footerText}</span>
          </p>
        )}
      </div>
    </div>
  );
}
