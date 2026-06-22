import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import Sidebar from "./Sidebar";
import { ViewportDensityProvider } from "./ViewportDensityProvider";
import { RecordingStatus, type Recording } from "@/types";

const routerPush = vi.fn();
const routerReplace = vi.fn();
const addNotification = vi.fn();
const getRecordings = vi.fn();
const getGlobalSpeakers = vi.fn();
const getTags = vi.fn();
const getRecordingsCalendar = vi.fn();

vi.mock("next/link", () => ({
  default: ({ children, href, ...props }: React.ComponentPropsWithoutRef<"a">) => (
    <a href={typeof href === "string" ? href : "#"} {...props}>
      {children}
    </a>
  ),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => "/recordings",
  useRouter: () => ({
    push: routerPush,
    replace: routerReplace,
  }),
}));

vi.mock("@/lib/api", () => ({
  getRecordings: (...args: unknown[]) => getRecordings(...args),
  renameRecording: vi.fn(),
  retryProcessing: vi.fn(),
  inferSpeakers: vi.fn(),
  getGlobalSpeakers: (...args: unknown[]) => getGlobalSpeakers(...args),
  archiveRecording: vi.fn(),
  restoreRecording: vi.fn(),
  softDeleteRecording: vi.fn(),
  permanentlyDeleteRecording: vi.fn(),
  getTags: (...args: unknown[]) => getTags(...args),
  cancelProcessing: vi.fn(),
  getRecordingsCalendar: (...args: unknown[]) => getRecordingsCalendar(...args),
}));

vi.mock("@/lib/timezone", () => ({
  getUserTimeZone: () => Promise.resolve("Europe/London"),
  localDayRangeToUtc: () => ({
    startISO: "2026-05-25T00:00:00Z",
    endISO: "2026-05-25T23:59:59Z",
  }),
}));

vi.mock("@/lib/store", () => ({
  useNavigationStore: () => ({
    currentView: "recordings",
    selectedTagIds: [],
    toggleTagFilter: vi.fn(),
    clearTagFilters: vi.fn(),
    selectionMode: false,
    selectedRecordingIds: [],
    toggleRecordingSelection: vi.fn(),
    selectAllRecordings: vi.fn(),
    clearSelection: vi.fn(),
    recordingsSidebarWidth: 320,
    setRecordingsSidebarWidth: vi.fn(),
  }),
}));

vi.mock("@/lib/notificationStore", () => ({
  useNotificationStore: () => ({ addNotification }),
}));

vi.mock("@/lib/searchUtils", () => ({
  createFuse: (items: Recording[]) => ({
    search: () => items.map((item) => ({ item })),
  }),
}));

vi.mock("./MeetingControls", () => ({
  default: () => <div data-testid="meeting-controls" />,
}));

vi.mock("./MonthCalendar", () => ({
  default: () => <div data-testid="month-calendar" />,
}));

vi.mock("./RecordingInfoModal", () => ({
  default: () => null,
}));

vi.mock("./ContextMenu", () => ({
  default: () => null,
}));

vi.mock("./ConfirmationModal", () => ({
  default: () => null,
}));

vi.mock("./BatchActionBar", () => ({
  default: () => null,
}));

const recording: Recording = {
  id: "rec-1",
  created_at: "2026-05-25T11:54:00Z",
  updated_at: "2026-05-25T11:54:00Z",
  name: "Latest meeting",
  meeting_uid: "meeting-1",
  audio_path: "/tmp/latest.wav",
  duration_seconds: 360,
  status: RecordingStatus.PROCESSED,
  is_archived: false,
  is_deleted: false,
  tags: [],
  speakers: [],
};

describe("Sidebar", () => {
  beforeEach(() => {
    routerPush.mockReset();
    routerReplace.mockReset();
    addNotification.mockReset();

    getRecordings.mockResolvedValue([recording]);
    getGlobalSpeakers.mockResolvedValue([]);
    getTags.mockResolvedValue([]);
    getRecordingsCalendar.mockResolvedValue({
      month: "2026-05",
      day_counts: [],
    });
  });

  it("keeps the recordings list on /recordings instead of redirecting to the latest item", async () => {
    render(
      <ViewportDensityProvider>
        <Sidebar />
      </ViewportDensityProvider>,
    );

    expect(await screen.findByRole("heading", { name: "Latest meeting" })).toBeInTheDocument();

    await waitFor(() => {
      expect(getRecordings).toHaveBeenCalled();
    });

    expect(routerReplace).not.toHaveBeenCalled();
  });
});
