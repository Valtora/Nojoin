import {
  addDays,
  addMinutes,
  differenceInMinutes,
  endOfDay,
  format,
  isSameDay,
  parseISO,
  startOfDay,
} from "date-fns";

import { COLOR_PALETTE } from "@/lib/constants";
import { formatTimeZoneDate, toTimeZoneDate } from "@/lib/timezone";
import {
  CalendarDashboardEvent,
  CalendarDashboardRecording,
  CalendarDashboardSummary,
  RecordingStatus,
} from "@/types";

export const WEEK_DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
export type CalendarViewMode = "month" | "agenda";
export const MAX_VISIBLE_DOTS = 4;
export const DEFAULT_TIMELINE_START_HOUR = 7;
export const DEFAULT_TIMELINE_END_HOUR = 21;
export const TIMELINE_HOUR_HEIGHT = 44;
export const MIN_TIMELINE_EVENT_HEIGHT = 16;

export type DayTimelineStatus = "past" | "live" | "upcoming";
export type DayTimelineDayState = "past" | "today" | "future";

export interface DayTimelineDraftEvent {
  event: CalendarDashboardEvent;
  startMinutes: number;
  endMinutes: number;
  status: DayTimelineStatus;
  continuesBefore: boolean;
  continuesAfter: boolean;
}

export interface DayTimelineEvent extends DayTimelineDraftEvent {
  column: number;
  columns: number;
  top: number;
  height: number;
}

export interface DayTimelineData {
  allDayEvents: CalendarDashboardEvent[];
  timedEvents: DayTimelineEvent[];
  startHour: number;
  endHour: number;
  height: number;
  nowOffset: number | null;
  nowLabel: string | null;
}

export type MonthAgendaItem =
  | {
      kind: "event";
      sortDate: Date;
      event: CalendarDashboardEvent;
    }
  | {
      kind: "recording";
      sortDate: Date;
      recording: CalendarDashboardRecording;
    };

export function getCalendarColourPresentation(colour: string | null | undefined) {
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

export function getDayMarkerColours(
  events: CalendarDashboardEvent[],
  recordings: CalendarDashboardRecording[],
  day: Date,
  timeZone: string,
): string[] {
  const coloursBySource = new Map<string, string>();

  recordings.forEach((recording) => {
    if (!recordingOccursOnDay(recording, day, timeZone)) {
      return;
    }

    coloursBySource.set("nojoin-recording", "orange");
  });

  events.forEach((event) => {
    if (!eventOccursOnDay(event, day, timeZone)) {
      return;
    }

    const sourceKey = `calendar-${event.calendar_id}`;
    if (!coloursBySource.has(sourceKey)) {
      coloursBySource.set(sourceKey, event.calendar_colour || "gray");
    }
  });

  return Array.from(coloursBySource.values());
}

export function getEventStart(event: CalendarDashboardEvent): Date | null {
  if (event.is_all_day && event.start_date) {
    return parseISO(event.start_date);
  }
  if (event.starts_at) {
    return parseISO(event.starts_at);
  }
  return null;
}

export function getEventEnd(event: CalendarDashboardEvent): Date | null {
  if (event.is_all_day && event.end_date) {
    return addDays(parseISO(event.end_date), -1);
  }
  if (event.ends_at) {
    return parseISO(event.ends_at);
  }
  return getEventStart(event);
}

export function getRecordingStart(recording: CalendarDashboardRecording): Date {
  return parseISO(recording.starts_at);
}

export function getRecordingEnd(
  recording: CalendarDashboardRecording,
): Date | null {
  if (!recording.ends_at) {
    return null;
  }
  return parseISO(recording.ends_at);
}

export function getTimelineEventEnd(
  event: CalendarDashboardEvent,
): Date | null {
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

export function eventOccursOnDay(
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

export function recordingOccursOnDay(
  recording: CalendarDashboardRecording,
  day: Date,
  timeZone: string,
): boolean {
  const recordingStart = getRecordingStart(recording);
  return isSameDay(toTimeZoneDate(recordingStart, timeZone), day);
}

export function formatRecordingTime(
  recording: CalendarDashboardRecording,
  timeZone: string,
): string {
  const startsAt = getRecordingStart(recording);
  const endsAt = getRecordingEnd(recording);
  if (!endsAt) {
    return formatTimeZoneDate(startsAt, timeZone, "HH:mm");
  }
  return `${formatTimeZoneDate(startsAt, timeZone, "HH:mm")} - ${formatTimeZoneDate(endsAt, timeZone, "HH:mm")}`;
}

export function formatRecordingDuration(
  durationSeconds: number | null | undefined,
): string {
  if (!durationSeconds || durationSeconds <= 0) {
    return "Duration unavailable";
  }

  const totalMinutes = Math.max(1, Math.round(durationSeconds / 60));
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;

  if (hours > 0 && minutes > 0) {
    return `${hours}h ${minutes}m`;
  }
  if (hours > 0) {
    return `${hours}h`;
  }
  return `${minutes}m`;
}

export function getRecordingStatusClasses(status: RecordingStatus): string {
  switch (status) {
    case RecordingStatus.ERROR:
      return "border-red-200 bg-red-50 text-red-700 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-200";
    case RecordingStatus.CANCELLED:
      return "border-gray-200 bg-gray-100 text-gray-700 dark:border-gray-600 dark:bg-gray-700/60 dark:text-gray-200";
    default:
      return "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200";
  }
}

export function buildNextEventHelper(
  summary: CalendarDashboardSummary | null,
  now: Date,
  timeZone: string,
): string | null {
  if (!summary?.next_event && summary?.recording_items.length) {
    return "Meeting history available";
  }

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

export function buildFooterText(
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

export function buildMonthAgendaItems(
  events: CalendarDashboardEvent[],
  recordings: CalendarDashboardRecording[],
): MonthAgendaItem[] {
  return [
    ...events.map((event) => ({
      kind: "event" as const,
      sortDate: getEventStart(event) ?? new Date(8640000000000000),
      event,
    })),
    ...recordings.map((recording) => ({
      kind: "recording" as const,
      sortDate: getRecordingStart(recording),
      recording,
    })),
  ].sort((leftItem, rightItem) => {
    const leftTime = leftItem.sortDate.getTime();
    const rightTime = rightItem.sortDate.getTime();

    if (leftTime !== rightTime) {
      return leftTime - rightTime;
    }

    if (leftItem.kind !== rightItem.kind) {
      return leftItem.kind === "event" ? -1 : 1;
    }

    const leftLabel = leftItem.kind === "event"
      ? leftItem.event.title
      : leftItem.recording.name;
    const rightLabel = rightItem.kind === "event"
      ? rightItem.event.title
      : rightItem.recording.name;
    return leftLabel.localeCompare(rightLabel);
  });
}

export function formatAgendaDate(
  event: CalendarDashboardEvent,
  timeZone: string,
): string {
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

export function formatAgendaTime(
  event: CalendarDashboardEvent,
  timeZone: string,
): string {
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

export function isHttpUrl(value: string | null | undefined): boolean {
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

export function normaliseComparableUrl(
  value: string | null | undefined,
): string | null {
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

export function getAgendaEventPresentation(event: CalendarDashboardEvent) {
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

export function formatHourLabel(hour: number): string {
  return `${String(hour).padStart(2, "0")}:00`;
}

export function getTimelineTitleClass(
  visualHeight: number | undefined,
  showTimeRow: boolean,
): string {
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

export function getTimelinePaddingClass(
  visualHeight: number | undefined,
  isSmallTimelineEvent: boolean,
): string {
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

export function getTimelineIndicatorSizeClass(
  visualHeight: number | undefined,
): string {
  if (visualHeight !== undefined && visualHeight < 24) {
    return "h-3 w-3";
  }

  return "h-3.5 w-3.5";
}

export function getTimelineDotSizeClass(
  visualHeight: number | undefined,
): string {
  if (visualHeight !== undefined && visualHeight < 24) {
    return "h-2 w-2";
  }

  return "h-2.5 w-2.5";
}

export function layoutDayTimelineEvents(
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

export function buildDayTimeline(
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
