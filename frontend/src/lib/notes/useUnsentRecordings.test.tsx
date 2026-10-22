import { act, render, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { noteSignedIn, resetSessionForTests } from "@/lib/session";
import {
  createTimelineNote,
  deleteTimelineNote,
  NO_ANSWER,
  resetNotesServer,
  server,
} from "@/test/fakeNotesServer";

import { EMPTY_DRAFT } from "./noteOutbox";
import { forgetNoteOutboxes, noteOutboxFor } from "./noteOutboxes";
import {
  MemoryNoteStore,
  NOTE_RECORD_VERSION,
  setNoteStoreForTests,
  type StoredOutbox,
} from "./noteStore";
import { useNoteOutbox, useUnsentRecordings } from "./useNoteOutbox";

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  ...(await import("@/test/fakeNotesServer")).fakeNotesApi,
}));

const ALICE = { id: 1, username: "alice", force_password_change: false };
const BOB = 2;

let store: MemoryNoteStore;

/** What a page that closed or crashed left in the browser for `recordingId`. */
const keep = async (
  recordingId: string,
  { bodies = [], boxText = "", discards = [], account = ALICE.id }: {
    bodies?: string[];
    boxText?: string;
    discards?: StoredOutbox["discards"];
    account?: number;
  },
) => {
  await store.put({
    version: NOTE_RECORD_VERSION,
    account,
    recordingId,
    notes: bodies.map((body, index) => ({
      id: `${recordingId}-note-${index}`,
      writeToken: `${recordingId}-token-${index}`,
      kind: "tail",
      body,
      anchorMs: null,
      createdAt: "2026-10-22T10:00:00Z",
    })),
    draft: boxText ? { ...EMPTY_DRAFT, text: boxText } : null,
    discards,
  });
};

/** A page that hasn't opened any recording, as `/recordings` is. */
const listPage = () => renderHook(() => useUnsentRecordings());

beforeEach(() => {
  resetNotesServer();
  resetSessionForTests();
  forgetNoteOutboxes();
  store = new MemoryNoteStore();
  setNoteStoreForTests(store);
  noteSignedIn(ALICE);
});

afterEach(() => {
  forgetNoteOutboxes();
  setNoteStoreForTests(null);
  resetSessionForTests();
});

describe("recordings with unsent notes kept in this browser", () => {
  it("marks a recording whose kept record holds a note not saved yet", async () => {
    await keep("rec-a", { bodies: ["Budget agreed"] });

    const { result } = listPage();

    await waitFor(() => expect(result.current).toEqual(["rec-a"]));
  });

  it("marks one whose record holds only text left in the box", async () => {
    await keep("rec-b", { boxText: "Half a thought" });

    const { result } = listPage();

    await waitFor(() => expect(result.current).toEqual(["rec-b"]));
  });

  it("marks one whose record holds only a discard the server has not confirmed", async () => {
    await keep("rec-c", { discards: [{ id: "rec-c-note-0", writeToken: "rec-c-token-0" }] });

    const { result } = listPage();

    await waitFor(() => expect(result.current).toEqual(["rec-c"]));
  });

  it("leaves another account's records unmarked", async () => {
    await keep("rec-bob", { bodies: ["Bob's note"], account: BOB });
    await keep("rec-a", { bodies: ["Budget agreed"] });

    const { result } = listPage();

    await waitFor(() => expect(result.current).toEqual(["rec-a"]));
  });

  it("sends nothing from a page that hasn't opened the recording", async () => {
    await keep("rec-a", { bodies: ["Budget agreed", "Ship on Friday"] });
    await keep("rec-b", { boxText: "Half a thought" });
    await keep("rec-c", { discards: [{ id: "rec-c-note-0", writeToken: "rec-c-token-0" }] });

    const { result } = listPage();
    await waitFor(() => expect(result.current).toHaveLength(3));
    // The session is confirmed again, as each in-app navigation does.
    act(() => noteSignedIn(ALICE));
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });

    expect(createTimelineNote).not.toHaveBeenCalled();
    expect(deleteTimelineNote).not.toHaveBeenCalled();
  });

  it("clears once the recording is opened and its notes are saved", async () => {
    await keep("rec-a", { bodies: ["Budget agreed"] });
    const { result } = listPage();
    await waitFor(() => expect(result.current).toEqual(["rec-a"]));

    // Opening the recording restores its record and sends the note (#553).
    act(() => {
      noteOutboxFor("rec-a");
    });

    await waitFor(() => expect(server.notes.size).toBe(1));
    await waitFor(() => expect(result.current).toEqual([]));
  });

  it("marks a recording whose text this page holds unsaved", async () => {
    const { result } = listPage();

    act(() => noteOutboxFor("rec-x").setDraft({ ...EMPTY_DRAFT, text: "Typed here" }));

    await waitFor(() => expect(result.current).toEqual(["rec-x"]));
  });

  it("keeps the mark while a discard on this page waits for the server", async () => {
    await keep("rec-c", { discards: [{ id: "rec-c-note-0", writeToken: "rec-c-token-0" }] });
    const { result } = listPage();
    await waitFor(() => expect(result.current).toEqual(["rec-c"]));
    server.failures.push(NO_ANSWER);

    act(() => {
      noteOutboxFor("rec-c");
    });
    await waitFor(() => expect(deleteTimelineNote).toHaveBeenCalled());

    await waitFor(() => expect(result.current).toEqual(["rec-c"]));
  });

  it("picks up another tab's record when this page's notes change", async () => {
    const { result } = listPage();
    await waitFor(() => expect(result.current).toEqual([]));
    await keep("rec-later", { bodies: ["Kept by another tab"] });

    act(() => noteOutboxFor("rec-x").setDraft({ ...EMPTY_DRAFT, text: "Typed here" }));

    await waitFor(() => expect(result.current).toEqual(expect.arrayContaining(["rec-later", "rec-x"])));
  });

  it("clears when the opened recording turns out to have nothing kept any more", async () => {
    await keep("rec-a", { bodies: ["Budget agreed"] });
    const { result } = listPage();
    await waitFor(() => expect(result.current).toEqual(["rec-a"]));
    // Another tab sent it meanwhile.
    await store.remove(ALICE.id, "rec-a");

    act(() => {
      noteOutboxFor("rec-a");
    });

    await waitFor(() => expect(result.current).toEqual([]));
  });

  it("takes a recording a page opens while rendering without updating the list mid-render", async () => {
    const errors = vi.spyOn(console, "error").mockImplementation(() => undefined);
    await keep("rec-a", { bodies: ["Budget agreed"] });
    const List = () => <output>{useUnsentRecordings().join(",")}</output>;
    const RecordingView = () => {
      useNoteOutbox("rec-a");
      return null;
    };
    const view = render(<List />);
    await waitFor(() => expect(view.container).toHaveTextContent("rec-a"));
    // Another tab sent it meanwhile.
    await store.remove(ALICE.id, "rec-a");

    view.rerender(
      <>
        <List />
        <RecordingView />
      </>,
    );

    await waitFor(() => expect(view.container).toHaveTextContent(/^$/));
    const renderPhaseUpdates = errors.mock.calls.filter(([message]) =>
      String(message).includes("Cannot update a component"),
    );
    errors.mockRestore();
    expect(renderPhaseUpdates).toEqual([]);
  });

  it("shows no mark from the previous account while the new one's records are read", async () => {
    await keep("rec-a", { bodies: ["Budget agreed"] });
    const read = store.recordingsOf.bind(store);
    vi.spyOn(store, "recordingsOf").mockImplementation((account) =>
      account === BOB ? new Promise(() => undefined) : read(account),
    );
    const { result } = listPage();
    await waitFor(() => expect(result.current).toEqual(["rec-a"]));

    act(() => noteSignedIn({ username: "bob", id: BOB, force_password_change: false }));

    expect(result.current).toEqual([]);
  });
});
