import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ClientStatus, RecordingStatus, type Recording } from "@/types";

import { AudioClock, isCaptureAdvancing } from "./audioClock";

type Read = Pick<Recording, "id" | "live_audio_ms" | "status" | "client_status">;

const recording: Read = {
  id: "rec-1",
  status: RecordingStatus.UPLOADING,
  client_status: ClientStatus.RECORDING,
  live_audio_ms: 0,
};

const read = (liveAudioMs: number | null, changes: Partial<Read> = {}): Read => ({
  ...recording,
  live_audio_ms: liveAudioMs,
  ...changes,
});

const paused = (liveAudioMs: number) =>
  read(liveAudioMs, { status: RecordingStatus.PAUSED, client_status: ClientStatus.PAUSED });

describe("AudioClock", () => {
  beforeEach(() => {
    // The default clock reads performance.now(), so fake it with the timers.
    vi.useFakeTimers({ toFake: ["setTimeout", "setInterval", "Date", "performance"] });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("runs on between polls while recording and resyncs on each poll", () => {
    const clock = new AudioClock();
    clock.observe(read(10_000));

    vi.advanceTimersByTime(400);
    expect(clock.audioNow("rec-1")).toBe(10_400);

    // The next poll, a second on: the server's figure wins over the estimate.
    vi.advanceTimersByTime(600);
    clock.observe(read(10_950));
    expect(clock.audioNow("rec-1")).toBe(10_950);

    vi.advanceTimersByTime(250);
    expect(clock.audioNow("rec-1")).toBe(11_200);
  });

  it("stands still while paused, however long, and runs on again after resume", () => {
    const clock = new AudioClock();
    clock.observe(read(30_000));
    vi.advanceTimersByTime(500);

    clock.observe(paused(30_400));
    vi.advanceTimersByTime(5 * 60_000);
    expect(clock.audioNow("rec-1")).toBe(30_400);

    // Resumed: paused wall time never reaches the audio timeline.
    clock.observe(read(30_400));
    vi.advanceTimersByTime(700);
    expect(clock.audioNow("rec-1")).toBe(31_100);
  });

  it("stands still while the browser finishes uploading after Stop", () => {
    const clock = new AudioClock();
    clock.observe(read(42_000, { client_status: ClientStatus.UPLOADING }));

    vi.advanceTimersByTime(3_000);

    expect(clock.audioNow("rec-1")).toBe(42_000);
  });

  it("ignores a read that was sent before the one it holds and arrived late", () => {
    const clock = new AudioClock();
    const firstSent = clock.stamp();
    vi.advanceTimersByTime(1_000);
    const secondSent = clock.stamp();
    clock.observe(read(20_000), secondSent);

    vi.advanceTimersByTime(300);
    clock.observe(read(19_000), firstSent);

    expect(clock.audioNow("rec-1")).toBe(20_300);
  });

  it("holds the last position once the capture is over", () => {
    const clock = new AudioClock();
    clock.observe(read(50_000));
    vi.advanceTimersByTime(800);

    clock.observe(read(null, { status: RecordingStatus.PROCESSING, client_status: undefined }));
    vi.advanceTimersByTime(10_000);

    expect(clock.audioNow("rec-1")).toBe(50_000);
  });

  it("knows nothing before a read, nor about another recording", () => {
    const clock = new AudioClock();
    expect(clock.audioNow("rec-1")).toBeNull();

    clock.observe(read(5_000));

    expect(clock.audioNow("rec-2")).toBeNull();
  });
});

describe("isCaptureAdvancing", () => {
  it("matches the server's rule", () => {
    expect(isCaptureAdvancing(RecordingStatus.UPLOADING, ClientStatus.RECORDING)).toBe(true);
    expect(isCaptureAdvancing(RecordingStatus.UPLOADING, null)).toBe(true);
    expect(isCaptureAdvancing(RecordingStatus.UPLOADING, ClientStatus.PAUSED)).toBe(false);
    expect(isCaptureAdvancing(RecordingStatus.UPLOADING, ClientStatus.UPLOADING)).toBe(false);
    expect(isCaptureAdvancing(RecordingStatus.PAUSED, ClientStatus.RECORDING)).toBe(false);
  });
});
