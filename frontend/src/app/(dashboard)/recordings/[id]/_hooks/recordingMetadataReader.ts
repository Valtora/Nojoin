import { getRecording, getRecordingIfChanged } from "@/lib/api";
import type { AudioClock } from "@/lib/notes/audioClock";
import type { Recording } from "@/types";

/** Orders metadata, including action refreshes, without losing unchanged clock reads. */
export function createRecordingMetadataReader(audioClock: AudioClock, params: Promise<{ id: string }>) {
  let generation = 0;
  let active = true;
  let requested = 0;
  let accepted = 0;
  const reads = new WeakMap<Recording, () => boolean>();

  const ticket = () => {
    const requestGeneration = generation;
    const order = ++requested;
    return {
      current: () => active && generation === requestGeneration && order >= accepted,
      accept: () => { accepted = order; },
    };
  };

  return {
    activate() { active = true; generation += 1; },
    cancel() { active = false; generation += 1; },
    isActive: () => active,
    invalidate() { generation += 1; },
    isCurrent: (data: Recording) => reads.get(data)?.() ?? false,
    async readIfChanged(recordingId: string, etag: string | null) {
      const read = ticket();
      const sentAt = audioClock.stamp();
      const expected = await params;
      if (!read.current() || expected.id !== recordingId) return null;
      const data = await getRecordingIfChanged(recordingId, etag);
      if (!read.current()) return null;
      read.accept();
      if (data) {
        reads.set(data.recording, read.current);
        audioClock.observe(data.recording, sentAt);
      }
      return data;
    },
    async read(recordingId: string) {
      const read = ticket();
      const sentAt = audioClock.stamp();
      const isCurrent = read.current;
      try {
        const expected = await params;
        if (!isCurrent() || expected.id !== recordingId) return null;
        const data = await getRecording(recordingId);
        if (!isCurrent()) return null;
        read.accept();
        reads.set(data, isCurrent);
        audioClock.observe(data, sentAt);
        return data;
      } catch (error) {
        if (!isCurrent()) return null;
        read.accept();
        throw error;
      }
    },
  };
}
