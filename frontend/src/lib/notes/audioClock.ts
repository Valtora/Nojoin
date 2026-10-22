import {
  ClientStatus,
  RecordingStatus,
  type Recording,
  type RecordingId,
} from "@/types";

/**
 * Where a capture is on its audio timeline now, as this device can tell, so a
 * note typed during a meeting is anchored where it was written.
 *
 * The server sends `live_audio_ms` with every read of a recording whose
 * capture is open (`backend/utils/live_audio_clock.py`): the end of the last
 * segment it holds, run on while recording. Between reads this clock runs that
 * figure on by the time since the read arrived, only while the capture is
 * advancing, so it stands still while paused and every device viewing the
 * recording reads the same timeline. Each read sets it afresh, so it never
 * drifts further than one poll from the server.
 *
 * While capture advances it never runs backwards: a read below what it last
 * said (the server stops running on 2.5 s past a late segment, where this
 * clock had kept going) holds it until the read catches up, so two notes sent
 * in turn are never anchored the other way round. A pause sets it to the
 * paused read, which is exact, even when that is a step back.
 *
 * The detail poll feeds it every read, changed or not: the page's recording
 * state ignores `live_audio_ms`, which changes on every poll.
 */

/** Mirrors `capture_is_advancing` in `backend/utils/live_audio_clock.py`. */
export const isCaptureAdvancing = (
  status: RecordingStatus | undefined,
  clientStatus: ClientStatus | null | undefined,
): boolean =>
  status === RecordingStatus.UPLOADING &&
  (clientStatus == null || clientStatus === ClientStatus.RECORDING);

type ClockRead = Pick<Recording, "id" | "live_audio_ms" | "status" | "client_status">;

interface Reading {
  recordingId: RecordingId;
  audioMs: number;
  advancing: boolean;
  /** When the read's request was sent, which orders reads that overlap. */
  sentAt: number;
  /** When it arrived: the clock runs on from here. */
  receivedAt: number;
}

export class AudioClock {
  private reading: Reading | null = null;
  /** What `audioNow` last said while capture advanced, which it never goes below. */
  private floor: { recordingId: RecordingId; audioMs: number } | null = null;

  constructor(private readonly monotonicNow: () => number = () => performance.now()) {}

  /** The time to pass as `sentAt` for a read about to be requested. */
  stamp = (): number => this.monotonicNow();

  /**
   * Takes a fresh read of the recording from the server. `sentAt` is when its
   * request went out (`stamp()`): a read sent before the one the clock holds
   * arrived late and is ignored, so overlapping polls cannot set it back.
   */
  observe = (recording: ClockRead, sentAt: number = this.monotonicNow()): void => {
    const receivedAt = this.monotonicNow();
    const current = this.reading;
    const sameRecording = current?.recordingId === recording.id;
    if (current && sameRecording && sentAt < current.sentAt) return;
    if (recording.live_audio_ms == null) {
      // The capture is over: the timeline ends where it was last seen.
      this.reading = sameRecording && current
        ? { ...current, advancing: false, sentAt, receivedAt }
        : null;
      this.floor = null;
      return;
    }
    const advancing = isCaptureAdvancing(recording.status, recording.client_status);
    if (!advancing || !sameRecording) this.floor = null;
    this.reading = {
      recordingId: recording.id,
      audioMs: recording.live_audio_ms,
      advancing,
      sentAt,
      receivedAt,
    };
  };

  /** The position now, in whole ms; null before any read of an open capture. */
  audioNow = (recordingId: RecordingId): number | null => {
    const reading = this.reading;
    if (!reading || reading.recordingId !== recordingId) return null;
    if (!reading.advancing) return reading.audioMs;
    const elapsed = Math.max(0, this.monotonicNow() - reading.receivedAt);
    const floor = this.floor?.recordingId === recordingId ? this.floor.audioMs : 0;
    const audioMs = Math.max(floor, Math.round(reading.audioMs + elapsed));
    this.floor = { recordingId, audioMs };
    return audioMs;
  };
}
