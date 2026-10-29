import { act, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import TranscriptView from "../TranscriptView";
import { useNotificationStore } from "@/lib/notificationStore";
import { LINE_SETTLING_REASON } from "@/lib/transcript/settling";
import type { TranscriptSegment } from "@/types";
import { useTranscriptEditing } from "./_hooks/useTranscriptEditing";
import type { TranscriptSearch } from "./_hooks/useTranscriptSearch";
import { line, viewProps } from "./transcriptTestFixtures";

// A finished recording whose processing left a line still settling, beside a locked one.
const settling: TranscriptSegment = line({
  id: "open", text: "Words still moving", provisional: true, settling_required: true, locked: false,
  words: [
    { id: "w1", order: 0, word: "Words", start_ms: 0, end_ms: 400, locked: false },
    { id: "w2", order: 1, word: " still", start_ms: 500, end_ms: 900, locked: false },
    { id: "w3", order: 2, word: " moving", start_ms: 1000, end_ms: 1400, locked: false },
  ],
});
const locked: TranscriptSegment = line({
  id: "done", speaker: "B", start: 31, end: 33, text: "Finished wording here", provisional: true,
  settling_required: true, locked: true,
});

beforeEach(() => useNotificationStore.setState({ activeNotifications: [], history: [] }));

describe("a finished recording with lines still settling", () => {
  it("holds the line's edits back in its menu, giving the reason", () => {
    render(<TranscriptView {...viewProps([settling, locked])} />);
    fireEvent.click(screen.getByRole("button", { name: "Actions for Alex's line at 00:00" }));
    const edit = screen.getByRole("menuitem", { name: "Edit text" });
    for (const item of [edit, screen.getByRole("menuitem", { name: "Change speaker" })]) {
      expect(item).toHaveAttribute("aria-disabled", "true");
      expect(item).toHaveAccessibleDescription(LINE_SETTLING_REASON);
    }
    fireEvent.click(edit);
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    fireEvent.keyDown(edit, { key: "Escape" });

    fireEvent.click(screen.getByRole("button", { name: "Actions for Blair's line at 00:31" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Edit text" }));
    expect(screen.getByRole("textbox")).toHaveValue("Finished wording here");
  });

  it("keeps the settling line's speaker read-only", () => {
    render(<TranscriptView {...viewProps([settling, locked])} />);
    expect(screen.getByRole("button", { name: "Alex" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Blair" })).toBeEnabled();
  });

  it("opens no text editor on the settling line", () => {
    const { result } = renderHook(() => useTranscriptEditing({
      segments: [settling, locked], readOnly: false, allowProvisionalEdits: false,
      onRenameSpeaker: vi.fn(), onUpdateSegmentText: vi.fn(), onFindAndReplace: vi.fn(),
      search: {} as TranscriptSearch, onFindReplaceDone: vi.fn(),
    }));
    act(() => result.current.lineActions.startTextEdit(settling, "open"));
    expect(result.current.editingTextId).toBeNull();
    act(() => result.current.lineActions.startTextEdit(locked, "done"));
    expect(result.current.editingTextId).toBe("done");
  });

  it("refuses Replace on the settling line, saying why, and replaces on the locked one", async () => {
    const props = viewProps([settling, locked]);
    render(<TranscriptView {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "Find & Replace" }));
    fireEvent.change(screen.getByPlaceholderText("Replace..."), { target: { value: "fixed" } });
    fireEvent.change(screen.getByPlaceholderText("Find..."), { target: { value: "moving" } });
    fireEvent.click(screen.getByRole("button", { name: /^Replace$/ }));
    expect(props.onUpdateSegmentText).not.toHaveBeenCalled();
    expect(useNotificationStore.getState().activeNotifications.map((note) => note.message))
      .toEqual([LINE_SETTLING_REASON]);

    fireEvent.change(screen.getByPlaceholderText("Find..."), { target: { value: "wording" } });
    fireEvent.click(screen.getByRole("button", { name: /^Replace$/ }));
    await waitFor(() => expect(props.onUpdateSegmentText).toHaveBeenCalledWith(locked, "Finished fixed here"));
  });
});
