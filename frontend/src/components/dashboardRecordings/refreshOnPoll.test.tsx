import type { ComponentPropsWithoutRef } from "react";
import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CaptureProvider } from "@/lib/capture/CaptureProvider";
import {
  ClientStatus,
  RecordingStatus,
  type CalendarDashboardSummary,
  type Recording,
} from "@/types";

import { useCalendarDashboard } from "../upcomingMeetings/useCalendarDashboard";
import ProcessingCard from "./ProcessingCard";
import RecentRecordingsCard from "./RecentRecordingsCard";
import { useDashboardRecordings } from "./useDashboardRecordings";

const api = vi.hoisted(() => ({
  getCalendarDashboardSummary: vi.fn(),
  getRecordings: vi.fn(),
  getUnfinishedCaptures: vi.fn(),
}));

vi.mock("@/lib/api", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/api")>(),
  ...api,
}));
vi.mock("@/lib/timezone", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/timezone")>(),
  getUserTimeZone: () => Promise.resolve("UTC"),
}));
vi.mock("next/navigation", () => ({
  usePathname: () => "/",
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("next/link", () => ({
  default: ({ children, ...props }: ComponentPropsWithoutRef<"a">) => <a {...props}>{children}</a>,
}));

const recording = (status: RecordingStatus): Recording => ({
  id: "rec-1",
  name: "Weekly sync",
  status,
  created_at: "2026-10-29T09:00:00Z",
  updated_at: "2026-10-29T09:00:00Z",
  meeting_uid: "weekly",
  audio_path: "/audio.wav",
  is_archived: false,
  is_deleted: false,
});

const summary = (status: RecordingStatus): CalendarDashboardSummary => ({
  month: "2026-10",
  timezone: "UTC",
  state: "ready",
  provider_configured: false,
  is_syncing: false,
  connection_count: 0,
  selected_calendar_count: 0,
  day_counts: [],
  agenda_items: [],
  recording_items: [{
    id: "rec-1", name: "Weekly sync", starts_at: "2026-10-29T09:00:00Z",
    status, client_status: null, speaker_names: [], tags: [],
  }],
  next_event: null,
});

/** The dashboard in miniature: its calendar poll and its processing card. */
function Dashboard() {
  useCalendarDashboard();
  const { processing } = useDashboardRecordings();
  return <ProcessingCard recordings={processing} />;
}

const serverHas = (status: RecordingStatus) => {
  api.getRecordings.mockResolvedValue([recording(status)]);
  api.getCalendarDashboardSummary.mockResolvedValue(summary(status));
};

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-29T10:00:00Z"));
  api.getUnfinishedCaptures.mockResolvedValue([]);
  vi.stubGlobal("navigator", { locks: { query: async () => ({ held: [], pending: [] }) } });
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("the dashboard while a meeting processes on the server", () => {
  it("drops it from Processing once the calendar poll sees it finish", async () => {
    serverHas(RecordingStatus.PROCESSING);
    render(<CaptureProvider><Dashboard /></CaptureProvider>);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(screen.getByText("Weekly sync")).toBeInTheDocument();

    serverHas(RecordingStatus.PROCESSED);
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });

    expect(screen.queryByText("Weekly sync")).not.toBeInTheDocument();
    // One poll and the one refresh it caused: nothing loops.
    expect(api.getCalendarDashboardSummary).toHaveBeenCalledTimes(2);
    expect(api.getRecordings).toHaveBeenCalledTimes(2);
  });
});

/** The dashboard's calendar poll beside its recent meetings card. */
function RecentDashboard() {
  useCalendarDashboard();
  const { recent } = useDashboardRecordings();
  return <RecentRecordingsCard recordings={recent} timeZone="UTC" />;
}

describe("the dashboard while a meeting starts in another tab or browser", () => {
  it("lists it in Recent Meetings once the calendar poll sees it appear", async () => {
    api.getRecordings.mockResolvedValue([]);
    api.getCalendarDashboardSummary.mockResolvedValue({
      ...summary(RecordingStatus.UPLOADING), recording_items: [],
    });
    render(<CaptureProvider><RecentDashboard /></CaptureProvider>);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(screen.queryByText("Weekly sync")).not.toBeInTheDocument();

    api.getRecordings.mockResolvedValue([
      { ...recording(RecordingStatus.UPLOADING), client_status: ClientStatus.RECORDING },
    ]);
    const started = summary(RecordingStatus.UPLOADING);
    started.recording_items[0].client_status = ClientStatus.RECORDING;
    api.getCalendarDashboardSummary.mockResolvedValue(started);
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });

    expect(screen.getByText("Weekly sync")).toBeInTheDocument();
    expect(screen.getByText("Live")).toBeInTheDocument();
  });
});
