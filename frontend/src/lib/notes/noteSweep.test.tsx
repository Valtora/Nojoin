import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import StoredNotesSender from "@/components/notes/StoredNotesSender";
import { noteSignedIn, resetSessionForTests, useSessionStore } from "@/lib/session";
import { createTimelineNote, NO_ANSWER, resetNotesServer, server } from "@/test/fakeNotesServer";
import { dispatchRecordingRemoved } from "@/lib/recordingEvents";
import { renderWithProviders } from "@/test/renderWithProviders";

import { EMPTY_DRAFT } from "./noteOutbox";
import {
  forgetNoteOutboxes,
  goneNotes,
  noteOutboxFor,
  noteRecordingRemoved,
  sweepStoredNotes,
  unsentRecordings,
} from "./noteOutboxes";
import {
  MemoryNoteStore,
  NOTE_RECORD_VERSION,
  setNoteStoreForTests,
  type StoredOutbox,
} from "./noteStore";

const getCurrentUser = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  ...(await import("@/test/fakeNotesServer")).fakeNotesApi,
  getCurrentUser: () => getCurrentUser(),
}));

const ALICE = { id: 1, username: "alice", force_password_change: false };
const BOB = { id: 2, username: "bob", force_password_change: false };

let store: MemoryNoteStore;

/** What a page that closed or crashed left in the browser for `recordingId`. */
const keep = async (recordingId: string, bodies: string[], boxText = "") => {
  const record: StoredOutbox = {
    version: NOTE_RECORD_VERSION,
    account: ALICE.id,
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
    discards: [],
  };
  await store.put(record);
  return record;
};

/** The page goes away, as a closed or killed tab does: nothing more is sent or written. */
const closeThePage = () => forgetNoteOutboxes();

/** Holds the next create's answer until `release` is called. */
const holdNextCreate = () => {
  const answer = createTimelineNote.getMockImplementation();
  let release = () => undefined as void;
  const released = new Promise<void>((resolve) => {
    release = resolve;
  });
  createTimelineNote.mockImplementationOnce(async (...args) => {
    await released;
    return answer!(...args);
  });
  return release;
};

const leave = () => {
  const event = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(event);
  return event.defaultPrevented;
};

beforeEach(() => {
  resetNotesServer();
  resetSessionForTests();
  forgetNoteOutboxes();
  store = new MemoryNoteStore();
  setNoteStoreForTests(store);
  noteSignedIn(ALICE);
  getCurrentUser.mockReset().mockImplementation(async () => {
    noteSignedIn(ALICE);
    return ALICE;
  });
});

afterEach(() => {
  forgetNoteOutboxes();
  setNoteStoreForTests(null);
  resetSessionForTests();
  vi.unstubAllGlobals();
});

describe("sending the notes kept for recordings this page never opened", () => {
  it("sends each once, under the id and token it was kept with, and removes the records", async () => {
    const first = await keep("rec-a", ["Budget agreed", "Ship on Friday"]);
    const second = await keep("rec-b", ["Call the vendor"]);

    await sweepStoredNotes(ALICE.id);

    await waitFor(() => expect(server.notes.size).toBe(3));
    const sent = server.creates.map(({ id, write_token }) => [id, write_token]);
    const stored = [...first.notes, ...second.notes].map((note) => {
      const { id, writeToken } = note as { id: string; writeToken: string };
      return [id, writeToken];
    });
    expect(sent).toEqual(expect.arrayContaining(stored));
    expect(server.creates).toHaveLength(3);
    await waitFor(async () => expect(await store.recordingsOf(ALICE.id)).toEqual([]));
  });

  it("neither asks before the page closes nor holds sign-in for notes it only sends, until the recording is opened", async () => {
    await keep("rec-a", ["Budget agreed"]);
    server.failures.push(NO_ANSWER);

    await sweepStoredNotes(ALICE.id);

    await waitFor(() => expect(unsentRecordings()).toEqual(["rec-a"]));
    expect(leave()).toBe(false);
    expect(useSessionStore.getState().heldBy).toEqual([]);

    // Opening the recording shows the note: the page's now, warning and all.
    noteOutboxFor("rec-a");
    expect(leave()).toBe(true);
    expect(useSessionStore.getState().heldBy).toEqual(["notes"]);
  });

  it("keeps the record when the page dies mid-send, and the next page sends the note again without a second copy", async () => {
    await keep("rec-a", ["Budget agreed"]);
    // The create lands, but the page dies before its answer arrives.
    server.loseNextAnswer = true;
    await sweepStoredNotes(ALICE.id);
    await waitFor(() => expect(server.creates).toHaveLength(1));
    closeThePage();
    expect(await store.recordingsOf(ALICE.id)).toEqual(["rec-a"]);

    await sweepStoredNotes(ALICE.id);

    await waitFor(async () => expect(await store.recordingsOf(ALICE.id)).toEqual([]));
    expect(server.creates).toHaveLength(2);
    expect(server.creates[1]).toMatchObject({
      id: server.creates[0].id,
      write_token: server.creates[0].write_token,
    });
    expect(server.notes.size).toBe(1);
  });

  it("sends once per page, however often the session is confirmed as the page moves about", async () => {
    await keep("rec-a", ["Budget agreed", "Ship on Friday"]);
    await keep("rec-b", ["Call the vendor"]);
    const view = renderWithProviders(<StoredNotesSender />);
    await waitFor(() => expect(server.notes.size).toBe(3));
    await waitFor(async () => expect(await store.recordingsOf(ALICE.id)).toEqual([]));

    // Each in-app navigation asks /users/me again; one remounts the layout.
    for (let navigation = 0; navigation < 3; navigation += 1) {
      act(() => noteSignedIn(ALICE));
      await keep(`rec-later-${navigation}`, ["Kept by another tab meanwhile"]);
    }
    view.unmount();
    renderWithProviders(<StoredNotesSender />);
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });

    expect(createTimelineNote).toHaveBeenCalledTimes(3);
  });

  it("sends one recording's notes at a time", async () => {
    await keep("rec-a", ["Budget agreed"]);
    await keep("rec-b", ["Call the vendor"]);
    const release = holdNextCreate();

    const sweep = sweepStoredNotes(ALICE.id);
    await waitFor(() => expect(createTimelineNote).toHaveBeenCalledTimes(1));
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    expect(createTimelineNote).toHaveBeenCalledTimes(1);

    release();
    await sweep;
    expect(createTimelineNote).toHaveBeenCalledTimes(2);
  });

  it("stops once the session ends, leaving the rest for a later page", async () => {
    await keep("rec-a", ["Budget agreed"]);
    await keep("rec-b", ["Call the vendor"]);
    const release = holdNextCreate();
    const sweep = sweepStoredNotes(ALICE.id);
    await waitFor(() => expect(createTimelineNote).toHaveBeenCalledTimes(1));

    act(() => useSessionStore.setState({ signInRequired: true }));
    release();
    await sweep;

    expect(createTimelineNote).toHaveBeenCalledTimes(1);
    expect(await store.recordingsOf(ALICE.id)).toContain("rec-b");
  });

  it("leaves alone a recording the page already shows", async () => {
    await keep("rec-a", ["Budget agreed"]);
    const shown = noteOutboxFor("rec-a");
    act(() => shown.setDraft({ ...EMPTY_DRAFT, text: "Typed on the page" }));

    await sweepStoredNotes(ALICE.id);

    expect(noteOutboxFor("rec-a")).toBe(shown);
    expect(leave()).toBe(true);
  });

  it("marks a recording whose discards the server has not confirmed", async () => {
    await store.put({
      version: NOTE_RECORD_VERSION,
      account: ALICE.id,
      recordingId: "rec-d",
      notes: [],
      draft: null,
      discards: [{ id: "rec-d-note-0", writeToken: "rec-d-token-0" }],
    });
    server.failures.push(NO_ANSWER);

    await sweepStoredNotes(ALICE.id);

    await waitFor(() => expect(unsentRecordings()).toEqual(["rec-d"]));
  });

  it("marks the recording until its notes are sent", async () => {
    await keep("rec-a", ["Budget agreed"]);
    server.failures.push(NO_ANSWER);
    await sweepStoredNotes(ALICE.id);
    await waitFor(() => expect(unsentRecordings()).toEqual(["rec-a"]));

    const outbox = noteOutboxFor("rec-a");
    outbox.retry(outbox.getSnapshot().entries[0].key);

    await waitFor(() => expect(unsentRecordings()).toEqual([]));
  });
});

describe("notes kept for a recording that no longer exists", () => {
  const LONG = "Budget agreed\nwith the caveat that the vendor signs by Friday, in full, as discussed";

  const sweepIntoTheNotice = async () => {
    await keep("rec-gone", [LONG], "Half a thought about the vendor");
    server.goneRecordings.add("rec-gone");
    renderWithProviders(<StoredNotesSender />);
    return screen.findByRole("dialog", { name: "Notes for a recording that no longer exists" });
  };

  it("shows each note and the box's text in full, and keeps them until dismissed", async () => {
    const dialog = await sweepIntoTheNotice();

    expect(dialog).toHaveTextContent("with the caveat that the vendor signs by Friday, in full, as discussed");
    expect(dialog).toHaveTextContent("Half a thought about the vendor");
    expect(await store.recordingsOf(ALICE.id)).toEqual(["rec-gone"]);
    // Nothing more to send, so nothing holds the page or marks the recording.
    expect(leave()).toBe(false);
    expect(unsentRecordings()).toEqual([]);

    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));

    await waitFor(async () => expect(await store.recordingsOf(ALICE.id)).toEqual([]));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("removes them once they are copied, all at once", async () => {
    const writeText = vi.fn(async () => undefined);
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
    await sweepIntoTheNotice();

    fireEvent.click(screen.getByRole("button", { name: "Copy all" }));

    await waitFor(() => expect(writeText).toHaveBeenCalledWith(`${LONG}\n\nHalf a thought about the vendor`));
    await waitFor(async () => expect(await store.recordingsOf(ALICE.id)).toEqual([]));
  });

  it("keeps them when they could not be copied", async () => {
    vi.stubGlobal("navigator", {
      ...navigator,
      clipboard: { writeText: vi.fn(async () => Promise.reject(new Error("denied"))) },
    });
    await sweepIntoTheNotice();

    fireEvent.click(screen.getByRole("button", { name: "Copy all" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("couldn't be copied");
    expect(await store.recordingsOf(ALICE.id)).toEqual(["rec-gone"]);
  });

  it("shows them again on the next page when this one closes first", async () => {
    await sweepIntoTheNotice();
    closeThePage();
    expect(await store.recordingsOf(ALICE.id)).toEqual(["rec-gone"]);

    await sweepStoredNotes(ALICE.id);

    expect(await screen.findByRole("dialog")).toHaveTextContent("Half a thought about the vendor");
  });

  it("never takes a recording in the bin for gone: it still takes notes", async () => {
    await keep("rec-binned", ["Budget agreed"]);

    await sweepStoredNotes(ALICE.id);

    await waitFor(() => expect(server.notes.size).toBe(1));
    expect(getCurrentUser).not.toHaveBeenCalled();
    expect(unsentRecordings()).toEqual([]);
  });

  it("never takes another account's 404 for a deleted recording", async () => {
    await keep("rec-a", ["Budget agreed"]);
    // Another tab signed the browser in as bob: alice's recording answers 404.
    server.goneRecordings.add("rec-a");
    getCurrentUser.mockImplementation(async () => {
      noteSignedIn(BOB);
      return BOB;
    });
    renderWithProviders(<StoredNotesSender />);

    await waitFor(() => expect(getCurrentUser).toHaveBeenCalled());
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(await store.recordingsOf(ALICE.id)).toEqual(["rec-a"]);
  });

  it("hears of a recording discarded on this page", async () => {
    renderWithProviders(<StoredNotesSender />);
    const outbox = noteOutboxFor("rec-open");
    act(() => outbox.setDraft({ ...EMPTY_DRAFT, text: "Typed as it was discarded" }));

    act(() => dispatchRecordingRemoved("rec-open"));

    expect(await screen.findByRole("dialog")).toHaveTextContent("Typed as it was discarded");
  });

  it("forgets a removed recording that holds nothing to copy, with no notice", async () => {
    renderWithProviders(<StoredNotesSender />);
    const outbox = noteOutboxFor("rec-empty");

    act(() => noteRecordingRemoved("rec-empty"));

    expect(goneNotes()).toEqual([]);
    expect(noteOutboxFor("rec-empty")).not.toBe(outbox);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("lets go of a removed recording's notes on the page, and offers them to copy", async () => {
    renderWithProviders(<StoredNotesSender />);
    const outbox = noteOutboxFor("rec-open");
    act(() => outbox.setDraft({ ...EMPTY_DRAFT, text: "Typed as it was deleted" }));
    await waitFor(async () => expect(await store.recordingsOf(ALICE.id)).toEqual(["rec-open"]));
    expect(leave()).toBe(true);

    act(() => noteRecordingRemoved("rec-open"));

    expect(await screen.findByRole("dialog")).toHaveTextContent("Typed as it was deleted");
    expect(leave()).toBe(false);
    expect(useSessionStore.getState().heldBy).toEqual([]);
  });
});
