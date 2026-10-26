import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  fireEvent,
  renderWithProviders,
  screen,
  waitFor,
  within,
} from "@/test/renderWithProviders";
import { useNavigationStore, type ViewType } from "@/lib/store";
import { useNotificationStore } from "@/lib/notificationStore";
import { RecordingStatus, type Recording } from "@/types";
import Sidebar from "./Sidebar";

/**
 * The recordings list's multi-select (issue #737), driven through the real
 * selection store, action hook and batch bar. The API is mocked.
 */

const routerPush = vi.fn();
let pathname = "/recordings";

const api = {
  getRecordings: vi.fn(),
  batchArchiveRecordings: vi.fn(),
  batchRestoreRecordings: vi.fn(),
  batchSoftDeleteRecordings: vi.fn(),
  batchPermanentlyDeleteRecordings: vi.fn(),
  batchAddTagToRecordings: vi.fn(),
  batchRemoveTagFromRecordings: vi.fn(),
};

vi.mock("next/link", () => ({
  default: ({ children, href, ...props }: React.ComponentPropsWithoutRef<"a">) => (
    <a href={typeof href === "string" ? href : "#"} {...props}>
      {children}
    </a>
  ),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => pathname,
  useRouter: () => ({ push: routerPush, replace: vi.fn() }),
}));

vi.mock("@/lib/api", () => ({
  getRecordings: (...a: unknown[]) => api.getRecordings(...a),
  getGlobalSpeakers: () => Promise.resolve([]),
  getTags: () => Promise.resolve([]),
  getRecordingsCalendar: () => Promise.resolve({ month: "2026-05", day_counts: [] }),
  renameRecording: vi.fn(),
  inferSpeakers: vi.fn(),
  deleteRecording: vi.fn(),
  discardRecordingCapture: vi.fn(),
  exportAudio: vi.fn(),
  setRecordingTranscriptionLanguage: vi.fn(),
  reprocessRecording: vi.fn(),
  createTag: vi.fn(),
  batchArchiveRecordings: (...a: unknown[]) => api.batchArchiveRecordings(...a),
  batchRestoreRecordings: (...a: unknown[]) => api.batchRestoreRecordings(...a),
  batchSoftDeleteRecordings: (...a: unknown[]) => api.batchSoftDeleteRecordings(...a),
  batchPermanentlyDeleteRecordings: (...a: unknown[]) =>
    api.batchPermanentlyDeleteRecordings(...a),
  batchAddTagToRecordings: (...a: unknown[]) => api.batchAddTagToRecordings(...a),
  batchRemoveTagFromRecordings: (...a: unknown[]) =>
    api.batchRemoveTagFromRecordings(...a),
}));

vi.mock("@/lib/notes/useNoteOutbox", () => ({ useUnsentRecordings: () => [] }));
vi.mock("@/lib/notes/deletedRecordingNotes", () => ({
  useForgetNotesOfDeletedRecordings: () => undefined,
}));
vi.mock("@/lib/notes/noteOutboxes", () => ({ forgetRecordingNotes: vi.fn() }));
vi.mock("@/lib/capture/CaptureProvider", () => ({
  useCapture: () => ({
    cancel: vi.fn(),
    recordingId: null,
    pausedRecording: null,
    runtimeActive: false,
  }),
}));
vi.mock("@/lib/timezone", () => ({
  getUserTimeZone: () => Promise.resolve("Europe/London"),
  localDayRangeToUtc: () => ({ startISO: "", endISO: "" }),
}));
// Search matches on the name, so a query hides the rows it does not name.
vi.mock("@/lib/searchUtils", () => ({
  createFuse: (items: Recording[]) => ({
    search: (query: string) =>
      items
        .filter((item) => item.name.toLowerCase().includes(query.toLowerCase()))
        .map((item) => ({ item })),
  }),
}));
vi.mock("./MeetingControls", () => ({ default: () => null }));
vi.mock("./MonthCalendar", () => ({ default: () => null }));
vi.mock("./RecordingInfoModal", () => ({ default: () => null }));
vi.mock("./BatchTagModal", () => ({ default: () => null }));
vi.mock("./ContextMenu", () => ({
  default: ({ items }: { items: { label: string; onClick: () => void }[] }) => (
    <div role="menu">
      {items.map((item) => (
        <button key={item.label} type="button" role="menuitem" onClick={item.onClick}>
          {item.label}
        </button>
      ))}
    </div>
  ),
}));
vi.mock("./ConfirmationModal", () => ({
  default: ({
    isOpen,
    title,
    confirmText,
    onConfirm,
    onClose,
  }: {
    isOpen: boolean;
    title: string;
    confirmText?: string;
    onConfirm: () => void;
    onClose: () => void;
  }) =>
    isOpen ? (
      <div role="dialog" aria-label={title}>
        <button
          type="button"
          onClick={() => {
            onConfirm();
            onClose();
          }}
        >
          {confirmText ?? "Confirm"}
        </button>
      </div>
    ) : null,
}));

const meeting = (id: string, name: string, extra: Partial<Recording> = {}): Recording => ({
  id,
  created_at: "2026-05-25T11:54:00Z",
  updated_at: "2026-05-25T11:54:00Z",
  name,
  meeting_uid: `meeting-${id}`,
  audio_path: `/tmp/${id}.wav`,
  duration_seconds: 360,
  status: RecordingStatus.PROCESSED,
  is_archived: false,
  is_deleted: false,
  tags: [],
  speakers: [],
  ...extra,
});

const ALPHA = meeting("a", "Alpha review");
const BRAVO = meeting("b", "Bravo planning");
const CHARLIE = meeting("c", "Charlie retro");
const DELTA = meeting("d", "Delta kickoff");

const checkbox = (name: string) =>
  screen.getByRole("checkbox", { name: `Select ${name}` }) as HTMLInputElement;
const row = (name: string) => screen.getByRole("link", { name: new RegExp(name) });
// The rows' checkboxes are named "Select <name>"; the header's is "Select all".
const selected = () =>
  screen
    .queryAllByRole("checkbox")
    .filter((box) => box.getAttribute("aria-label")?.startsWith("Select "))
    .filter((box) => (box as HTMLInputElement).checked)
    .map((box) => box.getAttribute("aria-label")?.replace("Select ", ""));
const selectAll = () =>
  screen.getByRole("checkbox", { name: "Select all" }) as HTMLInputElement;

const showList = async (view: ViewType, recordings: Recording[]) => {
  useNavigationStore.setState({ currentView: view });
  api.getRecordings.mockResolvedValue(recordings);
  renderWithProviders(<Sidebar />);
  await screen.findByRole("heading", { name: recordings[0].name });
};

describe("recordings list selection", () => {
  beforeEach(() => {
    pathname = "/recordings";
    routerPush.mockReset();
    Object.values(api).forEach((fn) => fn.mockReset());
    useNavigationStore.setState({
      currentView: "recordings",
      selectedTagIds: [],
      selectedRecordingIds: [],
      selectionAnchorId: null,
    });
    useNotificationStore.setState({ activeNotifications: [], history: [] });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("starts from a row checkbox, then a click toggles a row instead of opening it", async () => {
    await showList("recordings", [ALPHA, BRAVO, CHARLIE]);

    fireEvent.click(checkbox("Alpha review"));
    expect(screen.getByRole("group", { name: "Selected recordings" })).toHaveTextContent(
      "1 selected",
    );

    // fireEvent returns false when the default (following the link) was prevented.
    expect(fireEvent.click(row("Bravo planning"))).toBe(false);
    expect(fireEvent.click(row("Alpha review"))).toBe(false);

    expect(selected()).toEqual(["Bravo planning"]);
    expect(screen.getByRole("status")).toHaveTextContent("1 recording selected");
  });

  it("opens a recording on a plain click while nothing is selected", async () => {
    await showList("recordings", [ALPHA, BRAVO]);

    expect(fireEvent.click(row("Alpha review"))).toBe(true);
    expect(selected()).toEqual([]);
  });

  it("toggles with Space, extends with Shift and clears with Escape", async () => {
    await showList("recordings", [ALPHA, BRAVO, CHARLIE, DELTA]);

    fireEvent.keyDown(row("Bravo planning"), { key: " " });
    fireEvent.keyDown(row("Delta kickoff"), { key: " ", shiftKey: true });
    expect(selected()).toEqual(["Bravo planning", "Charlie retro", "Delta kickoff"]);

    fireEvent.click(row("Alpha review"), { ctrlKey: true });
    expect(selected()).toHaveLength(4);

    fireEvent.keyDown(row("Charlie retro"), { key: "Escape" });
    expect(selected()).toEqual([]);
    expect(screen.queryByRole("group", { name: "Selected recordings" })).toBeNull();
  });

  it("extends from the same recording after a refresh moves it", async () => {
    await showList("recordings", [ALPHA, BRAVO, CHARLIE]);
    fireEvent.click(checkbox("Bravo planning"));

    // A new meeting arrives at the top: every row moves down one place.
    api.getRecordings.mockResolvedValue([DELTA, ALPHA, BRAVO, CHARLIE]);
    fireEvent(window, new CustomEvent("recording-updated", { detail: {} }));
    await screen.findByRole("heading", { name: "Delta kickoff" });

    fireEvent.click(row("Delta kickoff"), { shiftKey: true });

    expect(selected()).toEqual(["Delta kickoff", "Alpha review", "Bravo planning"]);
  });

  it("selects every row shown from the header checkbox, which shows a partial state", async () => {
    await showList("archived", [
      { ...ALPHA, is_archived: true },
      { ...BRAVO, is_archived: true },
    ]);

    fireEvent.click(checkbox("Alpha review"));
    expect(selectAll().indeterminate).toBe(true);
    expect(selectAll().checked).toBe(false);

    fireEvent.click(selectAll());
    expect(selected()).toEqual(["Alpha review", "Bravo planning"]);
    expect(selectAll().indeterminate).toBe(false);
    expect(selectAll().checked).toBe(true);

    fireEvent.click(selectAll());
    expect(selected()).toEqual([]);
  });

  it("drops rows a search hides from the selection, and does not bring them back", async () => {
    await showList("recordings", [ALPHA, BRAVO]);
    fireEvent.click(checkbox("Alpha review"));
    fireEvent.click(checkbox("Bravo planning"));

    fireEvent.change(screen.getByPlaceholderText("Search..."), {
      target: { value: "alpha" },
    });
    await waitFor(() =>
      expect(screen.queryByRole("heading", { name: "Bravo planning" })).toBeNull(),
    );
    expect(selected()).toEqual(["Alpha review"]);

    fireEvent.change(screen.getByPlaceholderText("Search..."), {
      target: { value: "" },
    });
    await screen.findByRole("heading", { name: "Bravo planning" });
    expect(selected()).toEqual(["Alpha review"]);
  });

  it("never deletes for good a meeting restored from its own menu while selected", async () => {
    const binned = [
      { ...ALPHA, is_deleted: true },
      { ...BRAVO, is_deleted: true },
    ];
    await showList("deleted", binned);
    api.batchRestoreRecordings.mockResolvedValue({
      count: 1,
      skipped: 0,
      recording_ids: ["a"],
    });
    api.batchPermanentlyDeleteRecordings.mockResolvedValue({
      count: 1,
      skipped: 0,
      recording_ids: ["b"],
    });
    fireEvent.click(checkbox("Alpha review"));
    fireEvent.click(checkbox("Bravo planning"));

    fireEvent.contextMenu(row("Alpha review"));
    fireEvent.click(screen.getByRole("menuitem", { name: "Restore" }));
    expect(api.batchRestoreRecordings).toHaveBeenCalledWith(["a"]);
    expect(screen.queryByRole("heading", { name: "Alpha review" })).toBeNull();
    expect(selected()).toEqual(["Bravo planning"]);

    // The list reloads after the restore without Alpha, as the server has it.
    api.getRecordings.mockResolvedValue([binned[1]]);
    fireEvent.click(
      within(screen.getByRole("group", { name: "Selected recordings" })).getByRole(
        "button",
        { name: "Delete Permanently" },
      ),
    );
    fireEvent.click(
      within(
        screen.getByRole("dialog", { name: "Delete this recording permanently?" }),
      ).getByRole("button", { name: "Delete Permanently" }),
    );

    await waitFor(() =>
      expect(api.batchPermanentlyDeleteRecordings).toHaveBeenCalledTimes(1),
    );
    expect(api.batchPermanentlyDeleteRecordings).toHaveBeenCalledWith(["b"]);
  });

  it("leaves the open recording's page when the bar moves it to the bin", async () => {
    pathname = "/recordings/a";
    await showList("recordings", [ALPHA, BRAVO]);
    api.batchSoftDeleteRecordings.mockResolvedValue({
      count: 2,
      skipped: 0,
      recording_ids: ["a", "b"],
    });
    api.getRecordings.mockResolvedValue([]);

    fireEvent.click(checkbox("Alpha review"));
    fireEvent.click(checkbox("Bravo planning"));
    fireEvent.click(screen.getByRole("button", { name: "Move to Bin" }));

    await waitFor(() => expect(routerPush).toHaveBeenCalledWith("/recordings"));
    expect(api.batchSoftDeleteRecordings).toHaveBeenCalledWith(["a", "b"]);
    expect(useNotificationStore.getState().activeNotifications.at(-1)?.message).toBe(
      "Moved 2 recordings to the bin.",
    );
  });

  it("selects on a touch long-press rather than opening the row or its menu", async () => {
    await showList("recordings", [ALPHA, BRAVO]);
    vi.useFakeTimers();

    fireEvent.pointerDown(row("Alpha review"), { pointerType: "touch" });
    act(() => {
      vi.advanceTimersByTime(500);
    });
    expect(fireEvent.click(row("Alpha review"))).toBe(false);
    expect(selected()).toEqual(["Alpha review"]);

    // Android raises contextmenu for a held touch: it selects, with no menu.
    fireEvent.pointerDown(row("Bravo planning"), { pointerType: "touch" });
    fireEvent.contextMenu(row("Bravo planning"));
    expect(screen.queryByRole("menu")).toBeNull();
    expect(selected()).toEqual(["Alpha review", "Bravo planning"]);
  });

  it("names the clear button and returns focus to the list when it closes the bar", async () => {
    await showList("recordings", [ALPHA, BRAVO]);
    fireEvent.click(checkbox("Bravo planning"));

    const clear = screen.getByRole("button", { name: "Clear selection" });
    clear.focus();
    fireEvent.click(clear);

    expect(selected()).toEqual([]);
    expect(document.activeElement).toBe(row("Bravo planning"));
  });

  it("marks each row's selection state for assistive technology", async () => {
    await showList("recordings", [ALPHA, BRAVO]);

    fireEvent.click(checkbox("Alpha review"));

    expect(checkbox("Alpha review")).toBeChecked();
    expect(checkbox("Bravo planning")).not.toBeChecked();
    expect(screen.getByRole("list", { name: "Recordings list" })).toBeInTheDocument();
  });
});
