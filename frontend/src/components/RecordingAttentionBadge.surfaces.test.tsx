import type { ComponentPropsWithoutRef, ReactElement } from "react";
import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  RecordingStatus,
  type CalendarDashboardEvent,
  type CalendarDashboardRecording,
  type Recording,
} from "@/types";

let attentionRecordings: Recording[] = [];

vi.mock("@/lib/capture/CaptureProvider", () => ({
  useCapture: () => ({ attentionRecordings }),
}));
vi.mock("next/link", () => ({
  default: ({ children, ...props }: ComponentPropsWithoutRef<"a">) => <a {...props}>{children}</a>,
}));

import RecordingAttentionBadge from "./RecordingAttentionBadge";
import RecentRecordingsCard from "./dashboardRecordings/RecentRecordingsCard";
import ProcessingCard from "./dashboardRecordings/ProcessingCard";
import {
  AgendaEventCard,
  DashboardRecordingCard,
  DayTimelineAllDayChip,
} from "./upcomingMeetings/CalendarCards";
import { EventDetailsPopoverContent } from "./upcomingMeetings/EventDetailsPopover";

const recording = (status: RecordingStatus): Recording => ({
  id: "recoverable",
  name: "Planning meeting",
  status,
  created_at: "2026-10-27T09:00:00Z",
  updated_at: "2026-10-27T09:00:00Z",
  meeting_uid: "planning",
  audio_path: "/audio.wav",
  is_archived: false,
  is_deleted: false,
  capture_stalled: status === RecordingStatus.UPLOADING,
});

const calendarRecording = (source: Recording): CalendarDashboardRecording => ({
  id: source.id,
  name: source.name,
  status: source.status,
  starts_at: source.created_at,
  speaker_names: [],
  tags: [],
});

const event = (source: Recording): CalendarDashboardEvent => ({
  id: 1,
  title: "Calendar event",
  provider: "google",
  calendar_id: 1,
  calendar_name: "Work",
  is_all_day: false,
  starts_at: "2026-10-27T09:00:00Z",
  meeting_url_trusted: false,
  linked_recordings: [calendarRecording(source)],
});

const surfaces: [string, (source: Recording) => ReactElement][] = [
  ["recent recordings", (source) => <RecentRecordingsCard recordings={[source]} timeZone="UTC" />],
  ["processing", (source) => <ProcessingCard recordings={[source]} />],
  ["selected-day recording", (source) => (
    <DashboardRecordingCard recording={calendarRecording(source)} timeZone="UTC" showDate={false} />
  )],
  ["month-agenda recording", (source) => (
    <DashboardRecordingCard recording={calendarRecording(source)} timeZone="UTC" showDate />
  )],
  ["all-day event linked recording", (source) => <DayTimelineAllDayChip event={{ ...event(source), is_all_day: true }} />],
  ["month-agenda event linked recording", (source) => <AgendaEventCard event={event(source)} timeZone="UTC" />],
  ["event popover linked recording", (source) => <EventDetailsPopoverContent event={event(source)} timeZone="UTC" />],
];

describe.each(surfaces)("recording attention on %s", (_name, surface) => {
  beforeEach(() => {
    attentionRecordings = [];
  });

  it.each([
    [RecordingStatus.PAUSED, "Paused, needs attention"],
    [RecordingStatus.UPLOADING, "Interrupted, needs attention"],
  ] as const)("marks eligible %s and removes the badge when browser ownership suppresses it", (status, label) => {
    const source = recording(status);
    attentionRecordings = [source];
    const { rerender } = render(surface(source));
    const link = screen.getByRole("link");
    expect(link).toHaveAttribute("href", "/recordings/recoverable");
    const badge = within(link).getByText(label);
    expect(badge).toBeVisible();
    expect(badge).toHaveAttribute("data-ui-tone", "warning");
    expect(badge.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    expect(within(link).queryByText(new RegExp(`^${status}$`, "i"))).not.toBeInTheDocument();

    // The list's status remains unchanged; browser ownership affects only the
    // controller's central eligibility result, which must remove the badge.
    attentionRecordings = [];
    rerender(surface(source));
    expect(within(link).queryByText(/needs attention/)).not.toBeInTheDocument();
  });
});

describe("recording attention identity", () => {
  beforeEach(() => {
    attentionRecordings = [];
  });

  it("does not badge an unrelated recording with the same paused status", () => {
    attentionRecordings = [{ ...recording(RecordingStatus.PAUSED), id: "other-recording" }];
    render(<RecentRecordingsCard recordings={[recording(RecordingStatus.PAUSED)]} timeZone="UTC" />);
    const link = screen.getByRole("link");
    expect(within(link).queryByText(/needs attention/)).not.toBeInTheDocument();
    expect(within(link).getByText("Paused")).toBeVisible();
  });

  it("reads a reloaded tab's own capture as paused, as the dialog it opens does (issue #789)", () => {
    // Offered at once after a reload, before the server judges it stalled.
    const reloaded = { ...recording(RecordingStatus.UPLOADING), capture_stalled: false };
    attentionRecordings = [reloaded];
    render(<RecordingAttentionBadge recordingId="recoverable" />);
    expect(screen.getByText("Paused, needs attention")).toBeVisible();
    expect(screen.queryByText(/Interrupted/)).not.toBeInTheDocument();
  });

  it("renders no badge or placeholder when the recording needs no recovery", () => {
    const { container } = render(<RecordingAttentionBadge recordingId="recoverable" />);
    expect(container).toBeEmptyDOMElement();
  });

  it("independently marks eligible recordings among multiple calendar event links", () => {
    const source = recording(RecordingStatus.PAUSED);
    attentionRecordings = [source];
    const linkedEvent = event(source);
    linkedEvent.linked_recordings.push({ ...calendarRecording(source), id: "held", name: "Held recording" });
    render(<EventDetailsPopoverContent event={linkedEvent} timeZone="UTC" />);
    expect(within(screen.getByRole("link", { name: /Planning meeting/ }))
      .getByText("Paused, needs attention")).toBeVisible();
    expect(within(screen.getByRole("link", { name: /Held recording/ }))
      .queryByText(/needs attention/)).not.toBeInTheDocument();
  });
});
