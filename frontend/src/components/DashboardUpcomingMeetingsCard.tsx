"use client";

import { useEffect, useMemo, useState } from "react";
import {
  addMinutes,
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
import { COLOR_PALETTE } from "@/lib/constants";
import { getCalendarDashboardSummary } from "@/lib/api";
import {
  DEFAULT_TIME_ZONE,
  formatTimeZoneDate,
  getUserTimeZone,
  toTimeZoneDate,
} from "@/lib/timezone";
import { CalendarDashboardEvent, CalendarDashboardSummary } from "@/types";

const WEEK_DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
type CalendarViewMode = "month" | "agenda";
const MAX_VISIBLE_DOTS = 4;
const DEFAULT_TIMELINE_START_HOUR = 7;
const DEFAULT_TIMELINE_END_HOUR = 21;
const TIMELINE_HOUR_HEIGHT = 44;
const MIN_TIMELINE_EVENT_HEIGHT = 16;

type DayTimelineStatus = "past" | "live" | "upcoming";
type DayTimelineDayState = "past" | "today" | "future";

interface DayTimelineDraftEvent {
  event: CalendarDashboardEvent;
  startMinutes: number;
  endMinutes: number;
  status: DayTimelineStatus;
  continuesBefore: boolean;
  continuesAfter: boolean;
}

interface DayTimelineEvent extends DayTimelineDraftEvent {
  column: number;
  columns: number;
  top: number;
  height: number;
}

interface DayTimelineData {
  allDayEvents: CalendarDashboardEvent[];
  timedEvents: DayTimelineEvent[];
  startHour: number;
  endHour: number;
  height: number;
  nowOffset: number | null;
  nowLabel: string | null;
}


function getCalendarColourPresentation(colour: string | null | undefined) {
  const paletteColour = colour
    ? COLOR_PALETTE.find((option) => option.key === colour.toLowerCase())
    : null;

  if (paletteColour) {
    return { className: paletteColour.dot, style: undefined };
  }

  if (colour) {
    return { className: "", style: { backgroundColor: colour } };
  }

  return { className: "bg-orange-500", style: undefined };
}


function getDayCalendarColours(
  events: CalendarDashboardEvent[],
  day: Date,
  timeZone: string,
): string[] {
  const coloursByCalendarId = new Map<number, string>();

  events.forEach((event) => {
    if (!eventOccursOnDay(event, day, timeZone)) {
      return;
    }

    if (!coloursByCalendarId.has(event.calendar_id)) {
      coloursByCalendarId.set(event.calendar_id, event.calendar_colour || "orange");
    }
  });

  return Array.from(coloursByCalendarId.values());
}


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


function getTimelineEventEnd(event: CalendarDashboardEvent): Date | null {
  const eventStart = getEventStart(event);
  if (!eventStart) {
    return null;
  }

  const eventEnd = getEventEnd(event);
  if (!eventEnd || eventEnd <= eventStart) {
    return addMinutes(eventStart, 30);
  }

  return eventEnd;
}


function eventOccursOnDay(
  event: CalendarDashboardEvent,
  day: Date,
  timeZone: string,
): boolean {
  const eventStart = getEventStart(event);
  const eventEnd = getEventEnd(event);
  if (!eventStart || !eventEnd) {
    return false;
  }

  if (event.is_all_day) {
    return startOfDay(day) >= startOfDay(eventStart) && startOfDay(day) <= startOfDay(eventEnd);
  }

  const zonedStart = toTimeZoneDate(eventStart, timeZone);
  const zonedEnd = toTimeZoneDate(eventEnd, timeZone);
  return zonedStart <= endOfDay(day) && zonedEnd >= startOfDay(day);
}


function isFutureOrLiveEvent(
  event: CalendarDashboardEvent,
  now: Date,
  timeZone: string,
): boolean {
  const eventStart = getEventStart(event);
  const eventEnd = getEventEnd(event);
  if (!eventStart || !eventEnd) {
    return false;
  }

  if (event.is_all_day) {
    const localToday = startOfDay(toTimeZoneDate(now, timeZone));
    const eventEndExclusive = event.end_date ? parseISO(event.end_date) : addDays(startOfDay(eventStart), 1);
    return eventEndExclusive > localToday;
  }

  return eventEnd >= now;
}


function buildNextEventHelper(
  summary: CalendarDashboardSummary | null,
  now: Date,
  timeZone: string,
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

  if (summary.next_event.is_all_day) {
    const localToday = startOfDay(toTimeZoneDate(now, timeZone));
    const eventEndExclusive = summary.next_event.end_date
      ? parseISO(summary.next_event.end_date)
      : addDays(startOfDay(eventStart), 1);

    if (eventStart <= localToday && eventEndExclusive > localToday) {
      return "Event live now";
    }

    const diffMinutes = Math.max(0, differenceInMinutes(eventStart, localToday));
    if (diffMinutes >= 24 * 60) {
      const diffDays = Math.max(1, Math.floor(diffMinutes / (24 * 60)));
      return `Next event in ${diffDays} ${diffDays === 1 ? "day" : "days"}`;
    }

    return "Next event today";
  }

  const eventEnd = getEventEnd(summary.next_event);
  if (eventEnd && eventStart <= now && eventEnd >= now) {
    return "Event live now";
  }

  const diffMinutes = Math.max(0, differenceInMinutes(eventStart, now));
  if (diffMinutes >= 24 * 60) {
    const diffDays = Math.max(1, Math.floor(diffMinutes / (24 * 60)));
    return `Next event in ${diffDays} ${diffDays === 1 ? "day" : "days"}`;
  }

  if (diffMinutes < 60) {
    return `Next event in ${Math.max(1, diffMinutes)}min`;
  }

  const hours = Math.floor(diffMinutes / 60);
  const minutes = diffMinutes % 60;
  return `Next event in ${hours}${hours === 1 ? "hr" : "hrs"} ${minutes}min`;
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


function formatAgendaDate(event: CalendarDashboardEvent, timeZone: string): string {
  if (event.is_all_day && event.start_date) {
    const startDate = parseISO(event.start_date);
    const endDate = event.end_date ? addDays(parseISO(event.end_date), -1) : startDate;
    if (format(startDate, "yyyy-MM-dd") !== format(endDate, "yyyy-MM-dd")) {
      return `${format(startDate, "EEE d MMM")} - ${format(endDate, "EEE d MMM")}`;
    }
    return format(startDate, "EEE d MMM");
  }

  const startsAt = getEventStart(event);
  return startsAt
    ? formatTimeZoneDate(startsAt, timeZone, "EEE d MMM")
    : "Unknown date";
}


function formatAgendaTime(event: CalendarDashboardEvent, timeZone: string): string {
  if (event.is_all_day) {
    return "All day";
  }

  const startsAt = getEventStart(event);
  const endsAt = getEventEnd(event);
  if (!startsAt) {
    return "Time unavailable";
  }
  if (!endsAt) {
    return formatTimeZoneDate(startsAt, timeZone, "HH:mm");
  }
  return `${formatTimeZoneDate(startsAt, timeZone, "HH:mm")} - ${formatTimeZoneDate(endsAt, timeZone, "HH:mm")}`;
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


function getAgendaEventPresentation(event: CalendarDashboardEvent) {
  const locationText = event.location?.trim() || null;
  const meetingUrl = event.meeting_url?.trim() || null;
  const hasTrustedMeetingUrl = Boolean(
    meetingUrl && event.meeting_url_trusted,
  );
  const locationIsUrl = isHttpUrl(locationText);
  const locationMatchesTrustedMeetingUrl = Boolean(
    hasTrustedMeetingUrl &&
      normaliseComparableUrl(locationText) &&
      normaliseComparableUrl(locationText) === normaliseComparableUrl(meetingUrl),
  );
  const showLocation = Boolean(locationText && !locationMatchesTrustedMeetingUrl);
  const showMeetingUrl = Boolean(
    meetingUrl && (hasTrustedMeetingUrl || meetingUrl !== locationText),
  );

  return {
    locationText,
    meetingUrl,
    locationIsUrl,
    showLocation,
    showMeetingUrl,
  };
}


function formatHourLabel(hour: number): string {
  return `${String(hour).padStart(2, "0")}:00`;
}


function getTimelineTitleClass(visualHeight: number | undefined, showTimeRow: boolean): string {
  if (visualHeight === undefined) {
    return showTimeRow ? "mt-1 line-clamp-2 text-sm" : "truncate text-xs leading-4";
  }

  if (showTimeRow) {
    if (visualHeight < 64) {
      return "mt-1 line-clamp-2 text-xs leading-4";
    }
    return "mt-1 line-clamp-2 text-sm";
  }

  if (visualHeight < 22) {
    return "truncate text-[9px] leading-3";
  }
  if (visualHeight < 28) {
    return "truncate text-[10px] leading-3";
  }
  if (visualHeight < 34) {
    return "truncate text-[11px] leading-3.5";
  }
  if (visualHeight < 40) {
    return "truncate text-xs leading-4";
  }

  return "truncate text-[13px] leading-4";
}


function getTimelinePaddingClass(visualHeight: number | undefined, isSmallTimelineEvent: boolean): string {
  if (!isSmallTimelineEvent) {
    return "py-3";
  }

  if (visualHeight === undefined) {
    return "py-2";
  }

  if (visualHeight < 22) {
    return "py-0.5";
  }
  if (visualHeight < 28) {
    return "py-1";
  }
  if (visualHeight < 36) {
    return "py-1.5";
  }

  return "py-2";
}


function getTimelineIndicatorSizeClass(visualHeight: number | undefined): string {
  if (visualHeight !== undefined && visualHeight < 24) {
    return "h-3 w-3";
  }

  return "h-3.5 w-3.5";
}


function getTimelineDotSizeClass(visualHeight: number | undefined): string {
  if (visualHeight !== undefined && visualHeight < 24) {
    return "h-2 w-2";
  }

  return "h-2.5 w-2.5";
}


function layoutDayTimelineEvents(
  events: DayTimelineDraftEvent[],
): Array<DayTimelineDraftEvent & { column: number; columns: number }> {
  const positionedEvents: Array<DayTimelineDraftEvent & { column: number; columns: number }> = [];
  let cluster: DayTimelineDraftEvent[] = [];
  let clusterEndMinutes = -1;

  const flushCluster = () => {
    if (!cluster.length) {
      return;
    }

    const activeColumns: Array<{ column: number; endMinutes: number }> = [];
    const clusterEntries = cluster.map((event) => ({
      ...event,
      column: 0,
      columns: 1,
    }));
    let maxColumns = 1;

    clusterEntries.forEach((entry) => {
      for (let index = activeColumns.length - 1; index >= 0; index -= 1) {
        if (activeColumns[index].endMinutes <= entry.startMinutes) {
          activeColumns.splice(index, 1);
        }
      }

      const usedColumns = new Set(activeColumns.map((activeEntry) => activeEntry.column));
      let column = 0;
      while (usedColumns.has(column)) {
        column += 1;
      }

      entry.column = column;
      activeColumns.push({ column, endMinutes: entry.endMinutes });
      maxColumns = Math.max(maxColumns, activeColumns.length, column + 1);
    });

    clusterEntries.forEach((entry) => {
      entry.columns = maxColumns;
      positionedEvents.push(entry);
    });

    cluster = [];
    clusterEndMinutes = -1;
  };

  events.forEach((event) => {
    if (!cluster.length) {
      cluster = [event];
      clusterEndMinutes = event.endMinutes;
      return;
    }

    if (event.startMinutes < clusterEndMinutes) {
      cluster.push(event);
      clusterEndMinutes = Math.max(clusterEndMinutes, event.endMinutes);
      return;
    }

    flushCluster();
    cluster = [event];
    clusterEndMinutes = event.endMinutes;
  });

  flushCluster();

  return positionedEvents;
}


function buildDayTimeline(
  events: CalendarDashboardEvent[],
  day: Date | null,
  now: Date,
  timeZone: string,
  dayState: DayTimelineDayState | null,
): DayTimelineData | null {
  if (!day || !dayState) {
    return null;
  }

  const allDayEvents = events.filter((event) => event.is_all_day);
  const dayStart = startOfDay(day);
  const dayEnd = addMinutes(dayStart, 24 * 60);

  const timedEvents = events
    .filter((event) => !event.is_all_day)
    .map((event) => {
      const eventStart = getEventStart(event);
      const eventEnd = getTimelineEventEnd(event);
      if (!eventStart || !eventEnd) {
        return null;
      }

      const zonedStart = toTimeZoneDate(eventStart, timeZone);
      const zonedEnd = toTimeZoneDate(eventEnd, timeZone);
      const displayStart = zonedStart < dayStart ? dayStart : zonedStart;
      const displayEnd = zonedEnd > dayEnd ? dayEnd : zonedEnd;

      if (displayEnd <= dayStart || displayStart >= dayEnd) {
        return null;
      }

      let status: DayTimelineStatus = "upcoming";
      if (dayState === "past") {
        status = "past";
      } else if (dayState === "today") {
        if (eventEnd <= now) {
          status = "past";
        } else if (eventStart <= now && eventEnd > now) {
          status = "live";
        }
      }

      return {
        event,
        startMinutes: Math.max(0, differenceInMinutes(displayStart, dayStart)),
        endMinutes: Math.max(1, differenceInMinutes(displayEnd, dayStart)),
        status,
        continuesBefore: zonedStart < dayStart,
        continuesAfter: zonedEnd > dayEnd,
      } satisfies DayTimelineDraftEvent;
    })
    .filter((event): event is DayTimelineDraftEvent => Boolean(event))
    .sort(
      (leftEvent, rightEvent) =>
        leftEvent.startMinutes - rightEvent.startMinutes ||
        leftEvent.endMinutes - rightEvent.endMinutes ||
        leftEvent.event.title.localeCompare(rightEvent.event.title),
    );

  const nowMinutes = dayState === "today"
    ? differenceInMinutes(toTimeZoneDate(now, timeZone), dayStart)
    : null;
  const minuteFloor = timedEvents.map((event) => event.startMinutes);
  const minuteCeiling = timedEvents.map((event) => event.endMinutes);

  if (nowMinutes !== null) {
    minuteFloor.push(nowMinutes);
    minuteCeiling.push(nowMinutes);
  }

  let startHour = DEFAULT_TIMELINE_START_HOUR;
  let endHour = DEFAULT_TIMELINE_END_HOUR;

  if (minuteFloor.length && minuteCeiling.length) {
    const earliestHour = Math.floor(Math.min(...minuteFloor) / 60);
    const latestHour = Math.ceil(Math.max(...minuteCeiling) / 60);
    startHour = Math.max(0, Math.min(DEFAULT_TIMELINE_START_HOUR, earliestHour));
    endHour = Math.min(24, Math.max(DEFAULT_TIMELINE_END_HOUR, latestHour));
  }

  if (endHour <= startHour) {
    endHour = Math.min(24, startHour + 1);
  }

  const timelineStartMinutes = startHour * 60;
  const timelineEndMinutes = endHour * 60;
  const timelineHeight = Math.max(
    TIMELINE_HOUR_HEIGHT,
    (endHour - startHour) * TIMELINE_HOUR_HEIGHT,
  );
  const pixelsPerMinute = TIMELINE_HOUR_HEIGHT / 60;

  const laidOutTimedEvents = layoutDayTimelineEvents(timedEvents).map((timedEvent) => {
    const visibleStartMinutes = Math.max(timelineStartMinutes, timedEvent.startMinutes);
    const visibleEndMinutes = Math.min(timelineEndMinutes, timedEvent.endMinutes);
    const baseTop = Math.max(0, (visibleStartMinutes - timelineStartMinutes) * pixelsPerMinute);
    const rawHeight = Math.max(1, (visibleEndMinutes - visibleStartMinutes) * pixelsPerMinute);
    const inset = rawHeight > 12 ? 2 : 0;
    const top = Math.min(timelineHeight, baseTop + inset / 2);

    return {
      ...timedEvent,
      top,
      height: Math.min(
        timelineHeight - top,
        Math.max(MIN_TIMELINE_EVENT_HEIGHT, Math.max(1, rawHeight - inset)),
      ),
    } satisfies DayTimelineEvent;
  });

  const nowOffset = timedEvents.length && nowMinutes !== null && nowMinutes >= timelineStartMinutes && nowMinutes <= timelineEndMinutes
    ? (nowMinutes - timelineStartMinutes) * pixelsPerMinute
    : null;

  return {
    allDayEvents,
    timedEvents: laidOutTimedEvents,
    startHour,
    endHour,
    height: timelineHeight,
    nowOffset,
    nowLabel: nowOffset !== null ? formatTimeZoneDate(now, timeZone, "HH:mm") : null,
  };
}


function DayTimelineAllDayChip({
  event,
}: {
  event: CalendarDashboardEvent;
}) {
  const calendarColour = getCalendarColourPresentation(event.calendar_colour);

  return (
    <div className="relative overflow-hidden rounded-2xl border border-gray-200 bg-white px-4 py-3 shadow-sm dark:border-gray-700/70 dark:bg-gray-800/80">
      <span
        className={`absolute inset-y-0 left-0 w-1.5 ${calendarColour.className}`}
        style={calendarColour.style}
      />
      <div className="pl-2">
        <div className="flex flex-wrap items-center gap-2 text-[11px] font-medium uppercase tracking-[0.14em] text-gray-500 dark:text-gray-400">
          <span>All day</span>
          <span>•</span>
          <span>{event.calendar_name}</span>
        </div>
        <div className="mt-1 line-clamp-2 text-sm font-semibold text-gray-950 dark:text-white">
          {event.title}
        </div>
      </div>
    </div>
  );
}


function DayTimelineEventCard({
  event,
  timeZone,
  status,
  layout,
  visualHeight,
}: {
  event: CalendarDashboardEvent;
  timeZone: string;
  status: DayTimelineStatus;
  layout: "timeline" | "stacked";
  visualHeight?: number;
}) {
  const calendarColour = getCalendarColourPresentation(event.calendar_colour);
  const {
    locationText,
    meetingUrl,
    locationIsUrl,
    showLocation,
    showMeetingUrl,
  } = getAgendaEventPresentation(event);
  const isLive = status === "live";
  const isPast = status === "past";
  const primaryUrl = showMeetingUrl && meetingUrl
    ? meetingUrl
    : showLocation && locationText && locationIsUrl
      ? locationText
      : null;
  const timelineDensity = layout === "timeline"
    ? visualHeight !== undefined && visualHeight < 28
      ? "dense"
      : visualHeight !== undefined && visualHeight < 52
        ? "compact"
        : "comfortable"
    : "comfortable";
  const isSmallTimelineEvent = layout === "timeline" && timelineDensity !== "comfortable";
  const showSecondaryMetadata = layout === "stacked" || timelineDensity === "comfortable";
  const showPlainLocation = Boolean(showLocation && locationText && !locationIsUrl && showSecondaryMetadata);
  const showTimeRow = layout === "stacked" || !isSmallTimelineEvent;
  const showLiveBadge = layout === "stacked" && isLive;
  const titleClass = layout === "timeline"
    ? getTimelineTitleClass(visualHeight, showTimeRow)
    : "mt-1 line-clamp-2 text-sm";
  const paddingClass = layout === "timeline"
    ? getTimelinePaddingClass(visualHeight, isSmallTimelineEvent)
    : "py-3.5";
  const linkIndicatorClass = layout === "timeline"
    ? getTimelineIndicatorSizeClass(visualHeight)
    : "h-3.5 w-3.5";
  const dotSizeClass = layout === "timeline"
    ? getTimelineDotSizeClass(visualHeight)
    : "h-2.5 w-2.5";
  const cardClasses = `relative h-full overflow-hidden rounded-[5px] border bg-white shadow-sm dark:bg-gray-900/95 ${
    isLive
      ? "border-orange-300 shadow-orange-600/15 dark:border-orange-400/40"
      : "border-gray-200 dark:border-gray-700/70"
  } ${
    isPast ? "opacity-70" : ""
  } ${
    primaryUrl
      ? "block cursor-pointer transition-colors hover:border-orange-200 hover:bg-orange-50/60 dark:hover:border-orange-400/30 dark:hover:bg-orange-500/10"
      : ""
  }`;
  const cardContent = (
    <>
      <span
        className={`absolute inset-y-0 left-0 w-1.5 ${calendarColour.className}`}
        style={calendarColour.style}
      />
      <div className={`h-full pl-4 pr-3 ${paddingClass}`}>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            {showTimeRow && (
              <div className="flex flex-wrap items-center gap-2 text-[10px] font-medium uppercase tracking-[0.14em] text-gray-500 dark:text-gray-400">
                <span>{formatAgendaTime(event, timeZone)}</span>
                {showLiveBadge && (
                  <span className="rounded-full bg-orange-100 px-2 py-0.5 text-[10px] font-semibold tracking-[0.16em] text-orange-700 dark:bg-orange-500/15 dark:text-orange-200">
                    Live now
                  </span>
                )}
              </div>
            )}
            <div className={`font-semibold text-gray-950 dark:text-white ${titleClass}`}>
              {event.title}
            </div>
          </div>
          <div className="mt-1 flex shrink-0 items-center gap-1.5">
            {primaryUrl && (
              <ExternalLink className={`${linkIndicatorClass} text-gray-400 dark:text-gray-500`} />
            )}
            <span
              className={`${dotSizeClass} rounded-full ${calendarColour.className}`}
              style={calendarColour.style}
            />
          </div>
        </div>

        {showSecondaryMetadata && (
          <>
            <div className="mt-2 text-xs text-gray-600 dark:text-gray-300">
              {event.calendar_name}
            </div>

            {showPlainLocation && locationText && (
              <div className="mt-2 inline-flex max-w-full items-start gap-2 text-xs text-gray-600 dark:text-gray-300">
                <MapPin className="mt-0.5 h-3.5 w-3.5 shrink-0 text-orange-600 dark:text-orange-300" />
                <span className="line-clamp-2">{locationText}</span>
              </div>
            )}
          </>
        )}
      </div>
    </>
  );

  if (primaryUrl) {
    return (
      <a
        href={primaryUrl}
        target="_blank"
        rel="noopener noreferrer"
        className={cardClasses}
      >
        {cardContent}
      </a>
    );
  }

  return <div className={cardClasses}>{cardContent}</div>;
}


function AgendaEventCard({
  event,
  timeZone,
}: {
  event: CalendarDashboardEvent;
  timeZone: string;
}) {
  const calendarColour = getCalendarColourPresentation(event.calendar_colour);
  const {
    locationText,
    meetingUrl,
    locationIsUrl,
    showLocation,
    showMeetingUrl,
  } = getAgendaEventPresentation(event);

  return (
    <div className="rounded-xl border border-gray-200 bg-white p-4 dark:border-gray-700/70 dark:bg-gray-800/80">
      <div className="flex flex-wrap items-center gap-2 text-xs font-medium uppercase tracking-[0.12em] text-gray-500 dark:text-gray-400">
        <span>{formatAgendaDate(event, timeZone)}</span>
        <span>•</span>
        <span>{formatAgendaTime(event, timeZone)}</span>
      </div>
      <div className="mt-2 text-base font-semibold text-gray-950 dark:text-white">
        {event.title}
      </div>
      <div className="mt-1 inline-flex items-center gap-2 text-sm text-gray-600 dark:text-gray-300">
        <span
          className={`h-2.5 w-2.5 rounded-full ${calendarColour.className}`}
          style={calendarColour.style}
        />
        {event.calendar_name}
      </div>
      {(showLocation || showMeetingUrl) && (
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

          {showMeetingUrl && meetingUrl && (
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
    </div>
  );
}

export default function DashboardUpcomingMeetingsCard() {
  const [now, setNow] = useState<Date>(() => new Date());
  const [timeZone, setTimeZone] = useState(DEFAULT_TIME_ZONE);
  const [timeZoneReady, setTimeZoneReady] = useState(false);
  const [viewedMonth, setViewedMonth] = useState<Date>(() =>
    startOfMonth(toTimeZoneDate(new Date(), DEFAULT_TIME_ZONE)),
  );
  const [viewMode, setViewMode] = useState<CalendarViewMode>("month");
  const [selectedDay, setSelectedDay] = useState<Date | null>(() =>
    startOfDay(toTimeZoneDate(new Date(), DEFAULT_TIME_ZONE)),
  );
  const [summary, setSummary] = useState<CalendarDashboardSummary | null>(null);
  const [calendarLoading, setCalendarLoading] = useState(true);
  const [calendarRefreshing, setCalendarRefreshing] = useState(false);
  const [calendarError, setCalendarError] = useState<string | null>(null);
  const [initialisedView, setInitialisedView] = useState(false);

  useEffect(() => {
    let cancelled = false;

    void getUserTimeZone().then((resolvedTimeZone) => {
      if (!cancelled) {
        setTimeZone(resolvedTimeZone);
        setTimeZoneReady(true);
      }
    });

    return () => {
      cancelled = true;
    };
  }, []);

  const activeTimeZone = summary?.timezone || timeZone;
  const zonedNow = useMemo(() => toTimeZoneDate(now, activeTimeZone), [now, activeTimeZone]);
  const currentDay = useMemo(() => startOfDay(zonedNow), [zonedNow]);

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

  useEffect(() => {
    if (!timeZoneReady || initialisedView) {
      return;
    }

    setViewedMonth(startOfMonth(currentDay));
    setSelectedDay(currentDay);
    setInitialisedView(true);
  }, [currentDay, initialisedView, timeZoneReady]);

  const viewedMonthKey = format(viewedMonth, "yyyy-MM");

  useEffect(() => {
    if (!timeZoneReady) {
      return;
    }

    setSelectedDay((currentSelectedDay) => {
      if (currentSelectedDay && isSameMonth(currentSelectedDay, viewedMonth)) {
        return currentSelectedDay;
      }
      if (isSameMonth(viewedMonth, zonedNow)) {
        return currentDay;
      }
      return startOfMonth(viewedMonth);
    });
  }, [currentDay, timeZoneReady, viewedMonth, zonedNow]);

  useEffect(() => {
    if (!timeZoneReady) {
      return;
    }

    let active = true;

    const loadSummary = async (preserveContent = false) => {
      if (active) {
        if (preserveContent) {
          setCalendarRefreshing(true);
        } else {
          setCalendarLoading(true);
          setCalendarError(null);
        }
      }

      try {
        const response = await getCalendarDashboardSummary(viewedMonthKey, activeTimeZone);
        if (!active) {
          return;
        }
        setSummary(response);
      } catch {
        if (!active) {
          return;
        }
        if (!preserveContent) {
          setCalendarError("Unable to load calendar data.");
        }
      } finally {
        if (active) {
          if (preserveContent) {
            setCalendarRefreshing(false);
          } else {
            setCalendarLoading(false);
          }
        }
      }
    };

    void loadSummary();
    const interval = window.setInterval(() => {
      void loadSummary(true);
    }, 60000);

    return () => {
      active = false;
      window.clearInterval(interval);
    };
  }, [activeTimeZone, timeZoneReady, viewedMonthKey]);

  const monthDays = useMemo(
    () =>
      eachDayOfInterval({
        start: startOfWeek(startOfMonth(viewedMonth), { weekStartsOn: 1 }),
        end: endOfWeek(endOfMonth(viewedMonth), { weekStartsOn: 1 }),
      }),
    [viewedMonth],
  );
  const isViewingCurrentMonth = isSameMonth(viewedMonth, zonedNow);
  const viewedMonthLabel = format(viewedMonth, "MMMM yyyy");
  const monthEvents = useMemo(() => summary?.agenda_items ?? [], [summary]);
  const dayEventColours = useMemo(() => {
    const coloursByDay = new Map<string, string[]>();

    monthDays.forEach((day) => {
      const dayKey = format(day, "yyyy-MM-dd");
      const dayColours = getDayCalendarColours(monthEvents, day, activeTimeZone);

      if (dayColours.length) {
        coloursByDay.set(dayKey, dayColours);
      }
    });

    return coloursByDay;
  }, [activeTimeZone, monthDays, monthEvents]);
  const nextEventHelper = useMemo(
    () => buildNextEventHelper(summary, now, activeTimeZone),
    [activeTimeZone, now, summary],
  );
  const footerText = useMemo(
    () => buildFooterText(summary, viewedMonthLabel, calendarError),
    [summary, viewedMonthLabel, calendarError],
  );
  const futureAgendaItems = useMemo(
    () => monthEvents.filter((event) => isFutureOrLiveEvent(event, now, activeTimeZone)),
    [activeTimeZone, monthEvents, now],
  );
  const selectedDayEvents = useMemo(() => {
    if (!selectedDay) {
      return [];
    }
    return monthEvents.filter((event) => eventOccursOnDay(event, selectedDay, activeTimeZone));
  }, [activeTimeZone, monthEvents, selectedDay]);
  const selectedDayLabel = selectedDay ? format(selectedDay, "EEEE d MMMM") : null;
  const selectedDayState = useMemo<DayTimelineDayState | null>(() => {
    if (!selectedDay) {
      return null;
    }

    if (isSameDay(selectedDay, currentDay)) {
      return "today";
    }

    return selectedDay.getTime() < currentDay.getTime() ? "past" : "future";
  }, [currentDay, selectedDay]);
  const selectedDayTimeline = useMemo(
    () => buildDayTimeline(selectedDayEvents, selectedDay, now, activeTimeZone, selectedDayState),
    [activeTimeZone, now, selectedDay, selectedDayEvents, selectedDayState],
  );
  const mobileNowDividerIndex = useMemo(() => {
    if (selectedDayState !== "today" || !selectedDayTimeline?.timedEvents.length) {
      return null;
    }

    const firstLiveEventIndex = selectedDayTimeline.timedEvents.findIndex((event) => event.status === "live");
    if (firstLiveEventIndex >= 0) {
      return firstLiveEventIndex;
    }

    const firstUpcomingEventIndex = selectedDayTimeline.timedEvents.findIndex((event) => event.status === "upcoming");
    return firstUpcomingEventIndex >= 0
      ? firstUpcomingEventIndex
      : selectedDayTimeline.timedEvents.length;
  }, [selectedDayState, selectedDayTimeline]);
  const isViewingToday = Boolean(
    selectedDay && isViewingCurrentMonth && isSameDay(selectedDay, currentDay),
  );

  const handleJumpToToday = () => {
    const currentDate = new Date();
    const currentZonedDate = startOfDay(toTimeZoneDate(currentDate, activeTimeZone));
    setNow(currentDate);
    setViewedMonth(startOfMonth(currentZonedDate));
    setSelectedDay(currentZonedDate);
  };

  return (
    <div className="rounded-[2rem] border border-orange-100 bg-white p-6 shadow-xl shadow-orange-900/10 backdrop-blur dark:border-gray-700/70 dark:bg-gray-900/85 dark:shadow-black/30">
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

      <div className="mt-6 rounded-[1.75rem] border border-gray-200 bg-white p-4 shadow-inner shadow-orange-950/5 dark:border-gray-700/70 dark:bg-gray-800/70">
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
          <div className="mt-5 rounded-[1.5rem] border border-gray-200 bg-white p-4 dark:border-gray-700/70 dark:bg-gray-800/70">
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
                const dayColours = dayEventColours.get(format(day, "yyyy-MM-dd")) || [];
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
                  <AgendaEventCard key={event.id} event={event} timeZone={activeTimeZone} />
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
              {selectedDayEvents.length ? (
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
                            className="relative overflow-hidden rounded-[1.5rem] border border-gray-200 bg-white px-2 dark:border-gray-700/70 dark:bg-gray-800/80"
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
                  ) : (
                    <p className="text-sm text-gray-600 dark:text-gray-300">
                      No timed events on {format(selectedDay, "EEE d MMM")}.
                    </p>
                  )}
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
