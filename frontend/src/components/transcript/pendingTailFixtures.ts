import type { TranscriptSegment } from "@/types";

export const pendingTailLines = (): [TranscriptSegment, TranscriptSegment] => [{
  id: "anchor", revision: 3, start: 0, end: 1, speaker: "LIVE_PROVISIONAL_1", text: "Firm words",
  provisional: true, settling_required: true, locked: true, source_channel: "microphone",
  words: [
    { id: "w1", order: 0, word: "Firm", start_ms: 0, end_ms: 400, locked: true },
    { id: "w2", order: 1, word: " words", start_ms: 500, end_ms: 1000, locked: true },
  ],
}, {
  id: "tail", revision: 4, start: 1, end: 2, speaker: "UNKNOWN", text: "pending tail",
  provisional: true, settling_required: true, locked: false, diarisation_pending: true, source_channel: "microphone",
  words: [
    { id: "w3", order: 2, word: "pending", start_ms: 1000, end_ms: 1500, locked: false },
    { id: "w4", order: 3, word: " tail", start_ms: 1500, end_ms: 2000, locked: false },
  ],
}];
