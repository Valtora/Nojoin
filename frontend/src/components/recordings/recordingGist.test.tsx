import { createRef, type ComponentProps } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { RecordingStatus, type CalendarDashboardEvent, type CalendarDashboardRecording, type Recording } from "@/types";
import RecordingHeader from "@/app/(dashboard)/recordings/[id]/_components/RecordingHeader";
import { AgendaEventCard, DashboardRecordingCard, DayTimelineEventCard } from "../upcomingMeetings/CalendarCards";
import { EventDetailsPopoverContent } from "../upcomingMeetings/EventDetailsPopover";
import RecentRecordingsCard from "../dashboardRecordings/RecentRecordingsCard";

vi.mock("@/components/RecordingTagEditor", () => ({ default: () => null }));
vi.mock("@/components/LinkedEventPanel", () => ({ default: () => null }));

const recording: Recording = {
  id: "rec-1",
  name: "Launch planning",
  gist: "Deciding the launch date.",
  meeting_uid: "meeting-1",
  audio_path: "/audio.wav",
  status: RecordingStatus.PROCESSED,
  created_at: "2026-10-29T10:00:00Z",
  updated_at: "2026-10-29T10:00:00Z",
  is_archived: false,
  is_deleted: false,
};

const calendarRecording: CalendarDashboardRecording = {
  ...recording,
  starts_at: recording.created_at,
  speaker_names: [],
  tags: [],
};

const linkedEvent: CalendarDashboardEvent = {
  id: 1,
  title: "Launch calendar event",
  provider: "google",
  calendar_id: 1,
  calendar_name: "Work",
  meeting_url_trusted: false,
  is_all_day: false,
  starts_at: recording.created_at,
  ends_at: "2026-10-29T11:00:00Z",
  linked_recordings: [calendarRecording],
};

describe("recording gist surfaces", () => {
  it("keeps linked meeting gist inline in the month agenda", () => {
    render(<AgendaEventCard event={linkedEvent} timeZone="UTC" />);
    expect(screen.getByText("Deciding the launch date.")).toBeInTheDocument();
  });

  it("keeps tiny timeline bubbles compact while offering gist in the tooltip and details popup", () => {
    const { rerender } = render(
      <DayTimelineEventCard event={linkedEvent} timeZone="UTC" layout="timeline" status="past" visualHeight={20} />,
    );
    expect(screen.getByRole("button")).toHaveAttribute("title", "Deciding the launch date.");
    expect(screen.queryByText("Deciding the launch date.")).not.toBeInTheDocument();
    rerender(<EventDetailsPopoverContent event={linkedEvent} timeZone="UTC" />);
    expect(screen.getByText("Deciding the launch date.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Launch planning/ })).toHaveAttribute("href", "/recordings/rec-1");
  });

  it.each([false, true])("shows inline agenda gist with showDate=%s and updates the live label", (showDate) => {
    const live = { ...calendarRecording, gist_live: true };
    const { rerender } = render(<DashboardRecordingCard recording={live} timeZone="UTC" showDate={showDate} />);
    expect(screen.getByText("Summary of transcript so far: Deciding the launch date.")).toBeInTheDocument();
    expect(screen.getByRole("link")).toHaveAttribute("title", "Summary of transcript so far: Deciding the launch date.");
    rerender(<DashboardRecordingCard recording={calendarRecording} timeZone="UTC" showDate={showDate} />);
    expect(screen.getByText("Deciding the launch date.")).toBeInTheDocument();
    expect(screen.queryByText(/Summary of transcript so far/)).not.toBeInTheDocument();
  });

  it("keeps recent links compact and omits tooltips when no gist exists", () => {
    const { rerender } = render(<RecentRecordingsCard recordings={[recording]} timeZone="UTC" />);
    expect(screen.getByRole("link")).toHaveAttribute("title", recording.gist);
    expect(screen.queryByText(recording.gist!)).not.toBeInTheDocument();
    rerender(<RecentRecordingsCard recordings={[{ ...recording, gist: null }]} timeZone="UTC" />);
    expect(screen.getByRole("link")).not.toHaveAttribute("title");
  });

  it.each([false, true])("keeps header rename hint and action on mobile=%s", (isMobile) => {
    const startEditing = vi.fn();
    const props: ComponentProps<typeof RecordingHeader> = {
      recording,
      playerSlot: <div />,
      isMobile,
      isEditingTitle: false,
      titleValue: recording.name,
      isMobileHeaderActionsOpen: false,
      currentTime: 0,
      audioRef: createRef<HTMLAudioElement>(),
      onRefresh: vi.fn(),
      setTitleValue: vi.fn(),
      setIsEditingTitle: startEditing,
      setIsMobileHeaderActionsOpen: vi.fn(),
      onBack: vi.fn(),
      onTitleSubmit: vi.fn(),
      onTimeUpdate: vi.fn(),
      onPlay: vi.fn(),
      onPause: vi.fn(),
    };
    const { rerender } = render(<RecordingHeader {...props} />);
    const heading = screen.getByRole("heading", { level: 1 });
    expect(heading).toHaveAttribute("title", "Deciding the launch date.\n\nClick to rename");
    fireEvent.click(heading);
    expect(startEditing).toHaveBeenCalledWith(true);
    rerender(<RecordingHeader {...props} recording={{ ...recording, gist: null }} />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveAttribute("title", "Click to rename");
  });
});
