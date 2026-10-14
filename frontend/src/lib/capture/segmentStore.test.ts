import { IDBFactory } from "fake-indexeddb";
import { describe, expect, it } from "vitest";

import {
  contiguousSegmentsAfter,
  createSegmentStore,
  IndexedDbSegmentStore,
  MemorySegmentStore,
  type SegmentStore,
  type StoredSegment,
} from "./segmentStore";

const segment = (recordingId: string, sequence: number): StoredSegment => ({
  recordingId,
  sequence,
  blob: new Blob([`${recordingId}:${sequence}`], { type: "audio/flac" }),
  durationSeconds: 2,
});

describe.each([
  ["IndexedDB", () => new IndexedDbSegmentStore(new IDBFactory())],
  ["memory", () => new MemorySegmentStore()],
] as [string, () => SegmentStore][])("%s segment store", (_name, createStore) => {
  it("lists a recording's segments in sequence order", async () => {
    const store = createStore();
    await store.put(segment("rec-1", 2));
    await store.put(segment("rec-1", 0));
    await store.put(segment("rec-2", 1));
    await store.put(segment("rec-1", 1));

    const listed = await store.list("rec-1");

    // The blob's contents are not compared: fake-indexeddb clones jsdom's Blob
    // through Node's structuredClone, which drops its prototype. Chrome stores
    // Blobs in IndexedDB natively.
    expect(listed.map((entry) => entry.sequence)).toEqual([0, 1, 2]);
    expect(listed.map((entry) => entry.recordingId)).toEqual(["rec-1", "rec-1", "rec-1"]);
    expect(listed[1].durationSeconds).toBe(2);
  });

  it("removes one segment, or a whole recording, and nothing else", async () => {
    const store = createStore();
    for (const sequence of [0, 1, 2]) {
      await store.put(segment("rec-1", sequence));
    }
    await store.put(segment("rec-2", 0));

    await store.remove("rec-1", 1);
    expect((await store.list("rec-1")).map((entry) => entry.sequence)).toEqual([0, 2]);

    await store.clear("rec-1");
    expect(await store.list("rec-1")).toEqual([]);
    expect(await store.list("rec-2")).toHaveLength(1);
  });

  it("replaces a segment written twice", async () => {
    const store = createStore();
    await store.put(segment("rec-1", 0));
    await store.put({ ...segment("rec-1", 0), durationSeconds: 1.5 });

    expect((await store.list("rec-1")).map((entry) => entry.durationSeconds)).toEqual([1.5]);
  });
});

describe("segment store helpers", () => {
  it("falls back to memory where there is no IndexedDB", () => {
    expect(createSegmentStore(undefined)).toBeInstanceOf(MemorySegmentStore);
  });

  it("keeps only the run that continues the server's last sequence", () => {
    const stored = [3, 4, 5, 6, 8, 9].map((sequence) => segment("rec-1", sequence));

    expect(contiguousSegmentsAfter(stored, 4).map((entry) => entry.sequence)).toEqual([5, 6]);
    expect(contiguousSegmentsAfter(stored, 9)).toEqual([]);
    expect(contiguousSegmentsAfter(stored, 0)).toEqual([]);
  });
});
