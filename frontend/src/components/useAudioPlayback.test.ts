import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RecordingStatus, type Recording } from "@/types";
import { pauseAudioPlayback, seekAudioPlayback, useAudioPlayback } from "./useAudioPlayback";

const live = (overrides: Partial<Recording> = {}) => ({
  id: "rec-1", has_proxy: false, capture_playback_sequence: 1,
  status: RecordingStatus.UPLOADING, ...overrides,
}) as Recording;

function player(recording = live(), stopAt?: number) {
  const audio = document.createElement("audio");
  const media = { time: 0, duration: NaN, paused: true, ended: false };
  Object.defineProperties(audio, {
    // Like a browser, a seek past the loaded duration lands at its end.
    currentTime: {
      get: () => media.time,
      set: (time: number) => { media.time = Number.isFinite(media.duration) ? Math.min(time, media.duration) : time; },
    },
    duration: { get: () => media.duration },
    paused: { get: () => media.paused },
    ended: { get: () => media.ended },
  });
  const event = (name: string) => act(() => { audio.dispatchEvent(new Event(name)); });
  const load = vi.spyOn(audio, "load").mockImplementation(() => {
    // A browser's load also resets the rate to its default.
    audio.playbackRate = audio.defaultPlaybackRate;
    media.time = 0;
    media.paused = true;
    media.ended = false;
    media.duration = NaN;
    audio.dispatchEvent(new Event("pause"));
  });
  const play = vi.spyOn(audio, "play").mockImplementation(async () => {
    media.paused = false;
    media.ended = false;
    audio.dispatchEvent(new Event("play"));
  });
  vi.spyOn(audio, "pause").mockImplementation(() => {
    media.paused = true;
    audio.dispatchEvent(new Event("pause"));
  });
  const ref = { current: audio };
  const onTimeUpdate = vi.fn(() => {
    if (stopAt !== undefined && audio.currentTime >= stopAt) audio.pause();
  });
  const hook = renderHook(({ recording }) => useAudioPlayback(recording, ref, { onTimeUpdate }), {
    initialProps: { recording },
  });
  const metadata = (duration: number) => {
    media.duration = duration;
    event("loadedmetadata");
  };
  return { ...hook, audio, media, load, play, event, metadata, onTimeUpdate };
}

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("growing server playback", () => {
  it("waits without requesting empty audio, then loads the first snapshot", () => {
    const p = player(live({ capture_playback_sequence: -1 }));
    expect(p.load).not.toHaveBeenCalled();
    expect(p.result.current.waiting).toBe(true);
    p.rerender({ recording: live({ capture_playback_sequence: 0 }) });
    expect(p.audio.src).toMatch(/\/v1\/recordings\/rec-1\/capture-stream\?sequence=0$/);
    p.metadata(2.5);
    expect(p.result.current.waiting).toBe(false);
    expect(p.result.current.duration).toBe(2.5);
  });

  it("keeps a paused listener's snapshot until play, then resumes at the same position", () => {
    const p = player();
    p.metadata(10);
    p.media.time = 6.25;
    p.audio.playbackRate = 1.5;
    p.rerender({ recording: live({ capture_playback_sequence: 2 }) });
    p.rerender({ recording: live({ capture_playback_sequence: 3 }) });
    p.event("timeupdate");
    expect(p.load).toHaveBeenCalledTimes(1);
    expect(p.audio.src).toContain("sequence=1");
    act(() => p.result.current.togglePlay());
    expect(p.audio.src).toContain("sequence=3");
    expect(p.media.time).toBe(0); // load resets the browser's media position.
    p.play.mockClear();
    p.metadata(15);
    expect(p.media.time).toBe(6.25);
    expect(p.audio.playbackRate).toBe(1.5);
    expect(p.play).toHaveBeenCalledOnce();
    expect(p.result.current.isPlaying).toBe(true);
  });

  it("seeks a paused listener past its loaded snapshot onto the newer one", async () => {
    const p = player();
    p.metadata(10);
    p.rerender({ recording: live({ capture_playback_sequence: 7 }) });
    act(() => seekAudioPlayback(p.audio, 14));
    expect(p.audio.src).toContain("sequence=7");
    await act(async () => { await p.audio.play(); });
    p.play.mockClear();
    p.metadata(16);
    expect(p.media.time).toBe(14);
    expect(p.play).toHaveBeenCalledOnce();
    expect(p.load).toHaveBeenCalledTimes(2);
  });

  it("defers playing growth until near EOF and resumes at the actual time", async () => {
    const p = player();
    p.metadata(10);
    p.media.time = 3;
    await act(async () => { await p.audio.play(); });
    p.play.mockClear();
    p.rerender({ recording: live({ capture_playback_sequence: 2 }) });
    expect(p.load).toHaveBeenCalledTimes(1);
    p.media.time = 8.5;
    p.event("timeupdate");
    expect(p.load).toHaveBeenCalledTimes(2);
    p.metadata(15);
    expect(p.media.time).toBe(8.5);
    expect(p.play).toHaveBeenCalledOnce();
    expect(p.result.current.isPlaying).toBe(true);
  });

  it("keeps a snippet stopped near EOF when its native pause event is still queued", async () => {
    const p = player(live(), 8.5);
    p.metadata(10);
    p.media.time = 3;
    await act(async () => { await p.audio.play(); });
    p.rerender({ recording: live({ capture_playback_sequence: 2 }) });
    vi.mocked(p.audio.pause).mockImplementation(() => {
      p.media.paused = true;
      queueMicrotask(() => p.audio.dispatchEvent(new Event("pause")));
    });
    p.play.mockClear();
    p.media.time = 8.5;
    await act(async () => { p.audio.dispatchEvent(new Event("timeupdate")); });
    expect(p.load).toHaveBeenCalledTimes(1);
    expect(p.media.time).toBe(8.5);
    expect(p.play).not.toHaveBeenCalled();
    expect(p.result.current.isPlaying).toBe(false);
  });

  it("continues after snapshot EOF when another chunk arrives", async () => {
    const p = player();
    p.metadata(10);
    await act(async () => { await p.audio.play(); });
    p.play.mockClear();
    p.media.time = 10;
    p.media.ended = true;
    p.media.paused = true;
    p.event("pause");
    p.event("ended");
    p.rerender({ recording: live({ capture_playback_sequence: 2 }) });
    p.metadata(15);
    expect(p.media.time).toBe(10);
    expect(p.play).toHaveBeenCalledOnce();
  });

  it("honours an explicit pause while new metadata is loading", async () => {
    const p = player();
    p.metadata(10);
    await act(async () => { await p.audio.play(); });
    p.media.time = 9;
    p.rerender({ recording: live({ capture_playback_sequence: 2 }) });
    p.play.mockClear();
    act(() => p.result.current.togglePlay());
    p.metadata(15);
    expect(p.media.time).toBe(9);
    expect(p.play).not.toHaveBeenCalled();
    expect(p.result.current.isPlaying).toBe(false);
  });

  it("honours an external pause request when loading has already paused the native element", async () => {
    const p = player();
    p.metadata(10);
    await act(async () => { await p.audio.play(); });
    p.media.time = 9;
    p.rerender({ recording: live({ capture_playback_sequence: 2 }) });
    expect(p.media.paused).toBe(true);
    const nativePause = vi.fn();
    p.audio.addEventListener("pause", nativePause);
    vi.mocked(p.audio.pause).mockImplementation(() => {
      if (!p.media.paused) {
        p.media.paused = true;
        p.audio.dispatchEvent(new Event("pause"));
      }
    });
    p.play.mockClear();
    act(() => pauseAudioPlayback(p.audio));
    expect(nativePause).not.toHaveBeenCalled();
    p.metadata(15);
    expect(p.media.time).toBe(9);
    expect(p.play).not.toHaveBeenCalled();
    expect(p.result.current.isPlaying).toBe(false);
  });

  it("does not restart when the listener pauses at the uploaded end", async () => {
    const p = player();
    p.metadata(10);
    await act(async () => { await p.audio.play(); });
    p.media.time = 10;
    p.media.paused = true;
    p.media.ended = true;
    p.event("ended");
    act(() => p.result.current.togglePlay());
    p.play.mockClear();
    p.rerender({ recording: live({ capture_playback_sequence: 2 }) });
    p.metadata(15);
    expect(p.play).not.toHaveBeenCalled();
    expect(p.result.current.isPlaying).toBe(false);
  });

  it("ignores nonfinite duration and restores only after metadata loads", async () => {
    const p = player();
    p.metadata(10);
    p.media.time = 9;
    await act(async () => { await p.audio.play(); });
    p.rerender({ recording: live({ capture_playback_sequence: 2 }) });
    p.media.duration = Infinity;
    p.event("durationchange");
    expect(p.result.current.duration).toBe(10);
    expect(p.media.time).toBe(0);
    p.metadata(15);
    expect(p.media.time).toBe(9);
  });

  it.each([
    { playing: true, status: RecordingStatus.PROCESSED },
    { playing: false, status: RecordingStatus.PROCESSED },
    { playing: true, status: RecordingStatus.ERROR },
    { playing: false, status: RecordingStatus.ERROR },
  ])("hands over to the final proxy after $status with playing=$playing", async ({ playing, status }) => {
    const p = player();
    p.metadata(10);
    p.media.time = 4.75;
    if (playing) await act(async () => { await p.audio.play(); });
    p.play.mockClear();
    p.rerender({ recording: live({ status: RecordingStatus.PROCESSING }) });
    expect(p.load).toHaveBeenCalledTimes(1);
    p.rerender({ recording: live({ status }) });
    expect(p.load).toHaveBeenCalledTimes(1);
    expect(p.audio.src).toContain("capture-stream?sequence=1");
    // The server stops advertising the snapshot once the proxy exists.
    p.rerender({ recording: live({ has_proxy: true, capture_playback_sequence: null, status: RecordingStatus.PROCESSED }) });
    expect(p.audio.src).toMatch(/\/v1\/recordings\/rec-1\/stream$/);
    p.metadata(9.8);
    expect(p.media.time).toBe(4.75);
    expect(p.play).toHaveBeenCalledTimes(playing ? 1 : 0);
    expect(p.result.current.isPlaying).toBe(playing);
  });
});
