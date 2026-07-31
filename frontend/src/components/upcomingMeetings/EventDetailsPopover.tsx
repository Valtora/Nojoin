import Link from "next/link";
import { ArrowRight, ExternalLink, MapPin, Video } from "lucide-react";

import { CalendarDashboardEvent } from "@/types";

import {
  DayTimelineStatus,
  formatAgendaDate,
  formatAgendaTime,
  getAgendaEventPresentation,
  getCalendarColourPresentation,
  getUrlHost,
} from "./calendarUtils";

export function EventDetailsPopoverContent({
  event,
  timeZone,
  status,
}: {
  event: CalendarDashboardEvent;
  timeZone: string;
  status?: DayTimelineStatus;
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
    <div className="w-72 rounded-surface-panel border border-surface-float-border bg-surface-float p-4 text-left shadow-float sm:w-80">
      <div className="flex items-start justify-between gap-3">
        <div className="text-sm font-semibold text-foreground">
          {event.title}
        </div>
        {status === "live" && (
          <span className="shrink-0 rounded-full bg-action-tint px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.16em] text-action-text">
            Live now
          </span>
        )}
      </div>

      <div className="mt-2 text-xs font-medium uppercase tracking-[0.12em] text-contrast-helper">
        {formatAgendaDate(event, timeZone)} • {formatAgendaTime(event, timeZone)}
      </div>

      <div className="mt-2 flex items-center gap-2 text-xs text-contrast-helper">
        <span
          className={`h-2 w-2 shrink-0 rounded-full ${calendarColour.className}`}
          style={calendarColour.style}
        />
        <span className="min-w-0 truncate">{event.calendar_name}</span>
      </div>

      {showLocation && locationText && (
        locationIsUrl ? (
          <a
            href={locationText}
            target="_blank"
            rel="noopener noreferrer"
            title={locationText}
            className="mt-3 flex items-start gap-2 text-xs text-contrast-helper transition-colors hover:text-foreground hover:underline"
          >
            <ExternalLink className="h-3.5 w-3.5 shrink-0 text-contrast-icon-muted" />
            <span className="min-w-0 break-words">
              Open link{locationHost ? ` (${locationHost})` : ""}
            </span>
          </a>
        ) : (
          <div className="mt-3 flex items-start gap-2 text-xs text-contrast-helper">
            <MapPin className="h-3.5 w-3.5 shrink-0 text-action-text" />
            <span className="min-w-0 break-words">{locationText}</span>
          </div>
        )
      )}

      {showMeetingUrl && meetingUrl && (
        <a
          href={meetingUrl}
          target="_blank"
          rel="noopener noreferrer"
          title={meetingUrl}
          className="mt-3 inline-flex items-center gap-2 rounded-full bg-action px-3.5 py-1.5 text-xs font-semibold text-foreground transition-colors hover:bg-action"
        >
          <Video className="h-3.5 w-3.5" />
          Join meeting{meetingHost ? ` (${meetingHost})` : ""}
        </a>
      )}

      {event.linked_recordings.length > 0 && (
        <div className="mt-3 border-t border-surface-border pt-3">
          <div className="text-[10px] font-semibold uppercase tracking-[0.16em] text-contrast-helper">
            Linked recordings
          </div>
          <div className="mt-2 space-y-1.5">
            {event.linked_recordings.map((recording) => (
              <Link
                key={recording.id}
                href={`/recordings/${recording.id}`}
                className="flex items-center gap-1.5 text-xs font-medium text-contrast-muted transition-colors hover:text-action-text"
              >
                <ArrowRight className="h-3 w-3 shrink-0" />
                <span className="min-w-0 truncate">{recording.name}</span>
              </Link>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
