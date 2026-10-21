import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { TimelineNote, TimelineNoteCreate } from "@/types";

import { forgetNoteOutboxes, noteOutboxFor } from "./noteOutbox";
import { useRecordingNotes } from "./useNoteOutbox";

const listTimelineNotes = vi.fn();
const createTimelineNote = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  listTimelineNotes: (...args: unknown[]) => listTimelineNotes(...args),
  createTimelineNote: (...args: unknown[]) => createTimelineNote(...args),
}));

const saved = (payload: TimelineNoteCreate): TimelineNote => ({
  id: payload.id!,
  recording_id: "rec-1",
  kind: payload.kind,
  body: payload.body,
  anchor_ms: payload.anchor_ms ?? null,
  revision: 1,
  last_write_token: payload.write_token ?? null,
  created_at: "2026-10-21T10:01:00",
  updated_at: "2026-10-21T10:01:00",
});

const recording = { id: "rec-1", timeline_notes_list_signature: "0:e3b0c44298fc1c14" };

const addNote = async () => {
  await act(async () => {
    noteOutboxFor("rec-1").submit({ kind: "point", body: "Budget approved", anchorMs: 61_000 });
  });
  await waitFor(() => expect(noteOutboxFor("rec-1").getSnapshot().entries).toEqual([]));
};

describe("useRecordingNotes", () => {
  beforeEach(() => {
    forgetNoteOutboxes();
    listTimelineNotes.mockReset().mockResolvedValue([]);
    createTimelineNote.mockReset().mockImplementation(
      async (_id: string, payload: TimelineNoteCreate) => saved(payload),
    );
  });

  it("fetches the list again after its own write when nothing polls the recording", async () => {
    const { result } = renderHook(() => useRecordingNotes(recording, false));
    await waitFor(() => expect(result.current).toEqual([]));
    expect(listTimelineNotes).toHaveBeenCalledTimes(1);

    await addNote();

    await waitFor(() => expect(listTimelineNotes).toHaveBeenCalledTimes(2));
  });

  it("leaves it to the next poll's signature while the recording is polled", async () => {
    const { result } = renderHook(() => useRecordingNotes(recording, true));
    await waitFor(() => expect(result.current).toEqual([]));

    await addNote();

    expect(listTimelineNotes).toHaveBeenCalledTimes(1);
  });
});
