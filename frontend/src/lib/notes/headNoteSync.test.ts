import { beforeEach, describe, expect, it, vi } from "vitest";

import type { TimelineNote } from "@/types";

import { HeadNoteSync, newNoteId, noteLength, stripNote } from "./headNoteSync";

const updateTimelineNote = vi.fn();

vi.mock("@/lib/api", () => ({
  listTimelineNotes: vi.fn(),
  createTimelineNote: vi.fn(),
  deleteTimelineNote: vi.fn(),
  updateTimelineNote: (...args: unknown[]) => updateTimelineNote(...args),
}));

/**
 * A head note as the API returns it, each revision written by another
 * device. Its kind never changes and it has no anchors or quote, so a write
 * can change only its text.
 */
const head = (body: string, revision: number): TimelineNote => ({
  id: "h1",
  recording_id: "rec-1",
  kind: "head",
  body,
  anchor_ms: null,
  start_ms: null,
  end_ms: null,
  quoted_text: null,
  created_by: { id: 1, username: "dana" },
  revision,
  last_write_token: "device-b-token",
  created_at: "2026-10-17T10:00:00Z",
  updated_at: `2026-10-17T10:0${revision}:00Z`,
});

/** The 409 for a write made against a stale revision, carrying the note as it is. */
const stale = (note: TimelineNote) => ({
  isAxiosError: true,
  response: {
    status: 409,
    data: { detail: { code: "revision_conflict", message: "x", note } },
  },
});

describe("HeadNoteSync", () => {
  beforeEach(() => {
    updateTimelineNote.mockReset();
  });

  it("runs saves one at a time, each against the revision the last one made", async () => {
    updateTimelineNote
      .mockResolvedValueOnce(head("One", 2))
      .mockResolvedValueOnce(head("Two", 3));
    const sync = new HeadNoteSync("rec-1", [head("Original", 1)]);

    await Promise.all([sync.save("One"), sync.save("Two")]);

    expect(
      updateTimelineNote.mock.calls.map(
        ([, , update]) => update.expected_revision,
      ),
    ).toEqual([1, 2]);
    expect(sync.getSnapshot().body).toBe("Two");
  });

  it("ignores a poll older than its own edit", async () => {
    updateTimelineNote.mockResolvedValue(head("Edited", 2));
    const sync = new HeadNoteSync("rec-1", [head("Original", 1)]);
    await sync.save("Edited");

    sync.ingest([head("Original", 1)]);
    expect(sync.getSnapshot().body).toBe("Edited");

    sync.ingest([head("From the other device", 3)]);
    expect(sync.getSnapshot().body).toBe("From the other device");
  });

  it("saves nothing while a conflict is unresolved", async () => {
    updateTimelineNote.mockRejectedValueOnce({
      isAxiosError: true,
      response: {
        status: 409,
        data: {
          detail: {
            code: "revision_conflict",
            message: "x",
            note: head("Theirs", 2),
          },
        },
      },
    });
    const sync = new HeadNoteSync("rec-1", [head("Original", 1)]);
    await expect(sync.save("Mine")).rejects.toBeTruthy();

    await expect(sync.save("Mine again")).rejects.toThrow(/conflict/);
    expect(updateTimelineNote).toHaveBeenCalledTimes(1);
    expect(sync.getSnapshot().conflict).toEqual({
      kind: "changed",
      note: head("Theirs", 2),
    });
  });

  it("sends an edit again over a write elsewhere that left the note's content as it was", async () => {
    updateTimelineNote
      .mockRejectedValueOnce(stale(head("Original", 2)))
      .mockResolvedValueOnce(head("Mine", 3));
    const sync = new HeadNoteSync("rec-1", [head("Original", 1)]);

    await sync.save("Mine");

    expect(
      updateTimelineNote.mock.calls.map(
        ([, , update]) => update.expected_revision,
      ),
    ).toEqual([1, 2]);
    expect(sync.getSnapshot()).toMatchObject({ body: "Mine", conflict: null });
  });

  it("takes a write elsewhere that changed the note's text as a conflict", async () => {
    const theirs = head("Original, and theirs", 2);
    updateTimelineNote.mockRejectedValueOnce(stale(theirs));
    const sync = new HeadNoteSync("rec-1", [head("Original", 1)]);

    await expect(sync.save("Mine")).rejects.toThrow(/conflict/);

    expect(updateTimelineNote).toHaveBeenCalledTimes(1);
    expect(sync.getSnapshot().conflict).toEqual({
      kind: "changed",
      note: theirs,
    });
  });

  it("keeps its snapshot when a poll changes nothing it shows", () => {
    const sync = new HeadNoteSync("rec-1", [head("Original", 1)]);
    const before = sync.getSnapshot();

    sync.ingest([head("Original", 1)]);

    expect(sync.getSnapshot()).toBe(before);
  });
});

describe("stripNote and noteLength", () => {
  it("trim and count a note as the API's Python does", () => {
    expect(stripNote("\u001c\u0085 note \u3000")).toBe("note");
    expect(stripNote("\ufeffnote")).toBe("\ufeffnote");
    expect(noteLength("\u{1F600}a")).toBe(2);
  });
});

describe("newNoteId", () => {
  it("is a version 4 UUID", () => {
    const ids = new Set(Array.from({ length: 50 }, newNoteId));
    expect(ids.size).toBe(50);
    for (const id of ids) {
      expect(id).toMatch(
        /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
      );
    }
  });
});
