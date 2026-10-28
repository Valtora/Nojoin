import type { ComponentProps } from "react";
import { act, render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type TranscriptView from "@/components/TranscriptView";
import TranscriptSection from "@/test/TranscriptSectionWithEdits";
import { RecordingStatus, type Recording } from "@/types";

const boundary = vi.hoisted(() => ({ props: null as ComponentProps<typeof TranscriptView> | null }));
vi.mock("@/components/TranscriptView", () => ({
  default: (props: ComponentProps<typeof TranscriptView>) => { boundary.props = props; return <div />; },
}));

describe("transcript mutation callbacks retained by an open control", () => {
  it("checks current authority before undo, redo and replace, then unlocks after the displayed snapshot is accepted", async () => {
    const onUndo = vi.fn();
    const onRedo = vi.fn();
    const onFindAndReplace = vi.fn();
    const props: ComponentProps<typeof TranscriptSection> = {
      active: true, recording: { id: "rec-1", status: RecordingStatus.PROCESSED } as Recording,
      transcriptSegments: [{ id: "old", speaker: "SPEAKER_00", text: "Old words", start: 0, end: 1 }],
      currentTime: -1, isPlaying: false, speakerMap: {}, speakerColors: {}, globalSpeakers: [],
      canUndo: true, canRedo: true, deferredTranscriptUtteranceIds: [],
      onPlaySegment: vi.fn(), onPause: vi.fn(), onResume: vi.fn(), onRenameSpeaker: vi.fn(), onRefresh: vi.fn(),
      onUpdateSegmentSpeaker: vi.fn(), onUpdateSegmentText: vi.fn(), onFindAndReplace, onUndo, onRedo,
      onExport: vi.fn(), onActiveEditUtteranceChange: vi.fn(),
    };
    const view = render(<TranscriptSection {...props} />);
    const retained = boundary.props!;
    view.rerender(<TranscriptSection {...props} hasRetainedPublication />);
    expect(boundary.props).toMatchObject({ readOnly: true, canUndo: false, canRedo: false });
    await act(async () => {
      retained.onUndo();
      retained.onRedo();
      await retained.onFindAndReplace("Old", "New");
    });
    expect(onUndo).not.toHaveBeenCalled();
    expect(onRedo).not.toHaveBeenCalled();
    expect(onFindAndReplace).not.toHaveBeenCalled();

    view.rerender(<TranscriptSection {...props} hasRetainedPublication transcriptIsAuthoritative
      deferredTranscriptUtteranceIds={["old"]} />);
    expect(boundary.props).toMatchObject({ readOnly: true, canUndo: false, canRedo: false });
    await act(async () => retained.onFindAndReplace("Old", "New"));
    expect(onFindAndReplace).not.toHaveBeenCalled();

    view.rerender(<TranscriptSection {...props} hasRetainedPublication transcriptIsAuthoritative />);
    expect(boundary.props).toMatchObject({ readOnly: false, canUndo: true, canRedo: true });
    await act(async () => {
      retained.onUndo();
      retained.onRedo();
      await retained.onFindAndReplace("Old", "New");
    });
    expect(onUndo).toHaveBeenCalledOnce();
    expect(onRedo).toHaveBeenCalledOnce();
    expect(onFindAndReplace).toHaveBeenCalledExactlyOnceWith("Old", "New");
  });
});
