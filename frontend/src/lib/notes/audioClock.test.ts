// @vitest-environment node

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

  it("runs on between polls while recording, resyncs on each poll, and never runs backwards", () => {
    const clock = new AudioClock();
    clock.observe(read(10_000));

    vi.advanceTimersByTime(400);
    expect(clock.audioNow("rec-1")).toBe(10_400);

    // A second on, it has run to 11 000, and the next poll says 10 950.
    vi.advanceTimersByTime(600);
    expect(clock.audioNow("rec-1")).toBe(11_000);
    clock.observe(read(10_950));
    // Held rather than set back, so a note sent now never lands before one
    // sent a moment ago...
    expect(clock.audioNow("rec-1")).toBe(11_000);
    vi.advanceTimersByTime(30);
    expect(clock.audioNow("rec-1")).toBe(11_000);
    // ...until the server's figure catches up, and on from there.
    vi.advanceTimersByTime(70);
    expect(clock.audioNow("rec-1")).toBe(11_050);
    // A poll ahead of it wins at once.
    clock.observe(read(12_000));
    expect(clock.audioNow("rec-1")).toBe(12_000);
  });

  it("never runs backwards across a late segment, where the server stops running on", () => {
    let now = 0;
    const clock = new AudioClock(() => now);
    // The last segment ends at 8 000, received at 0; the next is late and
    // lands at 3 100. The server runs on at most 2.5 s past a segment.
    const serverAt = (t: number) =>
      t < 3_100 ? 8_000 + Math.min(t, 2_500) : 10_000 + Math.min(t - 3_100, 2_500);
    const seen: number[] = [];
    for (let t = 0; t <= 5_000; t += 50) {
      now = t;
      if (t % 1_000 === 0) clock.observe(read(serverAt(t)), t);
      seen.push(clock.audioNow("rec-1")!);
    }
    const drops = seen.filter((value, index) => index > 0 && value < seen[index - 1]);
    expect(drops).toEqual([]);
  });

  it("stands still while paused, however long, and runs on again after resume", () => {
    const clock = new AudioClock();
    clock.observe(read(30_000));
    vi.advanceTimersByTime(500);
    expect(clock.audioNow("rec-1")).toBe(30_500);

    // The paused figure is exact: it wins even below what the clock said.
    clock.observe(paused(30_400));
    expect(clock.audioNow("rec-1")).toBe(30_400);
    vi.advanceTimersByTime(5 * 60_000);
    expect(clock.audioNow("rec-1")).toBe(30_400);

    // Resumed: paused wall time never reaches the audio timeline, and the
    // 30_500 the clock ran on to before the pause is not held over it.
    clock.observe(read(30_400));
    expect(clock.audioNow("rec-1")).toBe(30_400);
    vi.advanceTimersByTime(700);
    expect(clock.audioNow("rec-1")).toBe(31_100);
  });

  it("starts afresh on another recording", () => {
    const clock = new AudioClock();
    clock.observe(read(50_000));
    vi.advanceTimersByTime(1_000);
    expect(clock.audioNow("rec-1")).toBe(51_000);

    clock.observe(read(1_000, { id: "rec-2" }));
    expect(clock.audioNow("rec-2")).toBe(1_000);
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
