import { create } from "zustand";
import { persist } from "zustand/middleware";

interface AudioWarningState {
  suppressQuietAudioWarnings: boolean;
  dismissedMeetingRecordingIds: number[];
  dismissForMeeting: (recordingId: number) => void;
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