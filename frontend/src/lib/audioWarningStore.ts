import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { RecordingId } from "@/types";

interface AudioWarningState {
  suppressQuietAudioWarnings: boolean;
  dismissedMeetingRecordingIds: RecordingId[];
  dismissForMeeting: (recordingId: RecordingId) => void;
  suppressWarnings: () => void;
  resetWarnings: () => void;
}

export const useAudioWarningStore = create<AudioWarningState>()(
  persist(
    (set) => ({
      suppressQuietAudioWarnings: false,
      dismissedMeetingRecordingIds: [],
      dismissForMeeting: (recordingId) =>
        set((state) => ({
          dismissedMeetingRecordingIds:
            state.dismissedMeetingRecordingIds.includes(recordingId)
              ? state.dismissedMeetingRecordingIds
              : [...state.dismissedMeetingRecordingIds, recordingId],
        })),
      suppressWarnings: () => set({ suppressQuietAudioWarnings: true }),
      resetWarnings: () =>
        set({
          suppressQuietAudioWarnings: false,
          dismissedMeetingRecordingIds: [],
        }),
    }),
    {
      name: "nojoin-audio-warning-preferences",
      partialize: (state) => ({
        suppressQuietAudioWarnings: state.suppressQuietAudioWarnings,
      }),
    },
  ),
);