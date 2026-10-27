import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { TranscriptSegment } from "@/types";
import { useTranscriptEditing, type UseTranscriptEditingOptions } from "./useTranscriptEditing";
import { useTranscriptSearch } from "./useTranscriptSearch";

vi.mock("@/lib/notificationStore", () => ({
  useNotificationStore: () => ({ addNotification: vi.fn() }),
}));

const line: TranscriptSegment = {
  id: "u1", revision: 3, text: "Original words", speaker: "SPEAKER_1", start: 0, end: 2,
};

function renderEditing(overrides: Partial<UseTranscriptEditingOptions> = {}) {
  const base = {
    segments: [line], readOnly: false, allowProvisionalEdits: false,
    onRenameSpeaker: vi.fn(async () => {}), onUpdateSegmentText: vi.fn(async () => {}),
    onFindAndReplace: vi.fn(async () => {}), onFindReplaceDone: vi.fn(),
    ...overrides,
  };
  return renderHook((changes: Partial<UseTranscriptEditingOptions>) => {
    const options = { ...base, ...changes };
    const search = useTranscriptSearch({
      segments: options.segments, showSearch: true, scrollSegmentIntoView: vi.fn(),
    });
    return { editing: useTranscriptEditing({ ...options, search }), search };
  }, { initialProps: {} });
}

afterEach(() => vi.restoreAllMocks());

describe("shared editor mutation contracts", () => {
  it("retains a refused correction and retries unchanged text until accepted", async () => {
    const save = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true);
    const { result } = renderEditing({ onUpdateSegmentText: save });
    act(() => result.current.editing.lineActions.startTextEdit(line, "u1"));
    act(() => result.current.editing.editor.setValue("My correction"));
    await act(async () => result.current.editing.editor.handleTextSubmit(line));
    expect(result.current.editing.editingTextId).toBe("u1");
    expect(result.current.editing.editor.value).toBe("My correction");
    await act(async () => result.current.editing.editor.handleTextSubmit(line));
    expect(save).toHaveBeenCalledTimes(2);
    expect(result.current.editing.editingTextId).toBeNull();
  });

  it("retains a rejected correction without leaking an event-handler rejection", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const save = vi.fn().mockRejectedValue(new Error("Connection lost"));
    const { result } = renderEditing({ onUpdateSegmentText: save });
    act(() => result.current.editing.lineActions.startTextEdit(line, "u1"));
    act(() => result.current.editing.editor.setValue("Keep this draft"));
    await act(async () => result.current.editing.editor.handleTextSubmit(line));
    expect(result.current.editing.editingTextId).toBe("u1");
    expect(result.current.editing.editor.value).toBe("Keep this draft");
    expect(result.current.editing.isSubmitting).toBe(false);
  });

  it("deduplicates Enter and blur without closing a draft whose pending save is refused", async () => {
    let finish!: (saved: boolean) => void;
    const save = vi.fn(() => new Promise<boolean>((resolve) => { finish = resolve; }));
    const { result } = renderEditing({ onUpdateSegmentText: save });
    act(() => result.current.editing.lineActions.startTextEdit(line, "u1"));
    act(() => result.current.editing.editor.setValue("Pending correction"));
    let pending!: Promise<void>;
    act(() => { pending = result.current.editing.editor.handleTextSubmit(line); });
    await act(async () => result.current.editing.editor.handleTextSubmit(line));
    expect(save).toHaveBeenCalledTimes(1);
    expect(result.current.editing.editingTextId).toBe("u1");
    act(() => result.current.editing.editor.setValue("Newer typing"));
    await act(async () => { finish(false); await pending; });
    expect(result.current.editing.editor.value).toBe("Newer typing");
    expect(result.current.editing.editingTextId).toBe("u1");
  });

  it("keeps the opened revision and live mutation callback through finalisation", async () => {
    const liveSave = vi.fn().mockResolvedValue(false);
    const finishedSave = vi.fn().mockResolvedValue(true);
    const { result, rerender } = renderEditing({ onUpdateSegmentText: liveSave });
    act(() => result.current.editing.lineActions.startTextEdit(line, "u1"));
    act(() => result.current.editing.editor.setValue("Opened during capture"));
    const finalLine = { ...line, revision: 9, text: "Final wording" };
    rerender({ segments: [finalLine], onUpdateSegmentText: finishedSave, readOnly: true });
    await act(async () => result.current.editing.editor.handleTextSubmit(finalLine));
    expect(liveSave).toHaveBeenCalledWith(line, "Opened during capture");
    expect(finishedSave).not.toHaveBeenCalled();
    expect(result.current.editing.editor.value).toBe("Opened during capture");
  });

  it("captures the current callback at open while keeping line action identities stable", async () => {
    const oldSave = vi.fn();
    const newSave = vi.fn();
    const laterSave = vi.fn();
    const { result, rerender } = renderEditing({ onUpdateSegmentText: oldSave });
    const actions = result.current.editing.lineActions;
    rerender({ onUpdateSegmentText: newSave });
    expect(result.current.editing.lineActions).toBe(actions);
    act(() => result.current.editing.lineActions.startTextEdit(line, "u1"));
    act(() => result.current.editing.editor.setValue("New route"));
    rerender({ onUpdateSegmentText: laterSave });
    await act(async () => result.current.editing.editor.handleTextSubmit(line));
    expect(newSave).toHaveBeenCalledWith(line, "New route");
    expect(oldSave).not.toHaveBeenCalled();
    expect(laterSave).not.toHaveBeenCalled();
  });

  it("retains a refused rename and keeps the callback captured before a source switch", async () => {
    const liveRename = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true);
    const finishedRename = vi.fn();
    const { result, rerender } = renderEditing({ onRenameSpeaker: liveRename });
    act(() => result.current.editing.lineActions.startSpeakerRename("u1", "SPEAKER_1", "Alex"));
    act(() => result.current.editing.editor.setValue("Blair"));
    rerender({ onRenameSpeaker: finishedRename });
    await act(async () => result.current.editing.editor.handleSpeakerRenameSubmit());
    expect(result.current.editing.editingSpeakerLineId).toBe("u1");
    expect(result.current.editing.editor.value).toBe("Blair");
    await act(async () => result.current.editing.editor.handleSpeakerRenameSubmit());
    expect(liveRename).toHaveBeenCalledTimes(2);
    expect(finishedRename).not.toHaveBeenCalled();
    expect(result.current.editing.editingSpeakerLineId).toBeNull();
  });

  it.each([{ readOnly: true }, { bulkEditsDisabled: true }])(
    "blocks both replacement paths when capability is unavailable: %j", async (capabilities) => {
      const update = vi.fn();
      const replaceAll = vi.fn();
      const { result } = renderEditing({ ...capabilities, onUpdateSegmentText: update, onFindAndReplace: replaceAll });
      act(() => {
        result.current.search.setFindText("Original");
        result.current.search.setReplaceText("Changed");
      });
      expect(result.current.search.matches).toHaveLength(1);
      await act(async () => result.current.editing.handleReplaceCurrent());
      await act(async () => result.current.editing.handleFindReplaceSubmit());
      expect(update).not.toHaveBeenCalled();
      expect(replaceAll).not.toHaveBeenCalled();
    },
  );

  it("rebases retained newer typing only onto an observed accepted revision while retaining its mutation route", async () => {
    let accept!: (saved: boolean) => void;
    const original = vi.fn().mockImplementationOnce(() => new Promise<boolean>((resolve) => { accept = resolve; }))
      .mockResolvedValueOnce(true);
    const replacement = vi.fn().mockResolvedValue(true);
    const { result, rerender } = renderEditing({ onUpdateSegmentText: original });
    act(() => result.current.editing.lineActions.startTextEdit(line, "u1"));
    act(() => result.current.editing.editor.setValue("Accepted first correction"));
    let first!: Promise<void>;
    act(() => { first = result.current.editing.editor.handleTextSubmit(line); });
    act(() => result.current.editing.editor.setValue("Newer draft typed while saving"));
    await act(async () => { accept(true); await first; });
    const observed = { ...line, revision: 8, text: "Accepted first correction" };
    rerender({ segments: [observed], onUpdateSegmentText: replacement });
    await act(async () => result.current.editing.editor.handleTextSubmit(observed));
    expect(original).toHaveBeenLastCalledWith(observed, "Newer draft typed while saving");
    expect(replacement).not.toHaveBeenCalled();
  });

  it("never rebases a refused draft onto an unrelated newer poll revision", async () => {
    let accept!: (saved: boolean) => void;
    const save = vi.fn().mockImplementationOnce(() => new Promise<boolean>((resolve) => { accept = resolve; }))
      .mockResolvedValue(false);
    const { result, rerender } = renderEditing({ onUpdateSegmentText: save });
    act(() => result.current.editing.lineActions.startTextEdit(line, "u1"));
    act(() => result.current.editing.editor.setValue("Accepted correction"));
    let first!: Promise<void>;
    act(() => { first = result.current.editing.editor.handleTextSubmit(line); });
    act(() => result.current.editing.editor.setValue("My refused correction"));
    await act(async () => { accept(true); await first; });
    const observed = { ...line, revision: 8, text: "Accepted correction" };
    rerender({ segments: [observed] });
    await act(async () => result.current.editing.editor.handleTextSubmit(observed));
    expect(save).toHaveBeenLastCalledWith(observed, "My refused correction");
    const remote = { ...line, revision: 9, text: "Remote correction" };
    rerender({ segments: [remote] });
    await act(async () => result.current.editing.editor.handleTextSubmit(remote));
    expect(save).toHaveBeenLastCalledWith(observed, "My refused correction");
    expect(result.current.editing.editor.value).toBe("My refused correction");
  });
});
