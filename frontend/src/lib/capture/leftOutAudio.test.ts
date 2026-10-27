import { IDBFactory } from "fake-indexeddb";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useNotificationStore } from "@/lib/notificationStore";
import { setCachedUserTimeZone } from "@/lib/timezone";
import { withProcessTimeZone } from "@/test/processTimeZone";

import {
  IndexedDbLeftOutAudioStore,
  LEFT_OUT_AUDIO_KEEP_MS,
  leftOutAudioFileName,
  MemoryLeftOutAudioStore,
  offerLeftOutAudio,
  type LeftOutAudio,
  type LeftOutAudioStore,
} from "./leftOutAudio";

const ALICE = 7;
const BOB = 8;

/** Noon on 22 October 2026, local time. */
const NOW = new Date(2026, 9, 22, 12).getTime();

const audio = (id: string, overrides: Partial<LeftOutAudio> = {}): LeftOutAudio => ({
  id,
  recordingId: "rec-1",
  recordingName: "Weekly sync",
  ownerId: ALICE,
  blob: new Blob(["fLaC"], { type: "audio/flac" }),
  seconds: 96,
  keptAt: NOW,
  ...overrides,
});

describe.each([
  ["IndexedDB", () => new IndexedDbLeftOutAudioStore(new IDBFactory())],
  ["memory", () => new MemoryLeftOutAudioStore()],
] as [string, () => LeftOutAudioStore][])("%s left-out audio store", (_name, createStore) => {
  it("lists what it keeps, oldest first, and removes one by id", async () => {
    const store = createStore();
    await store.put(audio("b", { keptAt: NOW + 1 }));
    await store.put(audio("a"));

    expect((await store.list()).map((entry) => entry.id)).toEqual(["a", "b"]);
    expect((await store.get("b"))?.keptAt).toBe(NOW + 1);

    await store.remove("a");

    expect((await store.list()).map((entry) => entry.id)).toEqual(["b"]);
    expect(await store.get("a")).toBeUndefined();
  });
});

describe("offering left-out audio", () => {
  let store: MemoryLeftOutAudioStore;
  let closed: Set<string>;
  const save = vi.fn();
  const signedInAs = vi.fn(async (): Promise<number | undefined> => ALICE);

  const offer = (now = NOW) => offerLeftOutAudio({ store, closed, signedInAs, save, now });

  const active = () => useNotificationStore.getState().activeNotifications;

  const action = (label: string) => {
    const found = active()[0]?.actions?.find((candidate) => candidate.label === label);
    if (!found) {
      throw new Error(`No ${label} action on screen.`);
    }
    return found;
  };

  const ids = async () => (await store.list()).map((entry) => entry.id);

  beforeEach(() => {
    store = new MemoryLeftOutAudioStore();
    closed = new Set();
    save.mockReset();
    signedInAs.mockReset();
    signedInAs.mockResolvedValue(ALICE);
    useNotificationStore.setState({ activeNotifications: [], history: [] });
  });

  afterEach(() => {
    useNotificationStore.setState({ activeNotifications: [], history: [] });
  });

  it("offers its owner a download that stays up, saying how much and until when", async () => {
    await store.put(audio("a"));

    await offer();

    expect(active()).toHaveLength(1);
    expect(active()[0]).toMatchObject({ id: "left-out-audio-a", type: "warning", persistent: true });
    const until = new Date(NOW + LEFT_OUT_AUDIO_KEEP_MS).toLocaleDateString(undefined, {
      day: "numeric",
      month: "long",
    });
    expect(active()[0].message).toBe(
      'About 2 minutes of audio this browser recorded for "Weekly sync" is not in the ' +
        `recording. Download it to keep a copy. This browser deletes it on ${until}.`,
    );
    expect(active()[0].actions?.map((entry) => entry.label)).toEqual(["Download", "Delete"]);
  });

  it("offers it once, however often it looks", async () => {
    await store.put(audio("a"));

    await offer();
    await offer();

    expect(active()).toHaveLength(1);
  });

  it("offers it to no other account", async () => {
    await store.put(audio("a"));
    signedInAs.mockResolvedValue(BOB);

    await offer();

    expect(active()).toEqual([]);
    expect(await ids()).toEqual(["a"]);
  });

  it("offers nothing while it cannot tell who is signed in", async () => {
    await store.put(audio("a"));
    signedInAs.mockResolvedValue(undefined);

    await offer();

    expect(active()).toEqual([]);
  });

  it("asks who is signed in only when there is audio to offer", async () => {
    await offer();

    expect(signedInAs).not.toHaveBeenCalled();
  });

  it("removes audio nobody took once its time is up, whoever it belongs to", async () => {
    await store.put(audio("old", { ownerId: BOB }));
    await store.put(audio("new", { keptAt: NOW + 1 }));

    await offer(NOW + LEFT_OUT_AUDIO_KEEP_MS);

    expect(await ids()).toEqual(["new"]);
    expect(active().map((entry) => entry.id)).toEqual(["left-out-audio-new"]);
  });

  it("saves the file on Download, then deletes this browser's copy", async () => {
    await store.put(audio("a"));
    await offer();

    action("Download").onClick();

    await vi.waitFor(async () => expect(await ids()).toEqual([]));
    expect(save).toHaveBeenCalledWith(expect.objectContaining({ id: "a", seconds: 96 }));
    // The toast takes the offer down itself, as for any action.
    expect(action("Download").dismisses).toBeUndefined();
  });

  it("keeps it when the browser could not save it", async () => {
    await store.put(audio("a"));
    await offer();
    save.mockImplementation(() => {
      throw new Error("Blocked");
    });

    action("Download").onClick();

    await vi.waitFor(() =>
      expect(active().map((entry) => entry.type)).toEqual(["warning", "error"]),
    );
    expect(await ids()).toEqual(["a"]);
  });

  it("says so when another tab took it first", async () => {
    await store.put(audio("a"));
    await offer();
    await store.remove("a");

    action("Download").onClick();

    await vi.waitFor(() =>
      expect(active().map((entry) => entry.type)).toEqual(["warning", "info"]),
    );
    expect(save).not.toHaveBeenCalled();
  });

  it("deletes it on Delete", async () => {
    await store.put(audio("a"));
    await offer();

    action("Delete").onClick();

    await vi.waitFor(async () => expect(await ids()).toEqual([]));
    expect(save).not.toHaveBeenCalled();
  });

  it("hides a closed offer until the next page load, keeping the audio", async () => {
    await store.put(audio("a"));
    await offer();

    active()[0].onClose?.();
    useNotificationStore.getState().dismissToast("left-out-audio-a");
    await offer();

    expect(active()).toEqual([]);
    expect(await ids()).toEqual(["a"]);

    // The next page starts with nothing closed.
    closed = new Set();
    await offer();
    expect(active()).toHaveLength(1);
  });

  describe("with the time-zone setting ahead of the browser's zone", () => {
    withProcessTimeZone("America/New_York");

    it("dates the deletion in the setting's zone", async () => {
      setCachedUserTimeZone("Asia/Tokyo");
      // 28 October in New York, 29 October in Tokyo.
      const deletesAt = Date.parse("2026-10-29T02:30:00Z");
      await store.put(audio("a", { keptAt: deletesAt - LEFT_OUT_AUDIO_KEEP_MS }));

      await offer();

      const inZone = (timeZone: string) =>
        new Date(deletesAt).toLocaleDateString(undefined, { day: "numeric", month: "long", timeZone });
      expect(inZone("Asia/Tokyo")).not.toBe(inZone("America/New_York"));
      expect(active()[0].message).toContain(`This browser deletes it on ${inZone("Asia/Tokyo")}.`);
    });
  });
});

describe("the file a download is saved as", () => {
  it("is named after the recording, in the characters the audio export keeps", () => {
    expect(leftOutAudioFileName({ recordingId: "rec-1", recordingName: "Q3 review: Alice/Bob" })).toBe(
      "Q3 review AliceBob (left out).flac",
    );
  });

  it("falls back to the recording's id", () => {
    expect(leftOutAudioFileName({ recordingId: "rec-1" })).toBe("recording_rec-1 (left out).flac");
    expect(leftOutAudioFileName({ recordingId: "rec-1", recordingName: "???" })).toBe(
      "recording_rec-1 (left out).flac",
    );
  });
});
