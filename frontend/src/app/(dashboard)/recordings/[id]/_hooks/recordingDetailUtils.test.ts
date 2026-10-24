// @vitest-environment node

import { describe, expect, it } from "vitest";

import { RecordingStatus, type Recording } from "@/types";

import {
  AUDIO_RECHECK_INTERVAL_MS,
  AUDIO_RECHECK_WINDOW_MS,
  hasPolledRecordingChanged,
  pollFailureMeansRemoved,
  notFoundMeans,
  recordingPollIntervalMs,
  shouldPollRecordingUpdates,
} from "./recordingDetailUtils";

const recordingWith = (overrides: Partial<Recording>) =>
  ({
    id: "rec-1",
    name: "Weekly sync",
    status: RecordingStatus.PROCESSED,
    has_proxy: false,
    speakers: [],
    ...overrides,
  }) as unknown as Recording;

describe("polling a recording with no audio and no proxy", () => {
  it("stops the fast proxy poll, since no proxy is coming", () => {
    expect(shouldPollRecordingUpdates(recordingWith({ has_audio: false }))).toBe(
      false,
    );
    expect(shouldPollRecordingUpdates(recordingWith({ has_audio: true }))).toBe(
      true,
    );
  });

  it("still re-checks slowly, so audio a restore moves in later shows up", () => {
    expect(recordingPollIntervalMs(recordingWith({ has_audio: false }))).toBe(
      AUDIO_RECHECK_INTERVAL_MS,
    );
    expect(recordingPollIntervalMs(recordingWith({ has_audio: true }))).toBe(1000);
  });

  it("stops re-checking once the audio has been unavailable for the whole window", () => {
    const missing = recordingWith({ has_audio: false });

    expect(recordingPollIntervalMs(missing, AUDIO_RECHECK_WINDOW_MS - 1)).toBe(
      AUDIO_RECHECK_INTERVAL_MS,
    );
    expect(recordingPollIntervalMs(missing, AUDIO_RECHECK_WINDOW_MS)).toBeNull();
    // The window bounds only the re-check, never the wait for a proxy.
    expect(
      recordingPollIntervalMs(
        recordingWith({ has_audio: true }),
        AUDIO_RECHECK_WINDOW_MS,
      ),
    ).toBe(1000);
  });

  it("does not poll a settled recording that has its audio", () => {
    expect(
      recordingPollIntervalMs(recordingWith({ has_proxy: true, has_audio: true })),
    ).toBeNull();
    // Not checked by the endpoint (null) is not the same as missing.
    expect(
      recordingPollIntervalMs(recordingWith({ has_proxy: true, has_audio: null })),
    ).toBeNull();
  });

  it("treats the arrival of the audio as a change worth re-rendering", () => {
    const before = recordingWith({ has_audio: false });

    expect(hasPolledRecordingChanged(before, { ...before })).toBe(false);
    expect(hasPolledRecordingChanged(before, { ...before, has_audio: true })).toBe(
      true,
    );
  });
});

describe("noticing a change to the notes in a poll", () => {
  it("sees a new notes list signature, which the page then fetches the list for", () => {
    const before = recordingWith({ timeline_notes_list_signature: "1:aaaa" });
    expect(
      hasPolledRecordingChanged(before, recordingWith({ timeline_notes_list_signature: "1:aaaa" })),
    ).toBe(false);
    expect(
      hasPolledRecordingChanged(before, recordingWith({ timeline_notes_list_signature: "1:bbbb" })),
    ).toBe(true);
    expect(
      hasPolledRecordingChanged(before, recordingWith({ timeline_notes_list_signature: "0:cccc" })),
    ).toBe(true);
  });

  it("ignores the live audio clock, which moves on every poll", () => {
    const before = recordingWith({ live_audio_ms: 1_000 });
    expect(
      hasPolledRecordingChanged(before, { ...before, live_audio_ms: 2_000 }),
    ).toBe(false);
  });
});

describe("notFoundMeans", () => {
  const ALICE = { id: 1, username: "alice" };
  const BOB = { id: 2, username: "bob" };
  const notHeld = () => false;

  it("reads a 404 as a removal when the account that loaded the recording is still signed in", async () => {
    await expect(notFoundMeans(1, async () => ALICE, notHeld)).resolves.toEqual({ kind: "removed" });
  });

  it("names the account the server answered with when it is another", async () => {
    await expect(notFoundMeans(1, async () => BOB, notHeld)).resolves.toEqual({
      kind: "signed-in-elsewhere",
      username: "bob",
    });
  });

  it("says nothing more while sign-in is held, as its dialog has opened", async () => {
    await expect(notFoundMeans(1, async () => BOB, () => true)).resolves.toBeNull();
  });

  it("reads whether sign-in is held only once the server has answered", async () => {
    let held = false;
    const answer = notFoundMeans(
      1,
      async () => {
        // The answer is what holds it: a held page's `/users/me` opens the dialog.
        held = true;
        return BOB;
      },
      () => held,
    );
    await expect(answer).resolves.toBeNull();
  });

  it("does nothing when it can't tell who is signed in", async () => {
    await expect(
      notFoundMeans(1, async () => Promise.reject(new Error("Network Error")), notHeld),
    ).resolves.toBeNull();
  });

  it("claims no removal when the page never learnt which account loaded it", async () => {
    await expect(notFoundMeans(null, async () => BOB, notHeld)).resolves.toBeNull();
  });
});

describe("pollFailureMeansRemoved", () => {
  it("reads a 404 as a removed recording while the session works", () => {
    expect(pollFailureMeansRemoved(404, true)).toBe(true);
  });

  it("ignores a 404 while the session belongs to someone else", () => {
    // Another tab signed in as a different account: the recording still exists.
    expect(pollFailureMeansRemoved(404, false)).toBe(false);
  });

  it("ignores other failures", () => {
    expect(pollFailureMeansRemoved(401, true)).toBe(false);
    expect(pollFailureMeansRemoved(null, true)).toBe(false);
  });
});
