import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  fireEvent,
  renderWithProviders,
  screen,
  waitFor,
  within,
} from "@/test/renderWithProviders";
import { useImportAudioStore } from "@/lib/importAudioStore";
import type { ViewType } from "@/lib/store";
import Sidebar from "./Sidebar";
import ImportAudioHost from "./importAudio/ImportAudioHost";
import { RecordingStatus, type Recording } from "@/types";

const routerPush = vi.fn();
const routerReplace = vi.fn();
const addNotification = vi.fn();
const getRecordings = vi.fn();
const getGlobalSpeakers = vi.fn();
const getTags = vi.fn();
const getRecordingsCalendar = vi.fn();

let currentView: ViewType = "recordings";
let pausedRecording: { id: string } | null = null;
let pathname = "/recordings";
let unsentRecordings: string[] = [];

vi.mock("next/link", () => ({
  default: ({ children, href, ...props }: React.ComponentPropsWithoutRef<"a">) => (
    <a href={typeof href === "string" ? href : "#"} {...props}>
      {children}
    </a>
  ),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => pathname,
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
  discardRecordingCapture: vi.fn(),
  getRecordingsCalendar: (...args: unknown[]) => getRecordingsCalendar(...args),
}));

vi.mock("@/lib/notes/useNoteOutbox", () => ({
  useUnsentRecordings: () => unsentRecordings,
}));

vi.mock("@/lib/capture/CaptureProvider", () => ({
  useCapture: () => ({
    cancel: vi.fn(),
    recordingId: null,
    pausedRecording,
    runtimeActive: false,
  }),
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
    currentView,
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
  default: ({ pairedAction }: { pairedAction?: React.ReactNode }) => (
    <div data-testid="meeting-controls">{pairedAction}</div>
  ),
}));

vi.mock("./MonthCalendar", () => ({
  default: () => <div data-testid="month-calendar" />,
}));

vi.mock("./RecordingInfoModal", () => ({
  default: () => null,
}));

let lastMenuItems: { label: string }[] = [];
vi.mock("./ContextMenu", () => ({
  default: ({ items }: { items: { label: string }[] }) => {
    lastMenuItems = items;
    return <div data-testid="sidebar-context-menu" />;
  },
}));

vi.mock("./ConfirmationModal", () => ({
  default: () => null,
}));

vi.mock("./BatchActionBar", () => ({
  default: () => null,
}));

vi.mock("./ImportAudioModal", () => ({
  default: ({ isOpen }: { isOpen: boolean }) =>
    isOpen ? <div data-testid="import-modal" /> : null,
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
    currentView = "recordings";
    pausedRecording = null;
    pathname = "/recordings";
    unsentRecordings = [];
    useImportAudioStore.setState({ isImportAudioOpen: false });

    getRecordings.mockResolvedValue([recording]);
    getGlobalSpeakers.mockResolvedValue([]);
    getTags.mockResolvedValue([]);
    getRecordingsCalendar.mockResolvedValue({
      month: "2026-05",
      day_counts: [],
    });
  });

  it("keeps the recordings list on /recordings instead of redirecting to the latest item", async () => {
    renderWithProviders(<Sidebar />);

    expect(await screen.findByRole("heading", { name: "Latest meeting" })).toBeInTheDocument();

    await waitFor(() => {
      expect(getRecordings).toHaveBeenCalled();
    });

    expect(routerReplace).not.toHaveBeenCalled();
  });

  it("exposes the recordings-view context menu action set", async () => {
    lastMenuItems = [];
    renderWithProviders(<Sidebar />);

    const heading = await screen.findByRole("heading", { name: "Latest meeting" });
    fireEvent.contextMenu(heading);

    expect(await screen.findByTestId("sidebar-context-menu")).toBeInTheDocument();
    expect(lastMenuItems.map((item) => item.label)).toEqual([
      "Rename",
      "Retry Speaker Inference",
      "Retry Processing",
      "Show Recording Info",
      "Archive",
      "Delete",
    ]);
  });

  it("re-fetches recordings when a recording-updated event fires", async () => {
    renderWithProviders(<Sidebar />);

    await screen.findByRole("heading", { name: "Latest meeting" });
    const callsAfterMount = getRecordings.mock.calls.length;

    fireEvent(window, new CustomEvent("recording-updated", { detail: {} }));

    await waitFor(() => {
      expect(getRecordings.mock.calls.length).toBeGreaterThan(callsAfterMount);
    });
  });

  it("marks an import that did not finish rather than showing it uploading (issue #569)", async () => {
    getRecordings.mockResolvedValue([
      { ...recording, status: RecordingStatus.UPLOADING, import_stalled: true },
      { ...recording, id: "rec-2", name: "Import on its way", status: RecordingStatus.UPLOADING },
    ]);
    renderWithProviders(<Sidebar />);

    await screen.findByRole("heading", { name: "Latest meeting" });

    expect(screen.getAllByTitle("This import did not finish uploading.")).toHaveLength(1);
  });

  describe("unsent notes", () => {
    it("marks a recording in the list with unsent notes", async () => {
      unsentRecordings = ["rec-1"];
      renderWithProviders(<Sidebar />);

      const heading = await screen.findByRole("heading", { name: "Latest meeting" });
      const item = heading.closest("a") ?? heading.parentElement!;

      expect(within(item).getByText("Unsent notes")).toBeInTheDocument();
      // The mark is part of the link to the recording, so it is read with it.
      expect(screen.getByRole("link", { name: /Latest meeting.*Unsent notes/ })).toBeInTheDocument();
    });

    it("leaves the mark off the recording on screen, which shows its notes itself", async () => {
      unsentRecordings = ["rec-1"];
      pathname = "/recordings/rec-1";
      renderWithProviders(<Sidebar />);

      await screen.findByRole("heading", { name: "Latest meeting" });

      expect(screen.queryByText("Unsent notes")).not.toBeInTheDocument();
    });

    it("shows no mark when every note is saved", async () => {
      renderWithProviders(<Sidebar />);

      await screen.findByRole("heading", { name: "Latest meeting" });

      expect(screen.queryByText("Unsent notes")).not.toBeInTheDocument();
    });
  });

  describe("Import Audio", () => {
    const renderList = () =>
      renderWithProviders(
        <>
          <Sidebar />
          <ImportAudioHost />
        </>,
      );
    const importButtons = () =>
      screen.queryAllByRole("button", { name: "Import Audio" });
    // jsdom applies no CSS, so the desktop pair and the phone header button
    // are both in the tree; each is found by where it sits.
    const desktopImport = () =>
      within(screen.getByTestId("meeting-controls")).getByRole("button", {
        name: "Import Audio",
      });
    const phoneImport = () =>
      importButtons().find((button) => button.textContent === "Import");
    const emptyState = () =>
      screen.getByText("No recordings found.").parentElement as HTMLElement;

    it("pairs a labelled Import Audio with Start Meeting and opens the dialog", async () => {
      renderList();
      await screen.findByRole("heading", { name: "Latest meeting" });

      const button = desktopImport();
      expect(button).toHaveTextContent("Import Audio");
      expect(screen.queryByTestId("import-modal")).not.toBeInTheDocument();

      fireEvent.click(button);

      expect(screen.getByTestId("import-modal")).toBeInTheDocument();
    });

    it("offers a short Import in the phone list header, named in full", async () => {
      renderList();
      await screen.findByRole("heading", { name: "Latest meeting" });

      const button = phoneImport();
      expect(button).toBeDefined();
      fireEvent.click(button as HTMLElement);

      expect(screen.getByTestId("import-modal")).toBeInTheDocument();
    });

    it.each<ViewType>(["archived", "deleted"])(
      "has no import entry in the %s view",
      async (view) => {
        currentView = view;
        getRecordings.mockResolvedValue([]);
        renderList();

        await screen.findByText(/^No (archived|deleted) recordings\.$/);
        expect(importButtons()).toHaveLength(0);
      },
    );

    it("is disabled while a paused recording holds the capture lock, and re-enables when it clears", async () => {
      pausedRecording = { id: "rec-paused" };
      getRecordings.mockResolvedValue([]);
      const { rerender } = renderList();

      await waitFor(() => {
        expect(importButtons()).toHaveLength(3);
      });
      for (const button of importButtons()) {
        expect(button).toBeDisabled();
        fireEvent.click(button);
      }
      expect(screen.queryByTestId("import-modal")).not.toBeInTheDocument();

      pausedRecording = null;
      rerender(
        <>
          <Sidebar />
          <ImportAudioHost />
        </>,
      );

      for (const button of importButtons()) {
        expect(button).toBeEnabled();
      }
    });

    it("opens the import dialog from the empty list", async () => {
      getRecordings.mockResolvedValue([]);
      renderList();

      fireEvent.click(
        await within(emptyState()).findByRole("button", { name: "Import Audio" }),
      );

      expect(screen.getByTestId("import-modal")).toBeInTheDocument();
    });

    it("keeps the empty-list button back until the first fetch lands", async () => {
      let resolveRecordings: (value: Recording[]) => void = () => {};
      getRecordings.mockReturnValue(
        new Promise<Recording[]>((resolve) => {
          resolveRecordings = resolve;
        }),
      );
      renderList();

      expect(
        within(emptyState()).queryByRole("button", { name: "Import Audio" }),
      ).not.toBeInTheDocument();

      resolveRecordings([]);

      expect(
        await within(emptyState()).findByRole("button", { name: "Import Audio" }),
      ).toBeInTheDocument();
    });

    it("offers no empty-list button when a search is what emptied the list", async () => {
      getRecordings.mockResolvedValue([]);
      renderList();
      await within(emptyState()).findByRole("button", { name: "Import Audio" });

      fireEvent.change(screen.getByPlaceholderText("Search..."), {
        target: { value: "budget" },
      });

      expect(
        within(emptyState()).queryByRole("button", { name: "Import Audio" }),
      ).not.toBeInTheDocument();
    });
  });
});
