import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { updateTimelineNote } from "@/lib/api";
import type { TimelineNote } from "@/types";

import { NOTE_BODY_MAX_CHARS } from "./headNoteSync";
import { shownNotes } from "./noteOutbox";
import { forgetNoteOutboxes, noteOutboxFor } from "./noteOutboxes";
import { useNoteEdits } from "./useNoteEdits";

vi.mock("@/lib/api", async (original) => ({
  ...await original<typeof import("@/lib/api")>(),
  updateTimelineNote: vi.fn(),
}));

const note: TimelineNote = {
  id: "note-1", recording_id: "recording-1", kind: "head", body: "Original", revision: 2,
  created_at: "2026-10-27T10:00:00Z", updated_at: "2026-10-27T10:00:00Z",
};

const deferred = <T,>() => {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
};

afterEach(() => {
  forgetNoteOutboxes();
  vi.resetAllMocks();
});

describe("saved note optimism through the shared outbox", () => {
  it("shows edits before the answer, retains their confirmed revision and ignores an older listing", async () => {
    const request = deferred<TimelineNote>();
    vi.mocked(updateTimelineNote).mockReturnValueOnce(request.promise);
    const { result } = renderHook(() => useNoteEdits(note.recording_id));
    let saved!: Promise<string | null>;
    act(() => { saved = result.current.edit(note, "Changed"); });
    expect(result.current.apply([note])[0].body).toBe("Changed");
    const outbox = noteOutboxFor(note.recording_id);
    expect(shownNotes([note], outbox.getSnapshot())[0].body).toBe("Changed");
    act(() => outbox.ingest([note]));
    expect(result.current.apply([note])[0].body).toBe("Changed");

    await act(async () => request.resolve({ ...note, body: "Changed", revision: 3 }));
    expect(await saved).toBeNull();
    expect(result.current.apply([note])[0]).toMatchObject({ body: "Changed", revision: 3 });
    expect(outbox.getSnapshot().bodyEdits?.size).toBe(0);
    expect(updateTimelineNote).toHaveBeenCalledWith(note.recording_id, note.id, {
      body: "Changed", expected_revision: 2,
    });
  });

  it.each([new Error("offline"), { response: { status: 422, data: { detail: "Locked" } } }])(
    "rolls back a refused edit and permits a deliberate retry",
    async (failure) => {
      const request = deferred<TimelineNote>();
      vi.mocked(updateTimelineNote).mockReturnValueOnce(request.promise);
      const { result } = renderHook(() => useNoteEdits(note.recording_id));
      let saved!: Promise<string | null>;
      act(() => { saved = result.current.edit(note, "Changed"); });
      expect(result.current.apply([note])[0].body).toBe("Changed");
      await act(async () => request.reject(failure));
      expect(await saved).toBeTypeOf("string");
      expect(result.current.apply([note])[0].body).toBe("Original");
      expect(shownNotes([note], noteOutboxFor(note.recording_id).getSnapshot())[0].body).toBe("Original");

      vi.mocked(updateTimelineNote).mockResolvedValueOnce({ ...note, body: "Changed", revision: 3 });
      await act(async () => { expect(await result.current.edit(note, "Changed")).toBeNull(); });
      expect(result.current.apply([note])[0].body).toBe("Changed");
    },
  );

  it("a conflict restores the server's current body and the next save uses that revision", async () => {
    const current = { ...note, body: "Changed elsewhere", revision: 5 };
    vi.mocked(updateTimelineNote).mockRejectedValueOnce({ response: {
      status: 409, data: { detail: { code: "revision_conflict", note: current } },
    } });
    const { result } = renderHook(() => useNoteEdits(note.recording_id));
    await act(async () => {
      expect(await result.current.edit(note, "Mine")).toContain("Save again to keep yours.");
    });
    expect(result.current.apply([note])[0]).toMatchObject(current);
    vi.mocked(updateTimelineNote).mockResolvedValueOnce({ ...current, body: "Mine", revision: 6 });
    await act(async () => { await result.current.edit(result.current.apply([note])[0], "Mine"); });
    expect(updateTimelineNote).toHaveBeenLastCalledWith(note.recording_id, note.id, {
      body: "Mine", expected_revision: 5,
    });
  });

  it("does not let an older answer clear a newer edit of the same note", async () => {
    const first = deferred<TimelineNote>();
    const second = deferred<TimelineNote>();
    vi.mocked(updateTimelineNote).mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const { result } = renderHook(() => useNoteEdits(note.recording_id));
    act(() => { void result.current.edit(note, "First"); });
    act(() => { void result.current.edit(note, "Second"); });
    await act(async () => first.resolve({ ...note, body: "First", revision: 3 }));
    expect(result.current.apply([note])[0].body).toBe("Second");
    await act(async () => second.reject(new Error("offline")));
    expect(result.current.apply([note])[0]).toMatchObject({ body: "First", revision: 3 });
  });

  it("validates before publishing a preview or sending a request", async () => {
    const { result } = renderHook(() => useNoteEdits(note.recording_id));
    await act(async () => {
      expect(await result.current.edit(note, "x".repeat(NOTE_BODY_MAX_CHARS + 1))).toContain("at most");
    });
    expect(updateTimelineNote).not.toHaveBeenCalled();
    expect(result.current.apply([note])[0]).toEqual(note);
    expect(noteOutboxFor(note.recording_id).getSnapshot().bodyEdits?.size ?? 0).toBe(0);
  });

  it.each(["success", "conflict"] as const)(
    "keeps a late %s with its originating recording after the hook switches recording ids",
    async (outcome) => {
      const first = deferred<TimelineNote>();
      const second = deferred<TimelineNote>();
      vi.mocked(updateTimelineNote).mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
      // Reusing the note id proves every held result and preview is scoped by recording too.
      const other = { ...note, recording_id: "recording-2", body: "Other original", revision: 1 };
      const { result, rerender } = renderHook(({ recordingId }) => useNoteEdits(recordingId), {
        initialProps: { recordingId: note.recording_id },
      });
      const firstOutbox = noteOutboxFor(note.recording_id);
      let firstSaved!: Promise<string | null>;
      act(() => { firstSaved = result.current.edit(note, "First recording draft"); });
      expect(result.current.apply([note])[0]).toMatchObject({ body: "First recording draft", revision: 2 });
      expect(shownNotes([note], firstOutbox.getSnapshot())[0].body).toBe("First recording draft");

      rerender({ recordingId: other.recording_id });
      const secondOutbox = noteOutboxFor(other.recording_id);
      expect(result.current.apply([other])[0]).toEqual(other);
      expect(shownNotes([other], secondOutbox.getSnapshot())[0]).toEqual(other);
      let secondSaved!: Promise<string | null>;
      act(() => { secondSaved = result.current.edit(other, "Second recording draft"); });
      expect(result.current.apply([other])[0]).toMatchObject({ body: "Second recording draft", revision: 1 });
      expect(shownNotes([other], secondOutbox.getSnapshot())[0].body).toBe("Second recording draft");

      const firstAnswer = { ...note, body: outcome === "success" ? "First recording draft" : "Changed elsewhere",
        revision: outcome === "success" ? 3 : 5 };
      await act(async () => {
        if (outcome === "success") first.resolve(firstAnswer);
        else first.reject({ response: {
          status: 409, data: { detail: { code: "revision_conflict", note: firstAnswer } },
        } });
      });
      if (outcome === "success") expect(await firstSaved).toBeNull();
      else expect(await firstSaved).toContain("Save again to keep yours.");
      expect(firstOutbox.getSnapshot().bodyEdits?.size).toBe(0);
      expect(secondOutbox.getSnapshot().bodyEdits?.get(other.id)).toBe("Second recording draft");
      expect(result.current.apply([other])[0]).toMatchObject({ body: "Second recording draft", revision: 1 });

      await act(async () => second.reject(new Error("offline")));
      expect(await secondSaved).toContain("No answer");
      expect(secondOutbox.getSnapshot().bodyEdits?.size).toBe(0);
      expect(result.current.apply([other])[0]).toEqual(other);
      expect(shownNotes([other], secondOutbox.getSnapshot())[0]).toEqual(other);

      rerender({ recordingId: note.recording_id });
      expect(result.current.apply([note])[0]).toMatchObject(firstAnswer);
      expect(vi.mocked(updateTimelineNote).mock.calls.map(([recordingId]) => recordingId))
        .toEqual([note.recording_id, other.recording_id]);
    },
  );

  it.each(["success", "conflict"])("does not resurrect older pending text or a late %s", async (outcome) => {
    const first = deferred<TimelineNote>();
    const second = deferred<TimelineNote>();
    vi.mocked(updateTimelineNote).mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const { result } = renderHook(() => useNoteEdits(note.recording_id));
    act(() => { void result.current.edit(note, "First"); });
    act(() => { void result.current.edit(note, "Second"); });
    await act(async () => second.resolve({ ...note, body: "Second", revision: 4 }));
    expect(result.current.apply([note])[0]).toMatchObject({ body: "Second", revision: 4 });
    expect(noteOutboxFor(note.recording_id).getSnapshot().bodyEdits?.size).toBe(0);
    const older = { ...note, body: "First", revision: 3 };
    await act(async () => {
      if (outcome === "success") first.resolve(older);
      else first.reject({ response: { status: 409, data: { detail: { code: "revision_conflict", note: older } } } });
    });
    expect(result.current.apply([note])[0]).toMatchObject({ body: "Second", revision: 4 });
  });
});
