import { beforeEach, describe, expect, it, vi } from "vitest";

import type { TimelineNote } from "@/types";

import { HeadNoteSync, newNoteId } from "./headNoteSync";

const updateTimelineNote = vi.fn();

vi.mock("@/lib/api", () => ({
  createTimelineNote: vi.fn(),
  deleteTimelineNote: vi.fn(),
  updateTimelineNote: (...args: unknown[]) => updateTimelineNote(...args),
}));

const head = (body: string, revision: number): TimelineNote => ({
  id: "h1",
  recording_id: "rec-1",
  kind: "head",
  body,
  revision,
  created_at: "2026-10-17T10:00:00Z",
  updated_at: "2026-10-17T10:00:00Z",
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
      updateTimelineNote.mock.calls.map(([, , update]) => update.expected_revision),
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
      response: { status: 409, data: { detail: { message: "x", note: head("Theirs", 2) } } },
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
