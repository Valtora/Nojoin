import type { ComponentProps } from "react";
import { vi } from "vitest";
import type TranscriptView from "../TranscriptView";
import type SpeakerPanel from "../SpeakerPanel";
import type { GlobalSpeaker, RecordingSpeaker, TranscriptSegment } from "@/types";

export const speaker = (label: string, name: string, id = 1): RecordingSpeaker => ({
  id, diarization_label: label, local_name: name, recording_id: "r1",
  created_at: "2026-10-27T00:00:00Z", updated_at: "2026-10-27T00:00:00Z",
});
export const person = (id: number, name: string): GlobalSpeaker => ({
  id, name, created_at: "2026-10-27T00:00:00Z", updated_at: "2026-10-27T00:00:00Z",
});
export const line = (overrides: Partial<TranscriptSegment> = {}): TranscriptSegment => ({
  id: "long", revision: 4, speaker: "A", start: 0, end: 30,
  text: "Opening words and the final words.", ...overrides,
});
export const viewProps = (segments: TranscriptSegment[] = [line()]): ComponentProps<typeof TranscriptView> => ({
  recordingId: "r1", segments, currentTime: -1, isPlaying: false,
  speakerMap: { A: "Alex", A2: "Alex", B: "Blair" },
  speakers: [speaker("A", "Alex"), speaker("B", "Blair", 2)], globalSpeakers: [], speakerColors: {},
  onPlaySegment: vi.fn(), onPause: vi.fn(), onResume: vi.fn(), onRenameSpeaker: vi.fn(),
  onUpdateSegmentSpeaker: vi.fn(), onUpdateSegmentText: vi.fn(), onFindAndReplace: vi.fn(),
  onUndo: vi.fn(), onRedo: vi.fn(), canUndo: false, canRedo: false, onExport: vi.fn(),
});
export const panelProps = (): ComponentProps<typeof SpeakerPanel> => ({
  recordingId: "r1", segments: [line()], speakers: [speaker("A", "Alex"), speaker("B", "Blair", 2)],
  globalSpeakers: [], speakerColors: {}, currentTime: -1, isPlaying: false,
  onPlaySegment: vi.fn(), onPause: vi.fn(), onResume: vi.fn(), onColorChange: vi.fn(), onRefresh: vi.fn(),
});
