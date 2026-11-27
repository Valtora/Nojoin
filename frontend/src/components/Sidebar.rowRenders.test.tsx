import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fireEvent, renderWithProviders, screen, waitFor } from "@/test/renderWithProviders";
import { useNavigationStore } from "@/lib/store";
import { RecordingStatus, type Recording } from "@/types";
import Sidebar from "./Sidebar";

/**
 * Which meeting cards draw again when the list changes (issue #907), at a
 * library of 1,000. The list, selection store, paging and poll are the real
 * ones; only the API is mocked, and it answers with fresh copies, as JSON
 * off the network would.
 *
 * A card's attention badge draws whenever its card does, so the probe below
 * counts card renders by recording id without touching the card itself.
 */

const LIBRARY_SIZE = 1000;
const PAGE = 50;

const cardRenders: string[] = [];

vi.mock("./RecordingAttentionBadge", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./RecordingAttentionBadge")>();
  const Real = actual.default;
  return {
    default: (props: React.ComponentProps<typeof Real>) => {
      cardRenders.push(props.recordingId);
      return <Real {...props} />;
    },
  };
});

const getRecordings = vi.fn();

vi.mock("next/link", () => ({
  default: ({ children, href, ...props }: React.ComponentPropsWithoutRef<"a">) => (
    <a href={typeof href === "string" ? href : "#"} {...props}>
      {children}
    </a>
  ),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => "/recordings",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

vi.mock("@/lib/api", () => ({
  getRecordings: (...a: unknown[]) => getRecordings(...a),
  getGlobalSpeakers: () => Promise.resolve([]),
  getTags: () => Promise.resolve([]),
  getRecordingsCalendar: () => Promise.resolve({ month: "2026-05", day_counts: [] }),
}));

vi.mock("@/lib/notes/useNoteOutbox", () => ({ useUnsentRecordings: () => [] }));
vi.mock("@/lib/notes/deletedRecordingNotes", () => ({
  useForgetNotesOfDeletedRecordings: () => undefined,
}));
const noAttention: Recording[] = [];
const refreshPausedRecording = async () => null;
vi.mock("@/lib/capture/CaptureProvider", () => ({
  useCapture: () => ({
    recordingId: null,
    pausedRecording: null,
    attentionRecordings: noAttention,
    runtimeActive: false,
    refreshPausedRecording,
  }),
}));
vi.mock("@/lib/timezone", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/timezone")>();
  return {
    ...actual,
    getUserTimeZone: async () => actual.setCachedUserTimeZone("Europe/London"),
  };
});
vi.mock("./MeetingControls", () => ({ default: () => null }));
vi.mock("./MonthCalendar", () => ({ default: () => null }));
vi.mock("./RecordingInfoModal", () => ({ default: () => null }));

/** Newest first, a minute apart, as the server orders them. */
const buildLibrary = (): Recording[] =>
  Array.from({ length: LIBRARY_SIZE }, (_, index) => {
    const created = new Date(Date.UTC(2026, 4, 25, 12) - index * 60_000).toISOString();
    return {
      id: `rec-${index}`,
      created_at: created,
      updated_at: created,
      name: `Meeting ${index}`,
      meeting_uid: `meeting-${index}`,
      audio_path: `/tmp/${index}.wav`,
      duration_seconds: 360,
      status: RecordingStatus.PROCESSED,
      is_archived: false,
      is_deleted: false,
      tags: [],
      speakers: [],
    };
  });

let library: Recording[] = [];

const cardCount = (container: HTMLElement) =>
  container.querySelectorAll("[data-recording-link]").length;

const loadMoreButton = () => screen.getByRole("button", { name: "Load more recordings" });

/**
 * Waits for `count` cards, then for the effects of the render that drew them:
 * the list reads its rows back in one, and a person's next press can't come
 * before it runs.
 */
const showCards = async (container: HTMLElement, count: number) => {
  await waitFor(() => expect(cardCount(container)).toBe(count), { timeout: 10_000 });
  await act(async () => {});
};

/** Mounts the list and pages it down to `rows` cards. */
const showRows = async (rows: number) => {
  const view = renderWithProviders(<Sidebar />);
  await showCards(view.container, PAGE);
  for (let shown = PAGE; shown < rows; shown += PAGE) {
    fireEvent.click(loadMoreButton());
    await showCards(view.container, shown + PAGE);
  }
  return view;
};

const rendered = () => [...new Set(cardRenders)].sort();
const ids = (from: number, to: number) =>
  Array.from({ length: to - from }, (_, offset) => `rec-${from + offset}`).sort();

const checkboxOf = (container: HTMLElement, name: string) =>
  container.querySelector<HTMLInputElement>(`input[aria-label="Select ${name}"]`)!;

describe("meeting list card renders at 1,000 cards", () => {
  beforeEach(() => {
    library = buildLibrary();
    getRecordings.mockReset();
    getRecordings.mockImplementation(async (filters: { skip?: number; limit?: number } = {}) => {
      const skip = filters.skip ?? 0;
      return structuredClone(library.slice(skip, skip + (filters.limit ?? 100)));
    });
    useNavigationStore.setState({
      currentView: "recordings",
      selectedRecordingIds: [],
      selectionAnchorId: null,
      selectedTagIds: [],
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("draws only the 50 new cards when the next page loads", async () => {
    const { container } = await showRows(LIBRARY_SIZE - PAGE);

    cardRenders.length = 0;
    fireEvent.click(loadMoreButton());
    await waitFor(() => expect(cardCount(container)).toBe(LIBRARY_SIZE), { timeout: 10_000 });

    expect(rendered()).toEqual(ids(LIBRARY_SIZE - PAGE, LIBRARY_SIZE));
  }, 60_000);

  it("draws no card on a search keystroke before the search is sent", async () => {
    await showRows(LIBRARY_SIZE);

    cardRenders.length = 0;
    fireEvent.change(screen.getByRole("textbox", { name: "Search recordings" }), {
      target: { value: "b" },
    });

    expect(screen.getByRole("textbox", { name: "Search recordings" })).toHaveValue("b");
    expect(cardRenders).toEqual([]);
  }, 60_000);

  it("draws only the card a poll finds changed", async () => {
    // The list's own 15 s poll, run now rather than waited for.
    const intervals = vi.spyOn(globalThis, "setInterval");
    await showRows(LIBRARY_SIZE);
    const poll = intervals.mock.calls.findLast(([, ms]) => ms === 15_000)?.[0];
    expect(poll).toBeTypeOf("function");
    const polls = getRecordings.mock.calls.length;

    library[3] = { ...library[3], name: "Renamed elsewhere" };
    cardRenders.length = 0;
    await act(async () => {
      (poll as () => void)();
    });
    await screen.findByRole("heading", { name: "Renamed elsewhere" });

    // The poll re-read the top of the list, and only the changed card drew.
    expect(getRecordings.mock.calls.length).toBeGreaterThan(polls);
    expect(getRecordings).toHaveBeenLastCalledWith(
      expect.objectContaining({ skip: 0, limit: 100 }),
    );
    expect(rendered()).toEqual(["rec-3"]);
  }, 60_000);

  it("draws only the changed card as its first tags appear, grow and disappear", async () => {
    const intervals = vi.spyOn(globalThis, "setInterval");
    const { container } = await showRows(LIBRARY_SIZE);
    const poll = intervals.mock.calls.findLast(([, ms]) => ms === 15_000)?.[0];
    expect(poll).toBeTypeOf("function");
    const row = container.querySelector<HTMLElement>('[data-recording-link="rec-3"]')!;

    for (const [count, padding, touchPadding] of [
      [1, "pr-11.5", "pointer-coarse:pr-[62px]"],
      [6, "pr-11.5", "pointer-coarse:pr-[62px]"],
      [7, "pr-14", "pointer-coarse:pr-[72px]"],
      [12, "pr-14", "pointer-coarse:pr-[72px]"],
      [13, "pr-17.5", "pointer-coarse:pr-[86px]"],
      [0, "pr-9", "pointer-coarse:pr-[52px]"],
    ] as const) {
      library[3] = {
        ...library[3],
        tags: Array.from({ length: count }, (_, index) => ({
          id: index + 1,
          name: `Topic ${index + 1}`,
          color: "sky",
          created_at: library[3].created_at,
          updated_at: library[3].updated_at,
        })),
      };
      cardRenders.length = 0;
      await act(async () => {
        (poll as () => void)();
      });
      await waitFor(() => {
        expect(row.querySelectorAll("[data-tag-dot]")).toHaveLength(count > 12 ? 11 : count);
        expect(row).toHaveClass(padding, touchPadding);
      });

      expect(rendered()).toEqual(["rec-3"]);
      expect(row).toHaveClass("pl-9", "pointer-coarse:pl-3");
      if (count > 12) expect(row).toHaveTextContent("+2");
      expect(container.querySelector('[data-recording-link="rec-4"]')).toHaveClass(
        "pr-9", "pointer-coarse:pr-[52px]", "pl-9", "pointer-coarse:pl-3",
      );
    }
  }, 60_000);

  it("draws only the card whose selection changed", async () => {
    const { container } = await showRows(LIBRARY_SIZE);
    // The first selection shows every card's checkbox, so every card draws.
    fireEvent.click(checkboxOf(container, "Meeting 10"));
    expect(checkboxOf(container, "Meeting 10").checked).toBe(true);

    cardRenders.length = 0;
    fireEvent.click(checkboxOf(container, "Meeting 500"));

    expect(checkboxOf(container, "Meeting 500").checked).toBe(true);
    expect(rendered()).toEqual(["rec-500"]);
  }, 60_000);

  it("draws no other card while a meeting is renamed, or when its menu opens", async () => {
    const { container } = await showRows(LIBRARY_SIZE);

    cardRenders.length = 0;
    fireEvent.doubleClick(screen.getByRole("heading", { name: "Meeting 7" }));
    const input = container.querySelector<HTMLInputElement>("li input[type='text']")!;
    fireEvent.change(input, { target: { value: "Meeting 7, renamed" } });
    fireEvent.change(input, { target: { value: "Meeting 7, renamed again" } });

    expect(input).toHaveValue("Meeting 7, renamed again");
    // The card being renamed shows its input, not its badge.
    expect(cardRenders).toEqual([]);

    fireEvent.keyDown(input, { key: "Escape" });
    await screen.findByRole("heading", { name: "Meeting 7" });
    cardRenders.length = 0;
    fireEvent.click(screen.getByRole("button", { name: "Actions for Meeting 20" }));

    expect(screen.getByRole("button", { name: "Actions for Meeting 20" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    expect(rendered()).toEqual(["rec-20"]);
  }, 60_000);
});
