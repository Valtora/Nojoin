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
import { fillMarkerViolations } from "@/test/fillMarkerContracts";
import { activeTransitions, animatesWidth } from "@/test/widthTransition";

const routerPush = vi.fn();
const routerReplace = vi.fn();
const addNotification = vi.fn();
const getRecordings = vi.fn();
const getGlobalSpeakers = vi.fn();
const getTags = vi.fn();
const getRecordingsCalendar = vi.fn();
const start = vi.fn();

const captureState = {
  controller: { getState: () => ({ error: null }) },
  runtimeActive: false,
  status: "idle",
  support: { supported: true, mode: "shared_audio" },
};

let currentView: ViewType = "recordings";
let pausedRecording: { id: string } | null = null;
let pathname = "/recordings";
let unsentRecordings: string[] = [];
let selectedRecordingIds: string[] = [];
let isRecordingsSidebarCollapsed = false;
const setRecordingsSidebarWidth = vi.fn();
const toggleRecordingsSidebarCollapse = vi.fn(() => {
  isRecordingsSidebarCollapsed = !isRecordingsSidebarCollapsed;
});

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
  batchArchiveRecordings: vi.fn(),
  batchRestoreRecordings: vi.fn(),
  batchSoftDeleteRecordings: vi.fn(),
  batchPermanentlyDeleteRecordings: vi.fn(),
  getTags: (...args: unknown[]) => getTags(...args),
  discardRecordingCapture: vi.fn(),
  getRecordingsCalendar: (...args: unknown[]) => getRecordingsCalendar(...args),
}));

vi.mock("@/lib/notes/useNoteOutbox", () => ({
  useUnsentRecordings: () => unsentRecordings,
}));

const sweptLists: (readonly { id: string }[] | null)[] = [];

vi.mock("@/lib/notes/deletedRecordingNotes", () => ({
  useForgetNotesOfDeletedRecordings: (listed: readonly { id: string }[] | null) => {
    sweptLists.push(listed);
  },
}));

vi.mock("@/lib/capture/CaptureProvider", () => ({
  useCapture: () => ({
    ...captureState,
    cancel: vi.fn(),
    recordingId: null,
    pausedRecording,
    start: (...args: unknown[]) => start(...args),
  }),
}));

vi.mock("@/lib/connectivity/monitor", () => ({
  useConnectivityStore: (selector: (state: { status: string }) => unknown) =>
    selector({ status: "online" }),
}));

vi.mock("@/lib/timezone", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/timezone")>();
  return {
    ...actual,
    getUserTimeZone: async () => actual.setCachedUserTimeZone("Europe/London"),
    localDayRangeToUtc: () => ({
      startISO: "2026-05-25T00:00:00Z",
      endISO: "2026-05-25T23:59:59Z",
    }),
  };
});

vi.mock("@/lib/store", () => ({
  useNavigationStore: () => ({
    currentView,
    selectedTagIds: [],
    toggleTagFilter: vi.fn(),
    clearTagFilters: vi.fn(),
    selectedRecordingIds,
    selectionAnchorId: null,
    setRecordingSelection: vi.fn(),
    toggleRecordingSelection: vi.fn(),
    clearSelection: vi.fn(),
    pruneRecordingSelection: vi.fn(),
    recordingsSidebarWidth: 320,
    setRecordingsSidebarWidth,
    isRecordingsSidebarCollapsed,
    toggleRecordingsSidebarCollapse,
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

vi.mock("./LiveMeetingControls", () => ({
  default: () => <div data-testid="live-controls" />,
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
    start.mockReset();
    captureState.runtimeActive = false;
    captureState.status = "idle";
    currentView = "recordings";
    pausedRecording = null;
    pathname = "/recordings";
    unsentRecordings = [];
    selectedRecordingIds = [];
    isRecordingsSidebarCollapsed = false;
    setRecordingsSidebarWidth.mockClear();
    toggleRecordingsSidebarCollapse.mockClear();
    sweptLists.length = 0;
    useImportAudioStore.setState({ isImportAudioOpen: false });

    getRecordings.mockResolvedValue([recording]);
    getGlobalSpeakers.mockResolvedValue([]);
    getTags.mockResolvedValue([]);
    getRecordingsCalendar.mockResolvedValue({
      month: "2026-05",
      day_counts: [],
    });
  });

  it("checks ordinary, active and batch-selected recording fills", async () => {
    getRecordings.mockResolvedValue([recording, { ...recording, id: "rec-2", name: "Other meeting" }]);
    const { rerender } = renderWithProviders(<Sidebar />);
    const heading = await screen.findByRole("heading", { name: "Latest meeting" });
    const card = heading.closest("a")!;
    const check = (background: string) => {
      expect(card).toHaveClass(background);
      expect(fillMarkerViolations(card)).toEqual([]);
    };
    check("bg-surface-card");
    pathname = "/recordings/rec-1";
    rerender(<Sidebar />);
    check("bg-action-tint");
    // Selecting another recording takes the highlight off the active one.
    selectedRecordingIds = ["rec-2"];
    rerender(<Sidebar />);
    check("bg-surface-card");
    selectedRecordingIds = ["rec-1"];
    rerender(<Sidebar />);
    check("bg-action-tint");
    selectedRecordingIds = [];
    pathname = "/recordings";
    rerender(<Sidebar />);
    check("bg-surface-card");
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
      "Move to Bin",
    ]);
  });

  it("follows the pointer while resizing and animates only collapse and expand (#747)", async () => {
    const { rerender } = renderWithProviders(<Sidebar />);
    await screen.findByRole("heading", { name: "Latest meeting" });
    const rail = document.getElementById("sidebar-recordings-list")!;
    const handle = rail.querySelector<HTMLElement>(".cursor-col-resize")!;
    const expandedWidth = rail.style.width;
    expect(expandedWidth).toMatch(/^\d+px$/);
    expect(animatesWidth(rail)).toBe(true);

    fireEvent.pointerDown(handle, { button: 0 });
    expect(activeTransitions(rail)).toEqual([]);
    fireEvent.pointerMove(document, { clientX: 400 });
    expect(setRecordingsSidebarWidth).toHaveBeenCalledWith(400);
    expect(activeTransitions(rail)).toEqual([]);

    fireEvent.pointerUp(document);
    expect(animatesWidth(rail)).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: "Collapse recordings list" }));
    rerender(<Sidebar />);
    expect(rail.style.width).toMatch(/^\d+px$/);
    expect(rail.style.width).not.toBe(expandedWidth);
    expect(animatesWidth(rail)).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: "Expand recordings list" }));
    rerender(<Sidebar />);
    expect(rail.style.width).toBe(expandedWidth);
    expect(animatesWidth(rail)).toBe(true);
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

  describe("collapsed recordings rail", () => {
    beforeEach(() => {
      isRecordingsSidebarCollapsed = true;
    });

    it("starts a meeting, opens the recording and refreshes the list", async () => {
      start.mockResolvedValue({ recordingId: "rec-new" });
      renderWithProviders(<Sidebar />);
      const button = await screen.findByRole("button", { name: "Start Meeting" });
      await waitFor(() => expect(getRecordings).toHaveBeenCalled());
      const callsAfterMount = getRecordings.mock.calls.length;

      expect(button).toBeEnabled();
      expect(screen.getByRole("button", { name: "Import Audio" })).toBeInTheDocument();
      fireEvent.click(button);

      await waitFor(() => {
        expect(routerPush).toHaveBeenCalledWith("/recordings/rec-new");
        expect(getRecordings.mock.calls.length).toBeGreaterThan(callsAfterMount);
      });
      expect(start).toHaveBeenCalledExactlyOnceWith("");
      expect(screen.queryByTestId("live-controls")).not.toBeInTheDocument();
    });

    it.each(["recording", "paused", "error"])("cannot start again during live %s", async (status) => {
      captureState.runtimeActive = true;
      captureState.status = status;
      renderWithProviders(<Sidebar />);

      const button = await screen.findByRole("button", { name: "Meeting in progress" });
      expect(button).toBeDisabled();
      fireEvent.click(button);
      expect(start).not.toHaveBeenCalled();
      expect(screen.queryByTestId("live-controls")).not.toBeInTheDocument();
    });

    it.each<ViewType>(["archived", "deleted"])("labels %s without offering recording actions", async (view) => {
      currentView = view;
      renderWithProviders(<Sidebar />);

      const label = view === "archived" ? "Archived" : "Deleted";
      expect(await screen.findByRole("img", { name: label })).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Start Meeting" })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Import Audio" })).not.toBeInTheDocument();
      expect(screen.getAllByRole("button")).toHaveLength(1);
      expect(screen.getByRole("button", { name: "Expand recordings list" })).toBeInTheDocument();
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

    it("looks for notes of recordings deleted elsewhere once the list has loaded (issue #636)", async () => {
      renderWithProviders(<Sidebar />);

      await screen.findByRole("heading", { name: "Latest meeting" });

      expect(sweptLists[0]).toBeNull();
      expect(sweptLists.at(-1)?.map(({ id }) => id)).toEqual(["rec-1"]);
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
      importButtons().find((button) => button.textContent === "Import Audio")!;
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

    it("pairs compact Start and Import below lg, starts a meeting and refreshes the list", async () => {
      start.mockResolvedValue({ recordingId: "rec-new" });
      renderList();
      await screen.findByRole("heading", { name: "Latest meeting" });
      const callsAfterMount = getRecordings.mock.calls.length;

      const actionGroup = phoneImport()!.parentElement!;
      const button = within(actionGroup).getByRole("button", { name: "Start Meeting" });
      expect(button).toHaveTextContent(/^Start$/);
      expect(actionGroup).toHaveClass("flex", "shrink-0");
      expect(actionGroup.parentElement).toHaveClass("lg:hidden");
      expect(desktopImport().closest(".hidden")).toHaveClass("lg:block");
      expect(within(actionGroup).getAllByRole("button")).toEqual([button, phoneImport()]);
      fireEvent.click(button);

      await waitFor(() => {
        expect(routerPush).toHaveBeenCalledWith("/recordings/rec-new");
        expect(getRecordings.mock.calls.length).toBeGreaterThan(callsAfterMount);
      });
      expect(start).toHaveBeenCalledExactlyOnceWith("");
    });

    it.each(["recording", "paused", "error"])("blocks compact Start during live %s", async (status) => {
      captureState.runtimeActive = true;
      captureState.status = status;
      renderList();
      await screen.findByRole("heading", { name: "Latest meeting" });

      const actionGroup = phoneImport()!.parentElement!;
      const button = within(actionGroup).getByRole("button", { name: "Meeting in progress" });
      expect(button).toBeDisabled();
      expect(within(actionGroup).queryByTestId("live-controls")).not.toBeInTheDocument();
      fireEvent.click(button);
      expect(start).not.toHaveBeenCalled();
      expect(routerPush).not.toHaveBeenCalled();
    });

    it.each<ViewType>(["archived", "deleted"])(
      "has no start or import entry in the %s view",
      async (view) => {
        currentView = view;
        getRecordings.mockResolvedValue([]);
        renderList();

        await screen.findByText(
          view === "archived" ? "No archived recordings." : "The bin is empty.",
        );
        expect(importButtons()).toHaveLength(0);
        expect(screen.queryByRole("button", { name: "Start Meeting" })).not.toBeInTheDocument();
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
