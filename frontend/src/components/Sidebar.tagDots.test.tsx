import type { ComponentPropsWithoutRef } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setCachedUserTimeZone } from "@/lib/timezone";
import {
  act,
  fireEvent,
  renderWithProviders,
  screen,
  waitFor,
  within,
} from "@/test/renderWithProviders";
import { useNavigationStore } from "@/lib/store";
import { dispatchRecordingRenamed } from "@/lib/recordingEvents";
import { RecordingStatus, type Recording, type Tag } from "@/types";
import Sidebar from "./Sidebar";

const getRecordings = vi.hoisted(() => vi.fn());

vi.mock("next/link", () => ({
  default: ({ children, ...props }: ComponentPropsWithoutRef<"a">) => <a {...props}>{children}</a>,
}));

vi.mock("next/navigation", () => ({
  usePathname: () => "/recordings/board",
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
  useCapture: () => ({
    cancel: vi.fn(), recordingId: null, pausedRecording: null, runtimeActive: false, attentionRecordings: [],
    refreshPausedRecording,
  }),
}));
const refreshPausedRecording = async () => null;
vi.mock("./MeetingControls", () => ({ default: () => null }));
vi.mock("./MonthCalendar", () => ({ default: () => null }));

const tag = (id: number, name: string, color: string): Tag => ({
  id,
  name,
  color,
  created_at: "2026-10-01T09:00:00Z",
  updated_at: "2026-10-01T09:00:00Z",
});

const CLIENTS = tag(1, "Clients", "teal");
const HIRING = tag(2, "Hiring", "amber");
const BOARD = tag(3, "Board", "red");
const Q4 = tag(4, "Q4", "blue");
const OFFSITE = tag(5, "Offsite", "violet");

const meeting = (id: string, name: string, tags: Tag[]): Recording => ({
  id,
  created_at: "2026-10-27T09:15:00",
  updated_at: "2026-10-27T09:15:00",
  name,
  meeting_uid: `meeting-${id}`,
  audio_path: "/audio.wav",
  duration_seconds: 600,
  status: RecordingStatus.PROCESSED,
  is_archived: false,
  is_deleted: false,
  tags,
});

const card = async (name: string) =>
  (await screen.findByRole("heading", { name })).closest("a")!;

const leftPadding = (link: HTMLElement) =>
  link.className.split(/\s+/).filter((name) => /(^|:)pl-/.test(name));

describe("tag dots on the meeting cards", () => {
  beforeEach(() => {
    setCachedUserTimeZone("UTC");
    useNavigationStore.setState({
      currentView: "recordings", selectedTagIds: [], selectedRecordingIds: [],
      isRecordingsSidebarCollapsed: false,
    });
    getRecordings.mockReset();
  });

  it("names a meeting's tags on its card and draws none on an untagged one", async () => {
    getRecordings.mockResolvedValue([
      meeting("board", "Board review with Alice Example", [CLIENTS, HIRING]),
      meeting("standup", "Daily standup", []),
    ]);
    renderWithProviders(<Sidebar />);

    const tagged = await card("Board review with Alice Example");
    expect(within(tagged).getByRole("img", { name: "Tags: Clients, Hiring" })).toBeInTheDocument();
    // The open meeting keeps its dots inside the highlighted card.
    expect(tagged).toHaveAttribute("aria-current", "page");
    // A screen reader hears the tags with the meeting's name.
    expect(tagged).toHaveAccessibleName(expect.stringContaining("Tags: Clients, Hiring"));
    expect(tagged.querySelectorAll("[data-tag-dot]")).toHaveLength(2);

    const untagged = await card("Daily standup");
    expect(within(untagged).queryByRole("img", { name: /^Tags?:/ })).not.toBeInTheDocument();
    expect(untagged.querySelectorAll("[data-tag-dot]")).toHaveLength(0);
  });

  it("caps a meeting with many tags and still names them all", async () => {
    getRecordings.mockResolvedValue([
      meeting("board", "Quarterly planning", [CLIENTS, HIRING, BOARD, Q4, OFFSITE]),
    ]);
    renderWithProviders(<Sidebar />);

    const row = await card("Quarterly planning");
    const dots = within(row).getByRole("img", {
      name: "Tags: Clients, Hiring, Board, Q4, Offsite",
    });
    expect(dots.querySelectorAll("[data-tag-dot]")).toHaveLength(2);
    expect(within(dots).getByText("+3")).toBeInTheDocument();
  });

  it("redraws the dots when a tag changes anywhere in the app", async () => {
    getRecordings.mockResolvedValue([meeting("board", "Board review", [CLIENTS])]);
    renderWithProviders(<Sidebar />);
    const row = await card("Board review");
    expect(within(row).getByRole("img", { name: "Tag: Clients" })).toBeInTheDocument();
    const calls = getRecordings.mock.calls.length;

    // The meeting page, the tag editor, the batch bar and the tag list all
    // announce a change this way.
    getRecordings.mockResolvedValue([
      meeting("board", "Board review", [CLIENTS, { ...BOARD, color: "green" }]),
    ]);
    act(() => {
      window.dispatchEvent(new CustomEvent("tags-updated"));
    });

    await waitFor(() =>
      expect(
        within(row).getByRole("img", { name: "Tags: Clients, Board" }),
      ).toBeInTheDocument(),
    );
    expect(getRecordings.mock.calls.length).toBeGreaterThan(calls);
    const [, board] = row.querySelectorAll("[data-tag-dot]");
    expect(board.className).toContain("bg-green-500");
  });

  it("keeps a meeting's dots when a rename updates its card in place", async () => {
    getRecordings.mockResolvedValue([meeting("board", "Board review", [CLIENTS, HIRING])]);
    renderWithProviders(<Sidebar />);
    await card("Board review");
    const calls = getRecordings.mock.calls.length;

    act(() => {
      dispatchRecordingRenamed("board", "Board review with Alice Example");
    });

    const renamed = await card("Board review with Alice Example");
    expect(within(renamed).getByRole("img", { name: "Tags: Clients, Hiring" })).toBeInTheDocument();
    expect(getRecordings.mock.calls.length).toBe(calls);
  });

  it("searches tag names on the server and shows the dots of what it finds", async () => {
    const board = meeting("board", "Board review", [CLIENTS, HIRING]);
    // The server matches the tag's name (backend/services/recording_search.py).
    getRecordings.mockImplementation(async (filters?: { q?: string }) =>
      filters?.q === "Hiring" ? [board] : [board, meeting("standup", "Daily standup", [BOARD])],
    );
    renderWithProviders(<Sidebar />);
    await card("Daily standup");

    fireEvent.change(screen.getByPlaceholderText("Search..."), { target: { value: "Hiring" } });

    await waitFor(() =>
      expect(screen.queryByRole("heading", { name: "Daily standup" })).not.toBeInTheDocument(),
    );
    expect(getRecordings).toHaveBeenLastCalledWith(expect.objectContaining({ q: "Hiring" }));
    const found = await card("Board review");
    expect(within(found).getByRole("img", { name: "Tags: Clients, Hiring" })).toBeInTheDocument();
  });

  it("keeps the gutter open on every card while any meeting in the list is tagged", async () => {
    getRecordings.mockResolvedValue([
      meeting("board", "Board review", [CLIENTS]),
      meeting("standup", "Daily standup", []),
    ]);
    renderWithProviders(<Sidebar />);

    expect(leftPadding(await card("Board review"))).toEqual(["pl-9"]);
    expect(leftPadding(await card("Daily standup"))).toEqual(["pl-9"]);
  });

  it("leaves an untagged list's cards as they were", async () => {
    getRecordings.mockResolvedValue([meeting("standup", "Daily standup", [])]);
    renderWithProviders(<Sidebar />);

    expect(leftPadding(await card("Daily standup"))).toEqual(["pl-3", "pointer-fine:pl-9"]);
  });
});
