import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  act,
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
import { ClientStatus, RecordingStatus, type Recording } from "@/types";
import { fillMarkerViolations } from "@/test/fillMarkerContracts";
import { activeTransitions, animatesWidth } from "@/test/widthTransition";

const routerPush = vi.fn();
const routerReplace = vi.fn();
const addNotification = vi.fn();
const getRecordings = vi.fn();
const getRecording = vi.fn();
const getGlobalSpeakers = vi.fn();
const getTags = vi.fn();
const getRecordingsCalendar = vi.fn();
const renameRecording = vi.fn();
const refreshPausedRecording = async () => null;
const start = vi.fn();

const captureState = {
  controller: { getState: () => ({ error: null }) },
  runtimeActive: false,
  uploadQueue: { segments: 0, seconds: 0 },
  coverageWarning: null,
  status: "idle",
  support: { supported: true, mode: "shared_audio" },
};

let currentView: ViewType = "recordings";
let pausedRecording: { id: string } | null = null;
let attentionRecordings: Recording[] = [];
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
  renameRecording: (...args: unknown[]) => renameRecording(...args),
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
  // The retry dialog's reads.
  getRecording: (...args: unknown[]) => getRecording(...args),
  getSettings: vi.fn(async () => ({})),
  getLanguageOptions: vi.fn(() => new Promise(() => {})),
  reprocessRecording: vi.fn(),
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
    attentionRecordings,
    refreshPausedRecording,
    start: (...args: unknown[]) => start(...args),
  }),
  useCaptureState: (select: (state: { elapsedSeconds: number; uploadQueue: { segments: number; seconds: number } }) => unknown) =>
    select({ elapsedSeconds: 0, uploadQueue: { segments: 0, seconds: 0 } }),
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

// One array, as the store keeps: a new one on every render would re-read the
// list on every render.
const noTagIds: number[] = [];
let selectedTagIds = noTagIds;

vi.mock("@/lib/store", () => ({
  useNavigationStore: () => ({
    currentView,
    selectedTagIds,
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
  useNotificationStore: (selector?: (state: { addNotification: typeof addNotification }) => unknown) =>
    selector ? selector({ addNotification }) : { addNotification },
}));

vi.mock("./LiveMeetingControls", async (importOriginal) => ({
  ...await importOriginal<typeof import("./LiveMeetingControls")>(),
  default: () => <div data-testid="live-controls" />,
}));

vi.mock("./MonthCalendar", () => ({
  default: () => <div data-testid="month-calendar" />,
}));

vi.mock("./RecordingInfoModal", () => ({
  default: () => null,
}));

let lastMenuItems: { label: string; onClick?: () => void }[] = [];
vi.mock("./ContextMenu", () => ({
  default: ({ items }: { items: { label: string; onClick?: () => void }[] }) => {
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
    attentionRecordings = [];
    pathname = "/recordings";
    unsentRecordings = [];
    selectedRecordingIds = [];
    isRecordingsSidebarCollapsed = false;
    setRecordingsSidebarWidth.mockClear();
    toggleRecordingsSidebarCollapse.mockClear();
    sweptLists.length = 0;
    selectedTagIds = noTagIds;
    useImportAudioStore.setState({ isImportAudioOpen: false });

    getRecordings.mockResolvedValue([recording]);
    getRecording.mockReset();
    renameRecording.mockReset().mockResolvedValue(undefined);
    getGlobalSpeakers.mockResolvedValue([]);
    getTags.mockResolvedValue([]);
    getRecordingsCalendar.mockResolvedValue({
      month: "2026-05",
      day_counts: [],
    });
  });

  it.each(["recordings", "archived", "deleted"] as ViewType[])(
    "marks recoverable recordings in the %s list, including an active row and search results",
    async (view) => {
      currentView = view;
      pathname = "/recordings/rec-1";
      const paused = { ...recording, status: RecordingStatus.PAUSED };
      const held = { ...paused, id: "held", name: "Held in another tab" };
      // The server answers the search.
      getRecordings.mockImplementation(async (filters?: { q?: string }) =>
        filters?.q === "Latest" ? [paused] : [paused, held],
      );
      attentionRecordings = [paused];
      renderWithProviders(<Sidebar />);

      const active = (await screen.findByRole("heading", { name: recording.name })).closest("a")!;
      expect(within(active).getByText("Paused, needs attention")).toBeVisible();
      const heldLink = screen.getByRole("heading", { name: held.name }).closest("a")!;
      expect(within(heldLink).queryByText(/needs attention/)).not.toBeInTheDocument();
      fireEvent.change(screen.getByPlaceholderText("Search..."), { target: { value: "Latest" } });
      await waitFor(() => expect(screen.queryByRole("heading", { name: held.name })).not.toBeInTheDocument());
      expect(within(active).getByText("Paused, needs attention")).toBeVisible();
    },
  );

  it("names the search filter button and says whether the filters are open (issue #840)", async () => {
    renderWithProviders(<Sidebar />);
    const filter = await screen.findByRole("button", { name: "Filter recordings" });
    expect(filter).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(filter);
    expect(screen.getByRole("button", { name: "Filter recordings" })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("Date Filter")).toBeInTheDocument();
  });

  it("labels an interrupted upload in the sidebar", async () => {
    const interrupted = { ...recording, status: RecordingStatus.UPLOADING, capture_stalled: true };
    getRecordings.mockResolvedValue([interrupted]);
    attentionRecordings = [interrupted];
    renderWithProviders(<Sidebar />);
    const link = (await screen.findByRole("heading", { name: recording.name })).closest("a")!;
    expect(within(link).getByText("Interrupted, needs attention")).toBeVisible();
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

  const unfinished = ["Rename", "Discard Recording", "Show Recording Info", "Archive", "Move to Bin"];
  it.each([
    ["a live capture", { status: RecordingStatus.UPLOADING, client_status: ClientStatus.RECORDING, discardable: true }, unfinished],
    ["a paused capture", { status: RecordingStatus.PAUSED, client_status: ClientStatus.PAUSED, discardable: true }, unfinished],
    ["a first pass", { status: RecordingStatus.PROCESSING, discardable: true }, unfinished],
    // Regenerating notes, inferring speakers or reprocessing: still a finished meeting.
    ["a finished meeting back in processing", { status: RecordingStatus.PROCESSING, discardable: false },
      ["Rename", "Show Recording Info", "Archive", "Move to Bin"]],
    ["a failed recording", { status: RecordingStatus.ERROR },
      ["Rename", "Retry Processing", "Show Recording Info", "Archive", "Move to Bin"]],
  ] as const)("offers on %s only what the server would do (#832, #833)", async (_, state, expected) => {
    getRecordings.mockResolvedValue([{ ...recording, ...state }]);
    lastMenuItems = [];
    renderWithProviders(<Sidebar />);

    fireEvent.contextMenu(await screen.findByRole("heading", { name: "Latest meeting" }));

    expect(await screen.findByTestId("sidebar-context-menu")).toBeInTheDocument();
    expect(lastMenuItems.map((item) => item.label)).toEqual(expected);
  });

  const REPLACES = /replaces the current transcript/;
  const retryFromTheRail = async (read: Partial<Recording>) => {
    const failed = { ...recording, status: RecordingStatus.ERROR };
    getRecordings.mockResolvedValue([failed]);
    getRecording.mockResolvedValue({ ...failed, ...read });
    renderWithProviders(<Sidebar />);
    fireEvent.contextMenu(await screen.findByRole("heading", { name: "Latest meeting" }));
    await screen.findByTestId("sidebar-context-menu");
    act(() => lastMenuItems.find((item) => item.label === "Retry Processing")?.onClick?.());
    return screen.findByRole("dialog", { name: "Retry processing" });
  };

  it("labels a failed recording's icon as the queued and processing ones are", async () => {
    getRecordings.mockResolvedValue([{ ...recording, status: RecordingStatus.ERROR }]);
    renderWithProviders(<Sidebar />);

    const failedIcon = await screen.findByRole("img", { name: "Processing failed" });
    expect(failedIcon).toHaveAttribute("title", expect.stringMatching(/^Processing failed/));
  });

  it("warns on a retry from the rail when a failed reprocess kept the transcript", async () => {
    await retryFromTheRail({
      previous_publication_available: true,
      transcript: { segments: [{ start: 0, end: 1, speaker: "SPEAKER_00", text: "Edited line" }] },
    } as unknown as Partial<Recording>);

    expect(await screen.findByText(REPLACES)).toBeInTheDocument();
    expect(getRecording).toHaveBeenCalledWith("rec-1");
  });

  it("drops the warning on a retry from the rail once it knows nothing would be replaced", async () => {
    await retryFromTheRail({ transcript: { segments: [], text: "" } } as unknown as Partial<Recording>);

    expect(await screen.findByText(/Processing runs again on this recording's audio/)).toBeInTheDocument();
    expect(screen.queryByText(REPLACES)).not.toBeInTheDocument();
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

  it("applies a rename made elsewhere in place, without reading the list again", async () => {
    renderWithProviders(<Sidebar />);

    await screen.findByRole("heading", { name: "Latest meeting" });
    const callsAfterMount = getRecordings.mock.calls.length;

    act(() => {
      fireEvent(window, new CustomEvent("recording-updated", { detail: { id: "rec-1", name: "Renamed on its page" } }));
    });

    expect(screen.getByRole("heading", { name: "Renamed on its page" })).toBeInTheDocument();
    await act(async () => {});
    expect(getRecordings).toHaveBeenCalledTimes(callsAfterMount);
  });

  describe("renaming from the row menu", () => {
    let announced: unknown[] = [];
    const listen = (event: Event) => announced.push((event as CustomEvent).detail);
    beforeEach(() => {
      announced = [];
      window.addEventListener("recording-updated", listen);
    });
    afterEach(() => {
      window.removeEventListener("recording-updated", listen);
      vi.restoreAllMocks();
    });

    const renameTo = async (name: string) => {
      fireEvent.contextMenu(await screen.findByRole("heading", { name: "Latest meeting" }));
      act(() => lastMenuItems.find((item) => item.label === "Rename")?.onClick?.());
      const input = screen.getByDisplayValue("Latest meeting");
      fireEvent.change(input, { target: { value: name } });
      fireEvent.keyDown(input, { key: "Enter" });
    };

    it("shows the name at once and tells the open recording", async () => {
      let finishRename!: () => void;
      renameRecording.mockImplementationOnce(() => new Promise<void>((resolve) => { finishRename = resolve; }));
      renderWithProviders(<Sidebar />);
      await screen.findByRole("heading", { name: "Latest meeting" });
      const callsAfterMount = getRecordings.mock.calls.length;

      await renameTo("Planning review");

      expect(screen.getByRole("heading", { name: "Planning review" })).toBeInTheDocument();
      expect(announced).toEqual([{ id: "rec-1", name: "Planning review" }]);
      await act(async () => finishRename());
      expect(renameRecording).toHaveBeenCalledExactlyOnceWith("rec-1", "Planning review");
      expect(getRecordings).toHaveBeenCalledTimes(callsAfterMount);
    });

    it("puts the old name back everywhere when the save fails", async () => {
      vi.spyOn(console, "error").mockImplementation(() => {});
      let failRename!: (error: Error) => void;
      renameRecording.mockImplementationOnce(() => new Promise<void>((_, reject) => { failRename = reject; }));
      renderWithProviders(<Sidebar />);

      await renameTo("Planning review");
      expect(screen.getByRole("heading", { name: "Planning review" })).toBeInTheDocument();
      await act(async () => failRename(new Error("offline")));

      expect(await screen.findByRole("heading", { name: "Latest meeting" })).toBeInTheDocument();
      expect(announced).toEqual([
        { id: "rec-1", name: "Planning review" },
        { id: "rec-1", name: "Latest meeting" },
      ]);
      expect(addNotification).toHaveBeenCalledWith({ type: "error", message: "Failed to rename recording." });
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

      if (status === "error") {
        const button = await screen.findByRole("button", { name: "Meeting in progress" });
        expect(button).toBeDisabled();
        fireEvent.click(button);
      } else {
        const passive = await screen.findByTestId("capture-status");
        expect(passive).toHaveTextContent(status === "paused" ? /^Paused$/ : /^Recording$/);
        expect(screen.queryByRole("button", { name: "Meeting in progress" })).not.toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Start Meeting" })).not.toBeInTheDocument();
      }
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
    const emptyState = async () =>
      (await screen.findByText("No recordings found.")).parentElement as HTMLElement;

    it("pairs a labelled Import Audio with Start Meeting and opens the dialog", async () => {
      renderList();
      await screen.findByRole("heading", { name: "Latest meeting" });

      const button = desktopImport();
      expect(button).toHaveTextContent("Import Audio");
      expect(screen.queryByTestId("import-modal")).not.toBeInTheDocument();

      fireEvent.click(button);

      expect(await screen.findByTestId("import-modal")).toBeInTheDocument();
    });

    it("offers a short Import in the phone list header, named in full", async () => {
      renderList();
      await screen.findByRole("heading", { name: "Latest meeting" });

      const button = phoneImport();
      expect(button).toBeDefined();
      fireEvent.click(button as HTMLElement);

      expect(await screen.findByTestId("import-modal")).toBeInTheDocument();
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
      if (status === "error") {
        const button = within(actionGroup).getByRole("button", { name: "Meeting in progress" });
        expect(button).toBeDisabled();
        fireEvent.click(button);
      } else {
        expect(within(actionGroup).getByTestId("capture-status")).toHaveTextContent(status === "paused" ? /^Paused$/ : /^Recording$/);
        expect(within(actionGroup).queryByRole("button", { name: "Meeting in progress" })).not.toBeInTheDocument();
        expect(within(actionGroup).queryByRole("button", { name: "Start Meeting" })).not.toBeInTheDocument();
      }
      expect(within(actionGroup).queryByTestId("live-controls")).not.toBeInTheDocument();
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
        await within(await emptyState()).findByRole("button", { name: "Import Audio" }),
      );

      expect(await screen.findByTestId("import-modal")).toBeInTheDocument();
    });

    it("says it is loading, with no empty-list button, until the first fetch lands", async () => {
      let resolveRecordings: (value: Recording[]) => void = () => {};
      getRecordings.mockReturnValue(
        new Promise<Recording[]>((resolve) => {
          resolveRecordings = resolve;
        }),
      );
      renderList();

      expect(screen.getByText("Loading recordings...")).toBeInTheDocument();
      expect(screen.queryByText("No recordings found.")).not.toBeInTheDocument();
      expect(importButtons()).toHaveLength(2);

      resolveRecordings([]);

      expect(
        await within(await emptyState()).findByRole("button", { name: "Import Audio" }),
      ).toBeInTheDocument();
      expect(screen.queryByText("Loading recordings...")).not.toBeInTheDocument();
    });

    it("says nothing matches, with no empty-list button, when a search is what emptied the list", async () => {
      getRecordings.mockResolvedValue([]);
      renderList();
      await within(await emptyState()).findByRole("button", { name: "Import Audio" });

      fireEvent.change(screen.getByPlaceholderText("Search..."), {
        target: { value: "budget" },
      });

      expect(screen.getByText("No recordings match.")).toBeInTheDocument();
      expect(importButtons()).toHaveLength(2);
      await waitFor(() =>
        expect(getRecordings).toHaveBeenLastCalledWith(
          expect.objectContaining({ q: "budget", skip: 0, limit: 50 }),
        ),
      );
      expect(screen.queryByText("No recordings found.")).not.toBeInTheDocument();
    });

    it("offers to try again when the list cannot be read", async () => {
      vi.spyOn(console, "error").mockImplementation(() => {});
      getRecordings.mockRejectedValueOnce(new Error("offline"));
      renderList();

      fireEvent.click(await screen.findByRole("button", { name: "Try again" }));

      expect(await screen.findByRole("heading", { name: "Latest meeting" })).toBeInTheDocument();
    });
  });

  describe("paging and search (issues #900 and #901)", () => {
    /** A made-up library, newest first, served a window at a time. */
    const library = Array.from({ length: 60 }, (_, n) => ({
      ...recording,
      id: `rec-${n}`,
      name: `Meeting ${n}`,
      created_at: new Date(Date.UTC(2026, 4, 25, 12) - n * 3_600_000).toISOString(),
    }));
    const serveLibrary = () =>
      getRecordings.mockImplementation(async (filters: { skip?: number; limit?: number } = {}) =>
        library.slice(filters.skip ?? 0, (filters.skip ?? 0) + (filters.limit ?? 100)),
      );

    it("searches on the server once typing pauses, with the filters", async () => {
      const tags = [3];
      selectedTagIds = tags;
      renderWithProviders(<Sidebar />);
      await screen.findByRole("heading", { name: "Latest meeting" });

      const search = screen.getByRole("textbox", { name: "Search recordings" });
      for (const value of ["b", "bu", "budget"]) {
        fireEvent.change(search, { target: { value } });
      }

      await waitFor(() =>
        expect(getRecordings).toHaveBeenLastCalledWith(
          expect.objectContaining({ q: "budget", tag_ids: tags, skip: 0, limit: 50 }),
        ),
      );
      const searched = getRecordings.mock.calls
        .map(([filters]) => (filters as { q?: string }).q)
        .filter(Boolean);
      expect(searched).toEqual(["budget"]);
    });

    it("loads older recordings from the end of the list and says when there are none", async () => {
      serveLibrary();
      renderWithProviders(<Sidebar />);
      await screen.findByRole("heading", { name: "Meeting 49" });
      expect(screen.queryByRole("heading", { name: "Meeting 50" })).not.toBeInTheDocument();
      expect(screen.queryByText("No older recordings.")).not.toBeInTheDocument();

      fireEvent.click(screen.getByRole("button", { name: "Load more recordings" }));

      expect(await screen.findByRole("heading", { name: "Meeting 59" })).toBeInTheDocument();
      expect(getRecordings).toHaveBeenLastCalledWith(
        expect.objectContaining({ skip: 49, limit: 51 }),
      );
      expect(screen.getAllByRole("heading", { name: /^Meeting \d+$/ })).toHaveLength(60);
      expect(screen.getByText("No older recordings.")).toBeInTheDocument();
      expect(screen.getByText("10 more recordings loaded")).toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: "Load more recordings" }),
      ).not.toBeInTheDocument();
    });

    it("moves keyboard focus from the button to the first recording it loaded", async () => {
      serveLibrary();
      renderWithProviders(<Sidebar />);
      await screen.findByRole("heading", { name: "Meeting 49" });

      const button = screen.getByRole("button", { name: "Load more recordings" });
      button.focus();
      fireEvent.click(button);

      const first = (await screen.findByRole("heading", { name: "Meeting 50" })).closest("a");
      await waitFor(() => expect(first).toHaveFocus());
    });

    it("keeps a rename made in place across the pages it has loaded", async () => {
      serveLibrary();
      renderWithProviders(<Sidebar />);
      await screen.findByRole("heading", { name: "Meeting 49" });
      fireEvent.click(screen.getByRole("button", { name: "Load more recordings" }));
      await screen.findByRole("heading", { name: "Meeting 59" });
      const calls = getRecordings.mock.calls.length;

      act(() => {
        fireEvent(window, new CustomEvent("recording-updated", { detail: { id: "rec-55", name: "Renamed old meeting" } }));
      });

      expect(screen.getByRole("heading", { name: "Renamed old meeting" })).toBeInTheDocument();
      expect(screen.getAllByRole("heading", { name: /meeting/i })).toHaveLength(60);
      expect(getRecordings).toHaveBeenCalledTimes(calls);
    });

    it("re-reads every loaded page when a recording changes elsewhere", async () => {
      serveLibrary();
      renderWithProviders(<Sidebar />);
      await screen.findByRole("heading", { name: "Meeting 49" });
      fireEvent.click(screen.getByRole("button", { name: "Load more recordings" }));
      await screen.findByRole("heading", { name: "Meeting 59" });

      fireEvent(window, new CustomEvent("recording-updated", { detail: {} }));

      await waitFor(() =>
        expect(getRecordings).toHaveBeenLastCalledWith(expect.objectContaining({ skip: 0, limit: 100 })),
      );
      expect(screen.getAllByRole("heading", { name: /^Meeting \d+$/ })).toHaveLength(60);
    });
  });
});
