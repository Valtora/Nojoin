import Link from "next/link";
import {
  ArrowRight,
  Calendar,
  Clock,
  ExternalLink,
  MapPin,
  Users,
} from "lucide-react";

import { getColorByKey } from "@/lib/constants";
import { formatTimeZoneDate } from "@/lib/timezone";
import {
  CalendarDashboardEvent,
  CalendarDashboardRecording,
  RecordingStatus,
} from "@/types";

import {
  DayTimelineStatus,
  formatAgendaDate,
  formatAgendaTime,
  formatRecordingDuration,
  formatRecordingTime,
  getAgendaEventPresentation,
  getCalendarColourPresentation,
  getRecordingStart,
  getRecordingStatusClasses,
  getTimelineDotSizeClass,
  getTimelineIndicatorSizeClass,
  getTimelinePaddingClass,
  getTimelineTitleClass,
} from "./calendarUtils";

export function LinkedRecordingsMeta({
  recordings,
}: {
  recordings: CalendarDashboardRecording[];
}) {
  if (!recordings.length) {
    return null;
  }

  const singleRecording = recordings.length === 1 ? recordings[0] : null;

  return (
    <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-gray-600 dark:text-gray-300">
      <span className="inline-flex items-center rounded-full border border-gray-200 bg-gray-100 px-2.5 py-1 font-medium text-gray-700 dark:border-gray-600 dark:bg-gray-700/60 dark:text-gray-200">
        {recordings.length === 1 ? "Recording linked" : `${recordings.length} recordings linked`}
      </span>
      {singleRecording ? (
        <Link
          href={`/recordings/${singleRecording.id}`}
          className="inline-flex items-center gap-1 text-xs font-semibold text-gray-700 transition-colors hover:text-gray-950 dark:text-gray-200 dark:hover:text-white"
        >
          Open recording
          <ArrowRight className="h-3 w-3" />
        </Link>
      ) : null}
    </div>
  );
}

export function DashboardRecordingCard({
  recording,
  timeZone,
  showDate,
}: {
  recording: CalendarDashboardRecording;
  timeZone: string;
  showDate: boolean;
}) {
  const startedAt = getRecordingStart(recording);
  const showStatus = recording.status !== RecordingStatus.PROCESSED;
  const hasTags = recording.tags.length > 0;
  const hasSpeakers = recording.speaker_names.length > 0;

  return (
    <Link
      href={`/recordings/${recording.id}`}
      className="group block rounded-[1.5rem] border border-orange-200/80 bg-white px-4 py-4 shadow-sm shadow-orange-950/5 transition-colors hover:border-orange-300 hover:bg-orange-50/40 dark:border-orange-500/20 dark:bg-gray-800/70 dark:hover:border-orange-400/30 dark:hover:bg-orange-500/10"
    >
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-start gap-2">
            <div className="min-w-0 flex-1 text-base font-semibold text-gray-950 dark:text-white">
              <span className="line-clamp-2">{recording.name}</span>
            </div>
            {hasTags ? (
              <div className="flex flex-wrap items-center gap-1.5">
                {recording.tags.map((tag) => {
                  const colour = getColorByKey(tag.color || "orange");

                  return (
                    <span
                      key={tag.id}
                      className="inline-flex items-center rounded-full border border-orange-200 bg-orange-50 px-2 py-0.5 text-[11px] font-semibold text-orange-900 dark:border-orange-500/20 dark:bg-orange-500/10 dark:text-orange-200"
                    >
                      <span
                        className={`mr-1.5 h-1.5 w-1.5 rounded-full ${colour.dot}`}
                      />
                      {tag.name}
                    </span>
                  );
                })}
              </div>
            ) : null}
          </div>

          {hasSpeakers ? (
            <div className="mt-3 inline-flex max-w-full items-start gap-2 text-sm text-gray-600 dark:text-gray-300">
              <Users className="mt-0.5 h-4 w-4 shrink-0 text-orange-600 dark:text-orange-300" />
              <span className="line-clamp-2">{recording.speaker_names.join(", ")}</span>
            </div>
          ) : null}

          <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm text-gray-600 dark:text-gray-300">
            {showDate ? (
              <span className="inline-flex items-center gap-1.5">
                <Calendar className="h-4 w-4 text-orange-600 dark:text-orange-300" />
                {formatTimeZoneDate(startedAt, timeZone, "EEE d MMM")}
              </span>
            ) : null}
            <span className="inline-flex items-center gap-1.5">
              <Clock className="h-4 w-4 text-orange-600 dark:text-orange-300" />
              {formatRecordingTime(recording, timeZone)}
            </span>
            <span>{formatRecordingDuration(recording.duration_seconds)}</span>
            {showStatus ? (
              <span className={`inline-flex rounded-full border px-2.5 py-1 text-xs font-semibold ${getRecordingStatusClasses(recording.status)}`}>
                {recording.status}
              </span>
            ) : null}
          </div>
        </div>

        <span className="inline-flex shrink-0 items-center gap-1 text-sm font-semibold text-orange-700 transition-colors group-hover:text-orange-800 dark:text-orange-300 dark:group-hover:text-orange-200">
          Open
          <ArrowRight className="h-4 w-4" />
        </span>
      </div>
    </Link>
  );
}

export function DayTimelineAllDayChip({
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
        <LinkedRecordingsMeta recordings={event.linked_recordings} />
      </div>
    </div>
  );
}

export function DayTimelineEventCard({
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

            {!isSmallTimelineEvent ? (
              <LinkedRecordingsMeta recordings={event.linked_recordings} />
            ) : null}
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

export function AgendaEventCard({
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
      <LinkedRecordingsMeta recordings={event.linked_recordings} />
    </div>
  );
}
