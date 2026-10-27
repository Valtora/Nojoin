import { act, renderHook } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { updateTimelineNote } from "@/lib/api";
import type { TimelineNote } from "@/types";
import { useNoteEdits } from "./useNoteEdits";

vi.mock("@/lib/api", () => ({ updateTimelineNote: vi.fn(), deleteTimelineNote: vi.fn() }));

it("translates a point span optimistically and retains the server's translated span", async () => {
  const original: TimelineNote = { id: "n1", recording_id: "r1", kind: "point", body: "A thought",
    anchor_ms: 1000, start_ms: 1000, end_ms: 4500, revision: 1, created_at: "now", updated_at: "now" };
  let answer!: (note: TimelineNote) => void;
  vi.mocked(updateTimelineNote).mockReturnValue(new Promise((resolve) => { answer = resolve; }));
  const { result } = renderHook(() => useNoteEdits("r1"));
  let pending!: Promise<string | null>;
  act(() => { pending = result.current.move(original, 6000); });
  expect(result.current.apply([original])[0]).toMatchObject({ anchor_ms: 6000, start_ms: 6000, end_ms: 9500 });
  await act(async () => { answer({ ...original, anchor_ms: 6000, start_ms: 6000, end_ms: 9500, revision: 2 }); await pending; });
  expect(result.current.apply([original])[0]).toMatchObject({ anchor_ms: 6000, start_ms: 6000, end_ms: 9500, revision: 2 });
});
