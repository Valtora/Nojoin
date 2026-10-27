import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, renderHook, screen, waitFor, within } from "@testing-library/react";

import { withProcessTimeZone } from "@/test/processTimeZone";
import { RecordingStatus, type Recording } from "@/types";
import LinkedEventPanel from "./LinkedEventPanel";
import RecordingInfoModal from "./RecordingInfoModal";
import RecentRecordingsCard from "./dashboardRecordings/RecentRecordingsCard";
import { useDashboardRecordings } from "./dashboardRecordings/useDashboardRecordings";

const api = vi.hoisted(() => ({
  getRecordings: vi.fn(),
  getRecordingInfo: vi.fn(),
  getRecordingCalendarEventCandidates: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  ...api,
  linkRecordingCalendarEvent: vi.fn(),
}));

vi.mock("./ui/Modal", () => ({
  default: ({ open, children }: { open: boolean; children: ReactNode }) =>
    open ? <div role="dialog">{children}</div> : null,
}));

const recording = (id: string, created_at: string): Recording => ({
  id,
  created_at,
  updated_at: created_at,
  name: `Meeting ${id}`,
  meeting_uid: `meeting-${id}`,
  audio_path: "/audio.wav",
  duration_seconds: 600,
  status: RecordingStatus.PROCESSED,
  is_archived: false,
  is_deleted: false,
});

const event = { id: 1, title: "Project meeting", starts_at: "2026-10-27T02:30:00", ends_at: null };
const eventInstant = new Date("2026-10-27T02:30:00Z");
const eventLabel = () =>
  `${eventInstant.toLocaleDateString(undefined, { month: "short", day: "numeric" })}, ` +
  eventInstant.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });

describe.each(["America/New_York", "Asia/Tokyo"])("recording server timestamps in %s", (timeZone) => {
  withProcessTimeZone(timeZone);

  beforeEach(() => {
    vi.clearAllMocks();
    api.getRecordingInfo.mockResolvedValue({ proxy: null });
    api.getRecordingCalendarEventCandidates.mockResolvedValue([event]);
  });

  it("shows the recording info clock at the UTC instant returned without a zone", async () => {
    render(<RecordingInfoModal isOpen onClose={vi.fn()} recording={recording("info", event.starts_at)} />);
    expect(await screen.findByText(eventInstant.toLocaleString())).toBeInTheDocument();
  });

  it("shows the linked calendar event at its UTC instant", () => {
    render(<LinkedEventPanel recordingId="linked" linkedEvent={event} />);
    expect(screen.getByText(eventLabel())).toBeInTheDocument();
  });

  it("shows candidate calendar event clocks at their UTC instants", async () => {
    render(<LinkedEventPanel recordingId="candidate" />);
    fireEvent.click(screen.getByRole("button", { name: "Link calendar event" }));
    const candidate = await screen.findByRole("button", { name: /Project meeting/ });
    expect(within(candidate).getByText(eventLabel())).toBeInTheDocument();
  });

  it.each([
    ["America/New_York", "2026-10-27T02:30:00", "26 Oct"],
    ["Asia/Tokyo", "2026-10-27T20:30:00", "28 Oct"],
  ])("keeps Recent Meetings on the correct day in %s", (timeZone, value, expected) => {
    render(<RecentRecordingsCard recordings={[recording("recent", value)]} timeZone={timeZone} />);
    expect(screen.getByText(`${expected} · 10m`)).toBeInTheDocument();
  });

  it("sorts mixed naive and aware server timestamps by their UTC instants", async () => {
    api.getRecordings.mockResolvedValue([
      recording("naive", "2026-10-27T12:00:00"),
      recording("earlier", "2026-10-27T11:00:00Z"),
      recording("later", "2026-10-27T13:00:00+00:00"),
    ]);
    const { result } = renderHook(() => useDashboardRecordings());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.recent.map((item) => item.id)).toEqual(["later", "naive", "earlier"]);
  });
});
