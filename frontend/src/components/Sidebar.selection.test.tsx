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
    attentionRecordings: [],
    runtimeActive: false,
    refreshPausedRecording,
  }),
}));
const refreshPausedRecording = async () => null;
vi.mock("@/lib/timezone", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/timezone")>();
  return {
    ...actual,
    getUserTimeZone: async () => actual.setCachedUserTimeZone("Europe/London"),
    localDayRangeToUtc: () => ({ startISO: "", endISO: "" }),
  };
});
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
interface MenuProps {
  items: { label: string; onClick: () => void }[];
  label?: string;
  anchor?: HTMLElement | null;
  returnFocusTo?: HTMLElement | null;
}
let lastMenu: MenuProps | null = null;
vi.mock("./ContextMenu", () => ({
  default: (props: MenuProps) => {
    lastMenu = props;
    return (
      <div role="menu" aria-label={props.label}>
        {props.items.map((item) => (
          <button key={item.label} type="button" role="menuitem" onClick={item.onClick}>
            {item.label}
          </button>
        ))}
      </div>
    );
  },
}));
let lastConfirmFocus: React.RefObject<HTMLElement | null> | undefined;
vi.mock("./ConfirmationModal", () => ({
  default: ({
    isOpen,
    title,
    confirmText,
    onConfirm,
    onClose,
    returnFocus,
  }: {
    isOpen: boolean;
    title: string;
    confirmText?: string;
    onConfirm: () => void;
    onClose: () => void;
    returnFocus?: React.RefObject<HTMLElement | null>;
  }) => {
    lastConfirmFocus = returnFocus;
    return isOpen ? (
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
        <button type="button" onClick={onClose}>
          Cancel
        </button>
      </div>
    ) : null;
  },
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

  // The dialog puts focus back on whatever opened it as it closes, which a
  // permanent delete has just taken away.
  it("hands focus to the next row once a permanent delete is confirmed, and not on Cancel", async () => {
    const binned = [ALPHA, BRAVO, CHARLIE].map((r) => ({ ...r, is_deleted: true }));
    await showList("deleted", binned);
    api.batchPermanentlyDeleteRecordings.mockResolvedValue({
      count: 1,
      skipped: 0,
      recording_ids: ["b"],
    });
    const bar = () => screen.getByRole("group", { name: "Selected recordings" });
    const dialog = () => screen.getByRole("dialog", { name: "Delete this recording permanently?" });

    fireEvent.click(checkbox("Bravo planning"));
    fireEvent.click(within(bar()).getByRole("button", { name: "Delete Permanently" }));
    fireEvent.click(within(dialog()).getByRole("button", { name: "Cancel" }));
    expect(lastConfirmFocus?.current ?? null).toBeNull();

    fireEvent.click(within(bar()).getByRole("button", { name: "Delete Permanently" }));
    const next = row("Charlie retro");
    fireEvent.click(within(dialog()).getByRole("button", { name: "Delete Permanently" }));

    expect(lastConfirmFocus?.current).toBe(next);
    await waitFor(() => expect(api.batchPermanentlyDeleteRecordings).toHaveBeenCalledWith(["b"]));
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

  // jsdom has no layout, so this pins the CSS contract instead. The status
  // line is sr-only, which is absolutely positioned. If its containing block
  // is outside the list's scroller, it sits at its static position below the
  // whole list, and that height becomes scrollable overflow on the app shell,
  // which the chat's auto-scroll then scrolled.
  it("keeps the status line's containing block inside the list's scroller", async () => {
    await showList("recordings", [ALPHA, BRAVO]);

    const nearest = (node: Element, classes: string[]) => {
      let el = node.parentElement;
      while (el && !classes.some((name) => el!.classList.contains(name))) {
        el = el.parentElement;
      }
      return el;
    };
    const status = screen.getByRole("status");
    const scroller = nearest(status, ["overflow-y-auto", "overflow-auto"]);
    const containingBlock = nearest(status, ["relative", "absolute", "fixed", "sticky"]);

    expect(scroller).not.toBeNull();
    expect(containingBlock).not.toBeNull();
    expect(scroller!.contains(containingBlock)).toBe(true);
  });

  it("marks each row's selection state for assistive technology", async () => {
    await showList("recordings", [ALPHA, BRAVO]);

    fireEvent.click(checkbox("Alpha review"));

    expect(checkbox("Alpha review")).toBeChecked();
    expect(checkbox("Bravo planning")).not.toBeChecked();
    expect(screen.getByRole("list", { name: "Recordings list" })).toBeInTheDocument();
  });

  // A long press selects on touch, so the row menu needs a way in there too.
  describe("the row's actions button", () => {
    const actionsButton = (name: string) =>
      screen.getByRole("button", { name: `Actions for ${name}` });
    const menuLabels = () =>
      within(screen.getByRole("menu"))
        .getAllByRole("menuitem")
        .map((item) => item.textContent);

    beforeEach(() => {
      lastMenu = null;
    });

    it.each([
      ["recordings", ALPHA],
      ["archived", { ...ALPHA, is_archived: true }],
      ["deleted", { ...ALPHA, is_deleted: true }],
    ] as const)(
      "opens the row menu right-click opens, in %s, against the button",
      async (view, recording) => {
        await showList(view, [recording, BRAVO]);
        fireEvent.contextMenu(row("Alpha review"));
        const rightClicked = menuLabels();
        // Right-click opens at the pointer, and gives focus back to the row.
        expect(lastMenu?.anchor ?? null).toBeNull();
        expect(lastMenu?.returnFocusTo).toBe(row("Alpha review"));

        const button = actionsButton("Alpha review");
        fireEvent.click(button);

        expect(screen.getByRole("menu", { name: "Actions for Alpha review" })).toBeInTheDocument();
        expect(menuLabels()).toEqual(rightClicked);
        expect(lastMenu?.anchor).toBe(button);
        expect(lastMenu?.returnFocusTo).toBe(button);
      },
    );

    it("is named for its row and says it opens a menu", async () => {
      await showList("recordings", [ALPHA, BRAVO]);

      const button = actionsButton("Bravo planning");
      expect(button).toHaveAttribute("aria-haspopup", "menu");
      expect(button).toHaveAttribute("aria-expanded", "false");

      fireEvent.click(button);
      expect(button).toHaveAttribute("aria-expanded", "true");

      // A second press closes it again.
      fireEvent.click(button);
      expect(button).toHaveAttribute("aria-expanded", "false");
      expect(screen.queryByRole("menu")).toBeNull();
    });

    it("opens from the keyboard with the down arrow", async () => {
      await showList("recordings", [ALPHA, BRAVO]);

      fireEvent.keyDown(actionsButton("Alpha review"), { key: "ArrowDown" });

      expect(screen.getByRole("menu", { name: "Actions for Alpha review" })).toBeInTheDocument();
    });

    it("never selects the row or opens the recording, and acts on its own row while selecting", async () => {
      await showList("recordings", [ALPHA, BRAVO, CHARLIE]);
      api.batchSoftDeleteRecordings.mockResolvedValue({
        count: 1,
        skipped: 0,
        recording_ids: ["b"],
      });

      // Nothing selected: the press opens the menu and nothing else.
      expect(fireEvent.click(actionsButton("Alpha review"))).toBe(true);
      expect(selected()).toEqual([]);
      fireEvent.keyDown(actionsButton("Alpha review"), { key: " " });
      expect(selected()).toEqual([]);
      fireEvent.click(actionsButton("Alpha review"));

      // Selecting: a click on a row would toggle it, a click on its button does not.
      fireEvent.click(checkbox("Alpha review"));
      fireEvent.click(actionsButton("Bravo planning"));
      expect(selected()).toEqual(["Alpha review"]);
      expect(routerPush).not.toHaveBeenCalled();

      fireEvent.click(screen.getByRole("menuitem", { name: "Move to Bin" }));
      expect(api.batchSoftDeleteRecordings).toHaveBeenCalledWith(["b"]);
      expect(selected()).toEqual(["Alpha review"]);
    });

    it("does not start a selection on a touch held on it", async () => {
      await showList("recordings", [ALPHA, BRAVO]);
      vi.useFakeTimers();

      fireEvent.pointerDown(actionsButton("Alpha review"), { pointerType: "touch" });
      act(() => {
        vi.advanceTimersByTime(600);
      });

      expect(selected()).toEqual([]);
    });

    it("shows on touch always, and on desktop on hover or focus in the row", async () => {
      await showList("recordings", [ALPHA]);

      const button = actionsButton("Alpha review");
      expect(button).toHaveClass(
        "opacity-0",
        "group-hover:opacity-100",
        "group-focus-within:opacity-100",
        "pointer-coarse:opacity-100",
        // The full touch target on a touch screen.
        "pointer-coarse:h-[44px]",
        "pointer-coarse:w-[44px]",
      );

      fireEvent.click(button);
      expect(button).toHaveClass("opacity-100");
      expect(button).not.toHaveClass("opacity-0");
    });
  });
});
