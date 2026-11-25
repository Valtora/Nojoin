import type { ComponentPropsWithoutRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setCachedUserTimeZone } from "@/lib/timezone";
import { act, fireEvent, renderWithProviders, screen } from "@/test/renderWithProviders";
import { useNavigationStore } from "@/lib/store";
import { RecordingStatus, type Recording } from "@/types";
import Sidebar from "./Sidebar";

/**
 * The meeting cards in the recordings rail (#974): the list keeps its place
 * while the rail is collapsed, and a double click on a card's name renames
 * the meeting only where the list stays beside the meeting it opens.
 */

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
  getRecordingsCalendar: vi.fn().mockResolvedValue({ month: "2026-11", day_counts: [] }),
}));

vi.mock("@/lib/timezone", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/timezone")>(),
  getUserTimeZone: () => Promise.resolve("UTC"),
}));

vi.mock("@/lib/notes/useNoteOutbox", () => ({ useUnsentRecordings: () => [] }));
vi.mock("@/lib/notes/deletedRecordingNotes", () => ({ useForgetNotesOfDeletedRecordings: () => {} }));
vi.mock("@/lib/capture/CaptureProvider", () => ({
  useCapture: () => ({
    cancel: vi.fn(), recordingId: null, pausedRecording: null, runtimeActive: false, attentionRecordings: [],
    refreshPausedRecording,
  }),
}));
const refreshPausedRecording = async () => null;
vi.mock("./MeetingControls", () => ({ default: () => null }));
vi.mock("./MonthCalendar", () => ({ default: () => null }));
vi.mock("./importAudio/ImportAudioButton", () => ({ default: () => null }));

const meeting = (index: number): Recording => ({
  id: `m${index}`,
  created_at: "2026-11-20T09:15:00",
  updated_at: "2026-11-20T09:15:00",
  name: `Planning sync ${index}`,
  meeting_uid: `meeting-${index}`,
  audio_path: "/audio.wav",
  duration_seconds: 600,
  status: RecordingStatus.PROCESSED,
  is_archived: false,
  is_deleted: false,
  tags: [],
});

const MEETINGS = Array.from({ length: 30 }, (_, index) => meeting(index));

const setWidth = (width: number) =>
  Object.defineProperty(window, "innerWidth", { configurable: true, value: width });

const showList = async () => {
  renderWithProviders(<Sidebar />);
  await screen.findByRole("heading", { name: "Planning sync 0" });
};

beforeEach(() => {
  setCachedUserTimeZone("UTC");
  useNavigationStore.setState({
    currentView: "recordings",
    selectedTagIds: [],
    selectedRecordingIds: [],
    selectionAnchorId: null,
    isRecordingsSidebarCollapsed: false,
  });
  getRecordings.mockReset().mockResolvedValue(MEETINGS);
});

describe("the list's place across collapsing the rail", () => {
  // jsdom lays nothing out: the list's box starts 100px down the page, and
  // each card is CARD px tall with an 8px gap above the first.
  const LIST_TOP = 100;
  let card = 80;
  const scroller = () =>
    document.getElementById("recordings-list")!.closest<HTMLElement>(".overflow-y-auto")!;
  const box = (top: number, height: number) =>
    ({ top, bottom: top + height, left: 0, right: 320, width: 320, height, x: 0, y: top, toJSON: () => ({}) }) as DOMRect;

  let resizeCallbacks: { callback: ResizeObserverCallback; targets: Element[] }[] = [];
  const resize = (target: Element) =>
    act(() => {
      for (const { callback, targets } of resizeCallbacks) {
        if (targets.includes(target)) callback([], {} as ResizeObserver);
      }
    });

  beforeEach(() => {
    card = 80;
    resizeCallbacks = [];
    vi.stubGlobal(
      "ResizeObserver",
      class {
        private entry: { callback: ResizeObserverCallback; targets: Element[] };
        constructor(callback: ResizeObserverCallback) {
          this.entry = { callback, targets: [] };
          resizeCallbacks.push(this.entry);
        }
        observe(target: Element) {
          this.entry.targets.push(target);
        }
        unobserve() {}
        disconnect() {
          this.entry.targets = [];
        }
      },
    );
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(
      function (this: HTMLElement) {
        const list = document.getElementById("recordings-list");
        const scrolling = list?.closest<HTMLElement>(".overflow-y-auto");
        if (scrolling && this === scrolling) return box(LIST_TOP, 600);
        const id = this.dataset.recordingLink;
        if (scrolling && id) {
          const index = MEETINGS.findIndex((recording) => recording.id === id);
          return box(LIST_TOP + 8 + index * card - scrolling.scrollTop, card - 8);
        }
        return box(0, 0);
      },
    );
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  const scrollTo = (top: number) => {
    scroller().scrollTop = top;
    fireEvent.scroll(scroller());
  };

  it("brings the list back at the card that was at its top", async () => {
    await showList();
    // Card 12 at the top, its first 20px scrolled out of sight.
    scrollTo(8 + 12 * 80 + 20);

    fireEvent.click(screen.getByRole("button", { name: "Collapse recordings list" }));
    expect(document.getElementById("recordings-list")).toBeNull();

    // The list returns at the collapsed rail's width, where every card is
    // taller, and widens with the rail.
    card = 120;
    fireEvent.click(screen.getByRole("button", { name: "Expand recordings list" }));
    expect(scroller().scrollTop).toBe(8 + 12 * 120 + 20);

    card = 80;
    resize(scroller());
    expect(scroller().scrollTop).toBe(8 + 12 * 80 + 20);
  });

  it("starts at the top when a filter changed while the rail was collapsed", async () => {
    await showList();
    scrollTo(8 + 12 * 80);
    fireEvent.click(screen.getByRole("button", { name: "Collapse recordings list" }));

    act(() => {
      useNavigationStore.setState({ selectedTagIds: [7] });
    });
    fireEvent.click(screen.getByRole("button", { name: "Expand recordings list" }));
    await screen.findByRole("heading", { name: "Planning sync 0" });

    expect(scroller().scrollTop).toBe(0);
  });
});

describe("a double click on a card's name", () => {
  const { innerWidth } = window;

  afterEach(() => {
    setWidth(innerWidth);
  });

  it("on a phone, only opens the meeting: the menu renames it", async () => {
    setWidth(390);
    await showList();
    const name = screen.getByRole("heading", { name: "Planning sync 3" });
    const link = name.closest("a")!;

    // Not prevented: the link opens the meeting, which replaces the list.
    expect(fireEvent.click(link)).toBe(true);
    fireEvent.doubleClick(name);

    expect(screen.queryByDisplayValue("Planning sync 3")).toBeNull();
    expect(name).not.toHaveAttribute("title");
  });

  it("on a desktop, where the list stays beside the meeting, starts a rename", async () => {
    setWidth(1280);
    await showList();
    const name = screen.getByRole("heading", { name: "Planning sync 3" });
    expect(name).toHaveAttribute("title", "Double-click to rename");

    fireEvent.doubleClick(name);

    expect(screen.getByDisplayValue("Planning sync 3")).toHaveFocus();
  });
});
