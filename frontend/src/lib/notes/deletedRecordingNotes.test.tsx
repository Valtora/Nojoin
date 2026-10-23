import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { noteSignedIn, resetSessionForTests, useSessionStore } from "@/lib/session";
import { NO_ANSWER, resetNotesServer } from "@/test/fakeNotesServer";
import { httpError } from "@/test/httpErrors";

import {
  CHECKS_AT_ONCE,
  forgetNotesOfDeletedRecordings,
  MAX_RECORDINGS_CHECKED,
  RECORDING_NOT_FOUND_DETAIL,
  resetDeletedRecordingSweepForTests,
  useForgetNotesOfDeletedRecordings,
} from "./deletedRecordingNotes";
import { EMPTY_DRAFT } from "./noteOutbox";
import { forgetNoteOutboxes, noteOutboxFor } from "./noteOutboxes";
import { MemoryNoteStore, NOTE_RECORD_VERSION, setNoteStoreForTests } from "./noteStore";

const getRecording = vi.hoisted(() => vi.fn());
const getUserMe = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  ...(await import("@/test/fakeNotesServer")).fakeNotesApi,
  getRecording: (recordingId: string) => getRecording(recordingId),
  getUserMe: () => getUserMe(),
}));

const ALICE = { id: 1, username: "alice.example", force_password_change: false };
const BOB = { id: 2, username: "bob.example", force_password_change: false };

/** The API's answer for a recording deleted for good, or never the account's own. */
const GONE = httpError(404, RECORDING_NOT_FOUND_DETAIL);

let store: MemoryNoteStore;

/** A record this browser kept for `recordingId`, holding one unsent note. */
const keep = (recordingId: string, account = ALICE.id) =>
  store.put({
    version: NOTE_RECORD_VERSION,
    account,
    recordingId,
    notes: [
      {
        id: `${recordingId}-note`,
        writeToken: `${recordingId}-token`,
        kind: "tail",
        body: "Send the budget to Alice Example",
        anchorMs: null,
        createdAt: "2026-10-22T10:00:00Z",
      },
    ],
    draft: { ...EMPTY_DRAFT },
    discards: [],
  });

/** The recordings the server answers for, as their owner sees them; any other is gone. */
const serverHas = (recordings: Record<string, { is_deleted: boolean }>) => {
  getRecording.mockImplementation(async (recordingId: string) => {
    const recording = recordings[recordingId];
    if (!recording) throw GONE;
    return { id: recordingId, ...recording };
  });
};

const kept = () => store.recordingsOf(ALICE.id).then((ids) => [...ids].sort());

beforeEach(() => {
  resetNotesServer();
  resetSessionForTests();
  resetDeletedRecordingSweepForTests();
  forgetNoteOutboxes();
  store = new MemoryNoteStore();
  setNoteStoreForTests(store);
  noteSignedIn(ALICE);
  getRecording.mockReset();
  getUserMe.mockReset();
  getUserMe.mockResolvedValue(ALICE);
});

afterEach(() => {
  forgetNoteOutboxes();
  setNoteStoreForTests(null);
  resetSessionForTests();
});

describe("notes kept for a recording deleted on another device (issue #636)", () => {
  it("forgets them once the API says the recording is gone for its owner", async () => {
    await keep("rec-gone");
    await keep("rec-listed");
    serverHas({ "rec-listed": { is_deleted: false } });

    expect(await forgetNotesOfDeletedRecordings(["rec-listed"])).toEqual(["rec-gone"]);

    expect(await kept()).toEqual(["rec-listed"]);
    // A recording in the list is there, so it costs no request.
    expect(getRecording.mock.calls).toEqual([["rec-gone"]]);
  });

  it("asks /users/me before the checks and again after them", async () => {
    await keep("rec-gone");
    serverHas({});

    await forgetNotesOfDeletedRecordings([]);

    const [before, after] = getUserMe.mock.invocationCallOrder;
    const [check] = getRecording.mock.invocationCallOrder;
    expect(before).toBeLessThan(check);
    expect(after).toBeGreaterThan(check);
  });

  it("keeps the notes of a recording in the bin, which can be restored", async () => {
    await keep("rec-binned");
    serverHas({ "rec-binned": { is_deleted: true } });

    expect(await forgetNotesOfDeletedRecordings([])).toEqual([]);

    expect(await kept()).toEqual(["rec-binned"]);
  });

  it.each([
    ["a 404 that isn't the API's answer for a recording", httpError(404, "Not Found")],
    ["a 401", httpError(401, "Not authenticated")],
    ["a 403", httpError(403, "Password change required")],
    ["a 500", httpError(500, "Internal Server Error")],
    ["a 502", httpError(502)],
    ["no answer", NO_ANSWER],
  ])("keeps them on %s", async (_answer, error) => {
    await keep("rec-a");
    getRecording.mockRejectedValue(error);

    expect(await forgetNotesOfDeletedRecordings([])).toEqual([]);

    expect(await kept()).toEqual(["rec-a"]);
  });

  it("checks nothing while the browser is signed in as another account (issue #583)", async () => {
    await keep("rec-a");
    getUserMe.mockResolvedValue(BOB);
    serverHas({});

    expect(await forgetNotesOfDeletedRecordings([])).toEqual([]);

    expect(getRecording).not.toHaveBeenCalled();
    expect(await kept()).toEqual(["rec-a"]);
  });

  it("keeps them when another account signs in while the checks are out", async () => {
    await keep("rec-a");
    getUserMe.mockResolvedValueOnce(ALICE).mockResolvedValueOnce(BOB);
    serverHas({});

    expect(await forgetNotesOfDeletedRecordings([])).toEqual([]);

    expect(await kept()).toEqual(["rec-a"]);
  });

  it.each([
    ["its session ends", () => useSessionStore.setState({ signInRequired: true })],
    ["it signs in as another account", () => noteSignedIn(BOB)],
  ])("keeps them when this tab finds meanwhile that %s", async (_change, change) => {
    await keep("rec-a");
    getRecording.mockImplementation(async () => {
      change();
      throw GONE;
    });

    expect(await forgetNotesOfDeletedRecordings([])).toEqual([]);

    expect(await kept()).toEqual(["rec-a"]);
  });

  it("keeps them when /users/me doesn't answer", async () => {
    await keep("rec-a");
    getUserMe.mockRejectedValue(NO_ANSWER);
    serverHas({});

    expect(await forgetNotesOfDeletedRecordings([])).toEqual([]);

    expect(await kept()).toEqual(["rec-a"]);
  });

  it("keeps them while this tab must sign in again", async () => {
    await keep("rec-a");
    useSessionStore.setState({ signInRequired: true });
    serverHas({});

    expect(await forgetNotesOfDeletedRecordings([])).toEqual([]);

    expect(getUserMe).not.toHaveBeenCalled();
    expect(await kept()).toEqual(["rec-a"]);
  });

  it("checks nothing before /users/me has named the account", async () => {
    await keep("rec-a");
    resetSessionForTests();

    expect(await forgetNotesOfDeletedRecordings([])).toEqual([]);

    expect(getUserMe).not.toHaveBeenCalled();
    expect(getRecording).not.toHaveBeenCalled();
  });

  it("leaves a recording this page holds an outbox for to that outbox", async () => {
    await keep("rec-open");
    noteOutboxFor("rec-open");
    serverHas({});

    expect(await forgetNotesOfDeletedRecordings([])).toEqual([]);

    expect(getRecording).not.toHaveBeenCalled();
    expect(getUserMe).not.toHaveBeenCalled();
  });

  it("leaves another account's records alone", async () => {
    await keep("rec-bob", BOB.id);
    serverHas({});

    expect(await forgetNotesOfDeletedRecordings([])).toEqual([]);

    expect(getRecording).not.toHaveBeenCalled();
    expect(await store.recordingsOf(BOB.id)).toEqual(["rec-bob"]);
  });

  it(`checks at most ${MAX_RECORDINGS_CHECKED} records, ${CHECKS_AT_ONCE} at a time`, async () => {
    for (let index = 0; index < MAX_RECORDINGS_CHECKED + 5; index += 1) {
      await keep(`rec-${String(index).padStart(2, "0")}`);
    }
    let inFlight = 0;
    let most = 0;
    getRecording.mockImplementation(async () => {
      inFlight += 1;
      most = Math.max(most, inFlight);
      await new Promise((resolve) => setTimeout(resolve, 0));
      inFlight -= 1;
      throw GONE;
    });

    const forgot = await forgetNotesOfDeletedRecordings([]);

    expect(getRecording).toHaveBeenCalledTimes(MAX_RECORDINGS_CHECKED);
    expect(most).toBe(CHECKS_AT_ONCE);
    expect(forgot).toHaveLength(MAX_RECORDINGS_CHECKED);
    expect(await kept()).toHaveLength(5);
  });
});

describe("when the sweep runs", () => {
  const listPage = (listed: { id: string }[] | null) =>
    renderHook(({ list }) => useForgetNotesOfDeletedRecordings(list), {
      initialProps: { list: listed },
    });

  it("waits for the recordings list, then sweeps once per page load", async () => {
    await keep("rec-gone");
    serverHas({});
    const page = listPage(null);

    expect(getUserMe).not.toHaveBeenCalled();

    page.rerender({ list: [{ id: "rec-listed" }] });
    await waitFor(async () => expect(await kept()).toEqual([]));
    expect(getRecording).toHaveBeenCalledTimes(1);

    // The list's next poll doesn't sweep again.
    await keep("rec-later");
    page.rerender({ list: [{ id: "rec-listed" }] });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(getRecording).toHaveBeenCalledTimes(1);
    expect(await kept()).toEqual(["rec-later"]);
  });

  it("sweeps again for another account signed in on the page", async () => {
    await keep("rec-alice");
    await keep("rec-bob", BOB.id);
    serverHas({});
    const page = listPage([]);
    await waitFor(() => expect(getRecording).toHaveBeenCalledWith("rec-alice"));

    getUserMe.mockResolvedValue(BOB);
    noteSignedIn(BOB);
    page.rerender({ list: [] });

    await waitFor(() => expect(getRecording).toHaveBeenCalledWith("rec-bob"));
    await waitFor(async () => expect(await store.recordingsOf(BOB.id)).toEqual([]));
  });
});
