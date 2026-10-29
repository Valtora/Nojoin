import { getRecording } from "@/lib/api";
import type { AudioClock } from "@/lib/notes/audioClock";

/** Orders metadata, including action refreshes, without losing unchanged clock reads. */
export function createRecordingMetadataReader(audioClock: AudioClock, params: Promise<{ id: string }>) {
  let generation = 0;
  let active = true;
  let requested = 0;
  let accepted = 0;

  return {
    activate() { active = true; generation += 1; },
    cancel() { active = false; generation += 1; },
    isActive: () => active,
    async read(recordingId: string) {
      const requestGeneration = generation;
      const order = ++requested;
      const sentAt = audioClock.stamp();
      const isCurrent = () => active && generation === requestGeneration && order >= accepted;
      try {
        const expected = await params;
        if (!isCurrent() || expected.id !== recordingId) return null;
        const data = await getRecording(recordingId);
        if (!isCurrent()) return null;
        accepted = order;
        audioClock.observe(data, sentAt);
        return data;
      } catch (error) {
        if (!isCurrent()) return null;
        accepted = order;
        throw error;
      }
    },
  };
}
