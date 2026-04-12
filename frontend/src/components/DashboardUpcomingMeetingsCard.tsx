"use client";

import { useEffect, useMemo, useState } from "react";
import {
  addMonths,
  addDays,
  differenceInMinutes,
  eachDayOfInterval,
  endOfDay,
  endOfMonth,
  endOfWeek,
  format,
  isSameDay,
  isSameMonth,
  isToday,
  parseISO,
  startOfDay,
  startOfMonth,
  startOfWeek,
} from "date-fns";
import {
  CalendarRange,
  ChevronLeft,
  ChevronRight,
  ExternalLink,
  LayoutGrid,
  Loader2,
  List,
  MapPin,
} from "lucide-react";
import { getCalendarDashboardSummary } from "@/lib/api";
import { CalendarDashboardEvent, CalendarDashboardSummary } from "@/types";

const WEEK_DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
type CalendarViewMode = "month" | "agenda";
const MAX_VISIBLE_DOTS = 4;


function getEventStart(event: CalendarDashboardEvent): Date | null {
  if (event.is_all_day && event.start_date) {
    return parseISO(event.start_date);
  }
  if (event.starts_at) {
    return parseISO(event.starts_at);
  }
  return null;
}


function getEventEnd(event: CalendarDashboardEvent): Date | null {
  if (event.is_all_day && event.end_date) {
    return addDays(parseISO(event.end_date), -1);
  }
  if (event.ends_at) {
    return parseISO(event.ends_at);
  }
  return getEventStart(event);
}


function eventOccursOnDay(event: CalendarDashboardEvent, day: Date): boolean {
  const eventStart = getEventStart(event);
  const eventEnd = getEventEnd(event);
  if (!eventStart || !eventEnd) {
    return false;
  }

  if (event.is_all_day) {
    return startOfDay(day) >= startOfDay(eventStart) && startOfDay(day) <= startOfDay(eventEnd);
  }

  return eventStart <= endOfDay(day) && eventEnd >= startOfDay(day);
}


function isFutureOrLiveEvent(event: CalendarDashboardEvent, now: Date): boolean {
  const eventStart = getEventStart(event);
  const eventEnd = getEventEnd(event);
  if (!eventStart || !eventEnd) {
    return false;
  }

  if (event.is_all_day) {
    const eventEndExclusive = event.end_date ? parseISO(event.end_date) : addDays(startOfDay(eventStart), 1);
    return eventEndExclusive > startOfDay(now);
  }

  return eventEnd >= now;
}


function buildNextEventHelper(
  summary: CalendarDashboardSummary | null,
  now: Date,
): string | null {
  if (!summary?.next_event) {
    switch (summary?.state) {
      case "provider_not_configured":
        return "Calendar providers are not configured";
      case "no_accounts":
        return "Connect a calendar account to get started";
      case "no_selected_calendars":
        return "Choose one or more calendars to sync";
      case "sync_in_progress":
        return "Calendar sync in progress";
      default:
        return "No upcoming events";
    }
  }

  const eventStart = getEventStart(summary.next_event);
  if (!eventStart) {
    return "No upcoming events";
  }

  const eventEnd = getEventEnd(summary.next_event);
  if (eventEnd && eventStart <= now && eventEnd >= now) {
    return "Event live now";
  }

  const diffMinutes = Math.max(0, differenceInMinutes(eventStart, now));
  if (diffMinutes >= 24 * 60) {
    const diffDays = Math.max(1, Math.floor(diffMinutes / (24 * 60)));
    return `Next event in ${diffDays}d`;
  }

  const hours = Math.floor(diffMinutes / 60);
  const minutes = diffMinutes % 60;
  return `Next event in ${hours}hrs${minutes}m`;
}


function buildFooterText(
  summary: CalendarDashboardSummary | null,
  viewedMonthLabel: string,
  calendarError: string | null,
): string {
  if (calendarError) {
    return calendarError;
  }

  switch (summary?.state) {
    case "provider_not_configured":
      return "Calendar providers are not configured.";
    case "no_accounts":
      return "No calendar accounts connected.";
    case "no_selected_calendars":
      return "Connected accounts have no selected calendars.";
    case "sync_in_progress":
      return "Syncing calendar data...";
    case "no_events":
      return `No events in ${viewedMonthLabel}.`;
    case "ready":
      return `No upcoming events in ${viewedMonthLabel}.`;
    default:
      return "Browse past and future months.";
  }
}


function formatAgendaDate(event: CalendarDashboardEvent): string {
  if (event.is_all_day && event.start_date) {
    const startDate = parseISO(event.start_date);
    const endDate = event.end_date ? addDays(parseISO(event.end_date), -1) : startDate;
    if (format(startDate, "yyyy-MM-dd") !== format(endDate, "yyyy-MM-dd")) {
      return `${format(startDate, "EEE d MMM")} - ${format(endDate, "EEE d MMM")}`;
    }
    return format(startDate, "EEE d MMM");
  }

  const startsAt = getEventStart(event);
  return startsAt ? format(startsAt, "EEE d MMM") : "Unknown date";
}


function formatAgendaTime(event: CalendarDashboardEvent): string {
  if (event.is_all_day) {
    return "All day";
  }

  const startsAt = getEventStart(event);
  const endsAt = getEventEnd(event);
  if (!startsAt) {
    return "Time unavailable";
  }
  if (!endsAt) {
    return format(startsAt, "HH:mm");
  }
  return `${format(startsAt, "HH:mm")} - ${format(endsAt, "HH:mm")}`;
}


function isHttpUrl(value: string | null | undefined): boolean {
  if (!value) {
    return false;
  }

  try {
    const parsed = new URL(value);
    return parsed.protocol === "http:" || parsed.protocol === "https:";
  } catch {
    return false;
  }
}


function normaliseComparableUrl(value: string | null | undefined): string | null {
  if (!value) {
    return null;
  }

  try {
    const parsed = new URL(value);
    const normalisedPath = parsed.pathname.replace(/\/$/, "") || "/";
    return `${parsed.protocol}//${parsed.host}${normalisedPath}${parsed.search}${parsed.hash}`;
  } catch {
    return null;
  }
}


function AgendaEventCard({ event }: { event: CalendarDashboardEvent }) {
  const locationText = event.location?.trim() || null;
  const meetingUrl = event.meeting_url?.trim() || null;
  const hasTrustedMeetingUrl = Boolean(
    meetingUrl && event.meeting_url_trusted,
  );
  const hasUntrustedMeetingUrl = Boolean(
    meetingUrl && !event.meeting_url_trusted,
  );
  const locationIsUrl = isHttpUrl(locationText);
  const locationMatchesTrustedMeetingUrl = Boolean(
    hasTrustedMeetingUrl &&
      normaliseComparableUrl(locationText) &&
      normaliseComparableUrl(locationText) === normaliseComparableUrl(meetingUrl),
  );
  const showLocation = Boolean(locationText && !locationMatchesTrustedMeetingUrl);
  const showSeparateUntrustedUrl = Boolean(
    hasUntrustedMeetingUrl && meetingUrl && meetingUrl !== locationText,
  );

  return (
    <div className="rounded-xl border border-white/70 bg-white/80 p-4 dark:border-white/10 dark:bg-gray-900/70">
      <div className="flex flex-wrap items-center gap-2 text-xs font-medium uppercase tracking-[0.12em] text-gray-500 dark:text-gray-400">
        <span>{formatAgendaDate(event)}</span>
        <span>•</span>
        <span>{formatAgendaTime(event)}</span>
      </div>
      <div className="mt-2 text-base font-semibold text-gray-950 dark:text-white">
        {event.title}
      </div>
      <div className="mt-1 text-sm text-gray-600 dark:text-gray-300">
        {event.calendar_name}
      </div>
      {(showLocation || showSeparateUntrustedUrl) && (
        <div className="mt-3 space-y-2 text-sm text-gray-600 dark:text-gray-300">
          {showLocation && locationText && (
            locationIsUrl ? (
              <a
                href={locationText}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-start gap-2 text-gray-600 transition-colors hover:text-gray-900 hover:underline dark:text-gray-300 dark:hover:text-white"
              >
                <ExternalLink className="mt-0.5 h-4 w-4 shrink-0 text-gray-400 dark:text-gray-500" />
                <span className="min-w-0 break-all">{locationText}</span>
              </a>
            ) : (
              <div className="inline-flex items-start gap-2">
                <MapPin className="mt-0.5 h-4 w-4 shrink-0 text-orange-600 dark:text-orange-300" />
                <span>{locationText}</span>
              </div>
            )
          )}

          {showSeparateUntrustedUrl && meetingUrl && (
            <a
              href={meetingUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-start gap-2 text-gray-600 transition-colors hover:text-gray-900 hover:underline dark:text-gray-300 dark:hover:text-white"
            >
              <ExternalLink className="mt-0.5 h-4 w-4 shrink-0 text-gray-400 dark:text-gray-500" />
              <span className="min-w-0 break-all">{meetingUrl}</span>
            </a>
          )}
        </div>
      )}
      {hasTrustedMeetingUrl && meetingUrl && (
        <a
          href={meetingUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="mt-3 inline-flex items-center gap-2 text-sm font-medium text-orange-700 transition-colors hover:text-orange-800 dark:text-orange-300 dark:hover:text-orange-200"
        >
          <ExternalLink className="h-4 w-4" />
          <span>Join Meeting</span>
        </a>
      )}
    </div>
  );
}

export default function DashboardUpcomingMeetingsCard() {
  const [now, setNow] = useState<Date>(() => new Date());
  const [viewedMonth, setViewedMonth] = useState<Date>(() =>
    startOfMonth(new Date()),
  );
  const [viewMode, setViewMode] = useState<CalendarViewMode>("month");
  const [selectedDay, setSelectedDay] = useState<Date | null>(() =>
    startOfDay(new Date()),
  );
  const [summary, setSummary] = useState<CalendarDashboardSummary | null>(null);
  const [calendarLoading, setCalendarLoading] = useState(true);
  const [calendarError, setCalendarError] = useState<string | null>(null);

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

  const viewedMonthKey = format(viewedMonth, "yyyy-MM");

  useEffect(() => {
    setSelectedDay((currentSelectedDay) => {
      if (currentSelectedDay && isSameMonth(currentSelectedDay, viewedMonth)) {
        return currentSelectedDay;
      }
      if (isSameMonth(viewedMonth, now)) {
        return startOfDay(now);
      }
      return startOfMonth(viewedMonth);
    });
  }, [now, viewedMonth]);

  useEffect(() => {
    let active = true;

    const loadSummary = async () => {
      if (active) {
        setCalendarLoading(true);
        setCalendarError(null);
      }

      try {
        const response = await getCalendarDashboardSummary(viewedMonthKey);
        if (!active) {
          return;
        }
        setSummary(response);
      } catch {
        if (!active) {
          return;
        }
        setCalendarError("Unable to load calendar data.");
      } finally {
        if (active) {
          setCalendarLoading(false);
        }
      }
    };

    void loadSummary();
    const interval = window.setInterval(() => {
      void loadSummary();
    }, 60000);

    return () => {
      active = false;
      window.clearInterval(interval);
    };
  }, [viewedMonthKey]);

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
  const monthEvents = useMemo(() => summary?.agenda_items ?? [], [summary]);
  const dayCounts = useMemo(() => {
    const countMap = new Map<string, number>();
    summary?.day_counts.forEach((entry) => {
      countMap.set(entry.date, entry.count);
    });
    return countMap;
  }, [summary]);
  const nextEventHelper = useMemo(
    () => buildNextEventHelper(summary, now),
    [summary, now],
  );
  const footerText = useMemo(
    () => buildFooterText(summary, viewedMonthLabel, calendarError),
    [summary, viewedMonthLabel, calendarError],
  );
  const futureAgendaItems = useMemo(
    () => monthEvents.filter((event) => isFutureOrLiveEvent(event, now)),
    [monthEvents, now],
  );
  const selectedDayEvents = useMemo(() => {
    if (!selectedDay) {
      return [];
    }
    return monthEvents.filter((event) => eventOccursOnDay(event, selectedDay));
  }, [monthEvents, selectedDay]);
  const selectedDayLabel = selectedDay ? format(selectedDay, "EEEE d MMMM") : null;

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
            {format(now, "HH:mm")}
          </div>
        </div>
      </div>

      <div className="mt-6 rounded-[1.75rem] border border-white/70 bg-white/75 p-4 shadow-inner shadow-orange-950/5 dark:border-white/10 dark:bg-gray-900/60">
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
            ) : isViewingCurrentMonth ? (
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
                const isSelectedDay = Boolean(selectedDay && isSameDay(day, selectedDay));
                const count = dayCounts.get(format(day, "yyyy-MM-dd")) || 0;
                const visibleDots = Math.min(count, MAX_VISIBLE_DOTS);
                const extraDots = count - visibleDots;
                const dotClassName = isCurrentDay && inCurrentMonth
                  ? "bg-white/90"
                  : "bg-orange-500 dark:bg-orange-400";
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
                      {count > 0 && (
                        <div className="mt-1 flex items-center gap-1">
                          {Array.from({ length: visibleDots }).map((_, index) => (
                            <span
                              key={`${day.toISOString()}-dot-${index}`}
                              className={`h-1.5 w-1.5 rounded-full ${dotClassName}`}
                            />
                          ))}
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
          <div className="mt-5 rounded-[1.5rem] border border-dashed border-orange-200 bg-orange-50/70 p-5 dark:border-orange-500/20 dark:bg-orange-500/10">
            <div className="text-sm font-semibold text-gray-950 dark:text-white">
              Agenda
            </div>
            {calendarLoading ? (
              <div className="mt-3 inline-flex items-center gap-2 text-sm text-gray-600 dark:text-gray-300">
                <Loader2 className="h-4 w-4 animate-spin" />
                Loading agenda...
              </div>
            ) : futureAgendaItems.length ? (
              <div className="mt-4 space-y-3">
                {futureAgendaItems.map((event) => (
                  <AgendaEventCard key={event.id} event={event} />
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
        <div className="mt-4 rounded-[1.5rem] border border-dashed border-orange-200 bg-orange-50/70 p-4 text-sm text-gray-600 dark:border-orange-500/20 dark:bg-orange-500/10 dark:text-gray-300">
          {calendarLoading ? (
            <span className="inline-flex items-center gap-2">
              <Loader2 className="h-4 w-4 animate-spin" />
              Loading day agenda...
            </span>
          ) : summary?.state === "ready" && selectedDay && selectedDayLabel ? (
            <div>
              <div className="text-sm font-semibold text-gray-950 dark:text-white">
                {selectedDayLabel}
              </div>
              {selectedDayEvents.length ? (
                <div className="mt-4 space-y-3">
                  {selectedDayEvents.map((event) => (
                    <AgendaEventCard key={event.id} event={event} />
                  ))}
                </div>
              ) : (
                <p className="mt-2 text-sm text-gray-600 dark:text-gray-300">
                  No events on {format(selectedDay, "EEE d MMM")}.
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
