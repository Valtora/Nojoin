import { create } from "zustand";

import type { RecordingId } from "@/types";

/**
 * What the live controls show about the screen study (#653), one capture at a
 * time. Kept in memory only, like the frames themselves.
 *
 * - `sampling`: frames are being taken and sent;
 * - `paused`: the recording is paused, so nothing is taken;
 * - `stopped`: sampling has ended, by Stop or after a failure, and the frames
 *   already sent stay until they are deleted;
 * - `deleted`: this meeting's frames have been deleted.
 */
export type ScreenStudyStatus = "sampling" | "paused" | "stopped" | "deleted";

export interface ScreenStudyState {
  recordingId: RecordingId | null;
  status: ScreenStudyStatus | null;
  frames: number;
  bytes: number;
  /** ISO time the server will delete the frames by. */
  deleteAfter: string | null;
  /** Frames waiting in this tab, and how many the full queue dropped. */
  queued: number;
  dropped: number;
  /** Why sampling stopped itself, when it did. */
  error: string | null;
  deleting: boolean;
}

export const EMPTY_SCREEN_STUDY: ScreenStudyState = {
  recordingId: null,
  status: null,
  frames: 0,
  bytes: 0,
  deleteAfter: null,
  queued: 0,
  dropped: 0,
  error: null,
  deleting: false,
};

interface ScreenStudyStore extends ScreenStudyState {
  update: (patch: Partial<ScreenStudyState>) => void;
  reset: () => void;
}

export const useScreenStudyStore = create<ScreenStudyStore>((set) => ({
  ...EMPTY_SCREEN_STUDY,
  update: (patch) => set(patch),
  reset: () => set(EMPTY_SCREEN_STUDY),
}));
