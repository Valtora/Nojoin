import type { ComponentPropsWithoutRef } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setCachedUserTimeZone } from "@/lib/timezone";
import { withProcessTimeZone } from "@/test/processTimeZone";
import { renderWithProviders, screen, within } from "@/test/renderWithProviders";
import { useNavigationStore } from "@/lib/store";
import { RecordingStatus, type Recording } from "@/types";
import Sidebar from "./Sidebar";

const getRecordings = vi.hoisted(() => vi.fn());

vi.mock("next/link", () => ({
  default: ({ children, ...props }: ComponentPropsWithoutRef<"a">) => <a {...props}>{children}</a>,
}));

vi.mock("next/navigation", () => ({
  usePathname: () => "/recordings",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

vi.mock("@/lib/api", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/api")>(),
  getRecordings,
  getGlobalSpeakers: vi.fn().mockResolvedValue([]),
  getTags: vi.fn().mockResolvedValue([]),
  getRecordingsCalendar: vi.fn().mockResolvedValue({ month: "2026-10", day_counts: [] }),
}));

vi.mock("@/lib/timezone", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/timezone")>(),
  getUserTimeZone: () => Promise.resolve("UTC"),
}));

vi.mock("@/lib/notes/useNoteOutbox", () => ({ useUnsentRecordings: () => [] }));
vi.mock("@/lib/notes/deletedRecordingNotes", () => ({ useForgetNotesOfDeletedRecordings: () => {} }));
vi.mock("@/lib/capture/CaptureProvider", () => ({
  useCapture: () => ({ cancel: vi.fn(), recordingId: null, pausedRecording: null, runtimeActive: false }),
}));
vi.mock("./MeetingControls", () => ({ default: () => null }));
vi.mock("./MonthCalendar", () => ({ default: () => null }));

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

const earlyInstant = new Date("2026-10-27T02:30:00Z");
const lateInstant = new Date("2026-10-27T20:30:00Z");
const formatDate = (date: Date, timeZone: string) => date.toLocaleDateString("en-GB", {
  day: "numeric", month: "short", year: "numeric", timeZone,
});
const formatClock = (date: Date, timeZone: string) => date.toLocaleTimeString([], {
  hour: "2-digit", minute: "2-digit", hour12: false, timeZone,
});

// The browser's zone and the Nojoin setting differ, and the sidebar must show
// the setting's day and clock.
describe.each([
  ["America/New_York", "Asia/Tokyo"],
  ["Asia/Tokyo", "America/New_York"],
])("sidebar server timestamps in %s with the setting in %s", (processZone, settingZone) => {
  withProcessTimeZone(processZone);

  beforeEach(() => {
    setCachedUserTimeZone(settingZone);
    useNavigationStore.setState({
      currentView: "recordings", selectedTagIds: [], selectedRecordingIds: [],
      isRecordingsSidebarCollapsed: false,
    });
    getRecordings.mockResolvedValue([
      recording("early", "2026-10-27T02:30:00"),
      recording("late", "2026-10-27T20:30:00"),
    ]);
  });

  it("labels recording days from UTC in the setting's zone across midnight", async () => {
    renderWithProviders(<Sidebar />);
    for (const [name, instant] of [["early", earlyInstant], ["late", lateInstant]] as const) {
      const title = await screen.findByRole("heading", { name: `Meeting ${name}` });
      expect(within(title.closest("a")!).getByText(formatDate(instant, settingZone))).toBeInTheDocument();
    }
  });

  it("shows the UTC start and duration-derived end as clock times in the setting's zone", async () => {
    renderWithProviders(<Sidebar />);
    const title = await screen.findByRole("heading", { name: "Meeting early" });
    const end = new Date("2026-10-27T02:40:00Z");
    const expected = `${formatClock(earlyInstant, settingZone)} - ${formatClock(end, settingZone)}`;
    expect(within(title.closest("a")!).getByText(expected)).toBeInTheDocument();
  });
});
