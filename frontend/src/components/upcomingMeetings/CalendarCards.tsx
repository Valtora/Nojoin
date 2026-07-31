import Link from "next/link";
import { Popover, PopoverButton, PopoverPanel } from "@headlessui/react";
import {
  ArrowRight,
  Calendar,
  ChevronsDown,
  ChevronsUp,
  Clock,
  ExternalLink,
  MapPin,
  Mic,
  Users,
  Video,
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
  getTimelineMetadataRowCapacity,
  getTimelinePaddingClass,
  getTimelineTitleClass,
  getUrlHost,
} from "./calendarUtils";
import { EventDetailsPopoverContent } from "./EventDetailsPopover";

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
    <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-contrast-helper">
      <span className="inline-flex items-center rounded-full border border-surface-border bg-surface-inset px-2.5 py-1 font-medium text-contrast-muted">
        {recordings.length === 1 ? "Recording linked" : `${recordings.length} recordings linked`}
      </span>
      {singleRecording ? (
        <Link
          href={`/recordings/${singleRecording.id}`}
          className="inline-flex items-center gap-1 text-xs font-semibold text-contrast-muted transition-colors hover:text-foreground"
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
      className="group block rounded-[1.5rem] border border-action-border bg-surface-card px-4 py-4 shadow-card transition-colors hover:border-action-border hover:bg-action-tint"
    >
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-start gap-2">
            <div className="min-w-0 flex-1 text-base font-semibold text-foreground">
              <span className="line-clamp-2">{recording.name}</span>
            </div>
            {hasTags ? (
              <div className="flex flex-wrap items-center gap-1.5">
                {recording.tags.map((tag) => {
                  const colour = getColorByKey(tag.color || "orange");

                  return (
                    <span
                      key={tag.id}
                      className="inline-flex items-center rounded-full border border-action-border bg-action-tint px-2 py-0.5 text-[11px] font-semibold text-action-text"
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
            <div className="mt-3 inline-flex max-w-full items-start gap-2 text-sm text-contrast-helper">
              <Users className="mt-0.5 h-4 w-4 shrink-0 text-action-text" />
              <span className="line-clamp-2">{recording.speaker_names.join(", ")}</span>
            </div>
          ) : null}

          <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm text-contrast-helper">
            {showDate ? (
              <span className="inline-flex items-center gap-1.5">
                <Calendar className="h-4 w-4 text-action-text" />
                {formatTimeZoneDate(startedAt, timeZone, "EEE d MMM")}
              </span>
            ) : null}
            <span className="inline-flex items-center gap-1.5">
              <Clock className="h-4 w-4 text-action-text" />
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

        <span className="inline-flex shrink-0 items-center gap-1 text-sm font-semibold text-action-text transition-colors group-hover:text-action-text">
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
    <div className="relative overflow-hidden rounded-2xl border border-surface-border bg-surface-card px-4 py-3 shadow-card">
      <span
        className={`absolute inset-y-0 left-0 w-1.5 ${calendarColour.className}`}
        style={calendarColour.style}
      />
      <div className="pl-2">
        <div className="flex flex-wrap items-center gap-2 text-[11px] font-medium uppercase tracking-[0.14em] text-contrast-helper">
          <span>All day</span>
          <span>•</span>
          <span>{event.calendar_name}</span>
        </div>
        <div className="mt-1 line-clamp-2 text-sm font-semibold text-foreground">
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
  continuesBefore,
  continuesAfter,
}: {
  event: CalendarDashboardEvent;
  timeZone: string;
  status: DayTimelineStatus;
  layout: "timeline" | "stacked";
  visualHeight?: number;
  continuesBefore?: boolean;
  continuesAfter?: boolean;
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
  const hasLink = Boolean(
    (showMeetingUrl && meetingUrl) ||
      (showLocation && locationText && locationIsUrl),
  );
  const timelineDensity = layout === "timeline"
    ? visualHeight !== undefined && visualHeight < 28
      ? "dense"
      : visualHeight !== undefined && visualHeight < 52
        ? "compact"
        : "comfortable"
    : "comfortable";
  const isSmallTimelineEvent = layout === "timeline" && timelineDensity !== "comfortable";
  const metadataRowCapacity = layout === "stacked"
    ? 2
    : timelineDensity === "comfortable"
      ? getTimelineMetadataRowCapacity(visualHeight)
      : 0;
  const hasPlainLocation = Boolean(showLocation && locationText && !locationIsUrl);
  const showPlainLocation = hasPlainLocation && metadataRowCapacity >= 1;
  const showCalendarName =
    metadataRowCapacity >= 2 || (metadataRowCapacity >= 1 && !hasPlainLocation);
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
  const showJoinPill = Boolean(
    isLive &&
      showMeetingUrl &&
      meetingUrl &&
      (layout === "stacked" || timelineDensity === "comfortable"),
  );
  const cardClasses = `relative block h-full w-full cursor-pointer overflow-hidden rounded-[5px] border bg-surface-card text-left shadow-card transition-colors hover:border-action-border hover:bg-action-tint focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring ${
    isLive
      ? "border-action-border"
      : "border-surface-border"
  } ${
    isPast ? "opacity-70" : ""
  } ${
    layout === "timeline" && continuesBefore ? "[border-top-style:dashed]" : ""
  } ${
    layout === "timeline" && continuesAfter ? "[border-bottom-style:dashed]" : ""
  }`;
  const cardContent = (
    <>
      <span
        className={`absolute inset-y-0 left-0 w-1.5 ${calendarColour.className}`}
        style={calendarColour.style}
      />
      {layout === "timeline" && continuesBefore && (
        <ChevronsUp className="pointer-events-none absolute left-1/2 top-0 h-3 w-3 -translate-x-1/2 text-contrast-icon-muted" />
      )}
      {layout === "timeline" && continuesAfter && (
        <ChevronsDown className="pointer-events-none absolute bottom-0 left-1/2 h-3 w-3 -translate-x-1/2 text-contrast-icon-muted" />
      )}
      <div className={`h-full pl-4 pr-3 ${paddingClass}`}>
        <div className={`flex items-start justify-between gap-3 ${showJoinPill ? "pr-12" : ""}`}>
          <div className="min-w-0">
            {showTimeRow && (
              <div className="flex flex-wrap items-center gap-2 text-[10px] font-medium uppercase tracking-[0.14em] text-contrast-helper">
                <span>{formatAgendaTime(event, timeZone)}</span>
                {showLiveBadge && (
                  <span className="rounded-full bg-action-tint px-2 py-0.5 text-[10px] font-semibold tracking-[0.16em] text-action-text">
                    Live now
                  </span>
                )}
              </div>
            )}
            <div className={`font-semibold text-foreground ${titleClass}`}>
              {event.title}
            </div>
          </div>
          {!showJoinPill && (
            <div className="mt-1 flex shrink-0 items-center gap-1.5">
              {event.linked_recordings.length > 0 && (
                <span
                  title={
                    event.linked_recordings.length === 1
                      ? "1 linked recording"
                      : `${event.linked_recordings.length} linked recordings`
                  }
                >
                  <Mic className={`${linkIndicatorClass} text-action-tint-fg`} />
                </span>
              )}
              {hasLink && (
                <ExternalLink className={`${linkIndicatorClass} text-contrast-icon-muted`} />
              )}
              <span
                className={`${dotSizeClass} rounded-full ${calendarColour.className}`}
                style={calendarColour.style}
              />
            </div>
          )}
        </div>

        {(showCalendarName || showPlainLocation) && (
          <>
            {showCalendarName && (
              <div className="mt-2 text-xs text-contrast-helper">
                {event.calendar_name}
              </div>
            )}

            {showPlainLocation && locationText && (
              <div
                className="mt-2 flex min-w-0 items-center gap-2 text-xs text-contrast-helper"
                title={locationText}
              >
                <MapPin className="h-3.5 w-3.5 shrink-0 text-action-text" />
                <span className="truncate">{locationText}</span>
              </div>
            )}

          </>
        )}
      </div>
    </>
  );

  return (
    <Popover className="relative block h-full">
      <PopoverButton
        className={cardClasses}
        aria-label={`View details for ${event.title}`}
      >
        {cardContent}
      </PopoverButton>
      {showJoinPill && meetingUrl && (
        <a
          href={meetingUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="absolute right-2 top-2 z-20 inline-flex items-center gap-1 rounded-full bg-action px-2.5 py-0.5 text-[10px] font-semibold text-foreground shadow-card transition-colors hover:bg-action"
        >
          <Video className="h-3 w-3" />
          Join
        </a>
      )}
      <PopoverPanel
        anchor={{
          to: layout === "timeline" ? "right start" : "bottom",
          gap: 8,
          padding: 12,
        }}
        className="z-50 focus:outline-none"
      >
        <EventDetailsPopoverContent
          event={event}
          timeZone={timeZone}
          status={status}
        />
      </PopoverPanel>
    </Popover>
  );
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
  const meetingHost = event.meeting_url_host || getUrlHost(meetingUrl);
  const locationHost = getUrlHost(locationText);

  return (
    <div className="rounded-xl border border-surface-border bg-surface-card p-4">
      <div className="flex flex-wrap items-center gap-2 text-xs font-medium uppercase tracking-[0.12em] text-contrast-helper">
        <span>{formatAgendaDate(event, timeZone)}</span>
        <span>•</span>
        <span>{formatAgendaTime(event, timeZone)}</span>
      </div>
      <div className="mt-2 text-base font-semibold text-foreground">
        {event.title}
      </div>
      <div className="mt-1 inline-flex items-center gap-2 text-sm text-contrast-helper">
        <span
          className={`h-2.5 w-2.5 rounded-full ${calendarColour.className}`}
          style={calendarColour.style}
        />
        {event.calendar_name}
      </div>
      {(showLocation || showMeetingUrl) && (
        <div className="mt-3 space-y-2 text-sm text-contrast-helper">
          {showLocation && locationText && (
            locationIsUrl ? (
              <a
                href={locationText}
                target="_blank"
                rel="noopener noreferrer"
                title={locationText}
                className="inline-flex items-start gap-2 text-contrast-helper transition-colors hover:text-foreground hover:underline"
              >
                <ExternalLink className="mt-0.5 h-4 w-4 shrink-0 text-contrast-icon-muted" />
                <span className="min-w-0 break-words">
                  Open link{locationHost ? ` (${locationHost})` : ""}
                </span>
              </a>
            ) : (
              <div className="inline-flex items-start gap-2">
                <MapPin className="mt-0.5 h-4 w-4 shrink-0 text-action-text" />
                <span>{locationText}</span>
              </div>
            )
          )}

          {showMeetingUrl && meetingUrl && (
            <a
              href={meetingUrl}
              target="_blank"
              rel="noopener noreferrer"
              title={meetingUrl}
              className="inline-flex items-start gap-2 text-contrast-helper transition-colors hover:text-foreground hover:underline"
            >
              <ExternalLink className="mt-0.5 h-4 w-4 shrink-0 text-contrast-icon-muted" />
              <span className="min-w-0 break-words">
                Join meeting{meetingHost ? ` (${meetingHost})` : ""}
              </span>
            </a>
          )}
        </div>
      )}
      <LinkedRecordingsMeta recordings={event.linked_recordings} />
    </div>
  );
}
