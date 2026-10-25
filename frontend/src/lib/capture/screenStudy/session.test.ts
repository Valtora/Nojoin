import { AxiosError } from "axios";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { SpeakerFrameUploadResult } from "@/lib/api/speakerFrames";

import { GRID_COLUMNS, GRID_ROWS } from "./changeRule";
import type { FrameGrabber, FrameSample } from "./frameGrabber";
import type { QueuedFrame } from "./frameQueue";
import { ScreenStudySession, type ScreenStudySessionOptions } from "./session";
import { useScreenStudyStore } from "./store";

class FakeTrack {
  readyState: "live" | "ended" = "live";

  clones: FakeTrack[] = [];

  stop = vi.fn(() => {
    this.readyState = "ended";
  });

  clone() {
    const copy = new FakeTrack();
    this.clones.push(copy);
    return copy as unknown as MediaStreamTrack;
  }
}

/** Serves grids the test sets, as the worker would for each new frame. */
class FakeGrabber implements FrameGrabber {
  level = 50;

  sampleError: Error | null = null;

  stopped = false;

  encodes = 0;

  async sample(): Promise<FrameSample> {
    if (this.sampleError) {
      throw this.sampleError;
    }
    return {
      grid: new Uint8Array(GRID_COLUMNS * GRID_ROWS).fill(this.level),
      width: 1280,
      height: 720,
    };
  }

  async encode() {
    this.encodes += 1;
    return new Blob([`jpeg-${this.encodes}`], { type: "image/jpeg" });
  }

  async framesSeen() {
    return 0;
  }

  stop() {
    this.stopped = true;
  }
}

const settle = async () => {
  for (let index = 0; index < 10; index += 1) {
    await Promise.resolve();
  }
};

const uploaded = (frames: number): SpeakerFrameUploadResult => ({
  status: "stored",
  frames,
  bytes: frames * 1_000,
  delete_after: "2026-11-08T12:00:00.000Z",
  recording_stopped: false,
});

describe("screen study session", () => {
  let track: FakeTrack;
  let grabber: FakeGrabber;
  let sent: QueuedFrame[];
  let audioWaiting: boolean;
  let clock: number;

  const createSession = (overrides: Partial<ScreenStudySessionOptions> = {}) => {
    const session = new ScreenStudySession({
      recordingId: "rec-1",
      displayTrack: track as unknown as MediaStreamTrack,
      audioWaiting: () => audioWaiting,
      upload: async (_recordingId, frame) => {
        sent.push(frame);
        return uploaded(sent.length);
      },
      remove: async () => ({ deleted: sent.length }),
      createGrabber: () => grabber,
      now: () => clock,
      ...overrides,
    });
    session.start();
    return session;
  };

  const tickAt = async (session: ScreenStudySession, sequence: number, offsetMs = 0) => {
    clock += 1_000;
    session.tick({ sequence, offsetMs });
    await settle();
  };

  beforeEach(() => {
    track = new FakeTrack();
    grabber = new FakeGrabber();
    sent = [];
    audioWaiting = false;
    clock = 0;
    useScreenStudyStore.getState().reset();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("works on a clone of the shared track, never the track itself", () => {
    createSession();
    expect(track.clones).toHaveLength(1);
    expect(useScreenStudyStore.getState()).toMatchObject({
      recordingId: "rec-1",
      status: "sampling",
    });
  });

  it("sends a frame when the screen changes, with its audio position", async () => {
    const session = createSession();

    await tickAt(session, 3, 1_000);
    await tickAt(session, 4, 0);
    grabber.level = 80;
    await tickAt(session, 4, 1_000);

    expect(sent.map(({ sequence, offsetMs }) => [sequence, offsetMs])).toEqual([
      [3, 1_000],
      [4, 1_000],
    ]);
    expect(useScreenStudyStore.getState()).toMatchObject({
      frames: 2,
      bytes: 2_000,
      deleteAfter: "2026-11-08T12:00:00.000Z",
    });
  });

  it("sends a still screen again after 15 s", async () => {
    const session = createSession();
    for (let second = 0; second < 16; second += 1) {
      await tickAt(session, second);
    }
    // The first frame, then the keep-alive 15 s later.
    expect(sent.map((frame) => frame.sequence)).toEqual([0, 15]);
  });

  it("takes nothing while the recording is paused", async () => {
    const session = createSession();
    await tickAt(session, 0);
    session.pause();
    expect(useScreenStudyStore.getState().status).toBe("paused");

    grabber.level = 200;
    await tickAt(session, 1);
    expect(sent).toHaveLength(1);

    session.resume();
    expect(useScreenStudyStore.getState().status).toBe("sampling");
    await tickAt(session, 2);
    expect(sent.map((frame) => frame.sequence)).toEqual([0, 2]);
  });

  it("holds frames while audio is waiting to upload", async () => {
    audioWaiting = true;
    const session = createSession();
    await tickAt(session, 0);
    expect(sent).toHaveLength(0);
    expect(useScreenStudyStore.getState().queued).toBe(1);

    audioWaiting = false;
    session.audioQueueChanged();
    await settle();
    expect(sent).toHaveLength(1);
  });

  it("turns itself off when a frame cannot be read, and stops its clone", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    const session = createSession();
    grabber.sampleError = new Error("the track ended");

    await tickAt(session, 0);

    expect(session.status).toBe("stopped");
    expect(grabber.stopped).toBe(true);
    expect(track.stop).not.toHaveBeenCalled();
    expect(useScreenStudyStore.getState()).toMatchObject({
      status: "stopped",
      error: "Sampling stopped: the track ended",
    });
  });

  it("turns itself off when the server refuses a frame", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    const refusal = new AxiosError("Insufficient Storage");
    Object.assign(refusal, { response: { status: 507, data: { detail: "full" } } });
    const session = createSession({ upload: async () => Promise.reject(refusal) });

    await tickAt(session, 0);
    grabber.level = 90;
    await tickAt(session, 1);

    expect(session.status).toBe("stopped");
    expect(grabber.encodes).toBe(1);
    expect(useScreenStudyStore.getState().error).toBe(
      "The screen study has reached its storage limit, so sampling stopped.",
    );
  });

  it("Stop ends sampling and keeps what is queued going", async () => {
    audioWaiting = true;
    const session = createSession();
    await tickAt(session, 0);

    session.stopSampling();
    grabber.level = 120;
    await tickAt(session, 1);

    expect(grabber.stopped).toBe(true);
    audioWaiting = false;
    session.audioQueueChanged();
    await settle();
    expect(sent.map((frame) => frame.sequence)).toEqual([0]);
    expect(useScreenStudyStore.getState().status).toBe("stopped");
  });

  it("drains for 20 s after the recording stops, then lets go", async () => {
    vi.useFakeTimers();
    audioWaiting = true;
    const session = createSession();
    await tickAt(session, 0);
    grabber.level = 99;
    await tickAt(session, 1);

    const finished = session.finish();
    expect(grabber.stopped).toBe(true);
    // The audio drains first, then one frame goes before the time runs out.
    audioWaiting = false;
    session.audioQueueChanged();
    await vi.advanceTimersByTimeAsync(0);
    expect(sent.length).toBeGreaterThanOrEqual(1);

    await vi.advanceTimersByTimeAsync(20_000);
    await finished;
    expect(session.status).toBe("ended");
    expect(useScreenStudyStore.getState().status).toBeNull();
  });

  it("gives up on frames still queued when the 20 s run out", async () => {
    vi.useFakeTimers();
    audioWaiting = true;
    const session = createSession();
    await tickAt(session, 0);

    const finished = session.finish();
    await vi.advanceTimersByTimeAsync(20_000);
    await finished;

    expect(sent).toHaveLength(0);
    expect(session.status).toBe("ended");
  });

  it("Delete stops sampling, drops the queue and deletes on the server", async () => {
    audioWaiting = true;
    const remove = vi.fn(async () => ({ deleted: 0 }));
    const session = createSession({ remove });
    await tickAt(session, 0);

    await session.deleteFrames();

    expect(remove).toHaveBeenCalledWith("rec-1");
    expect(grabber.stopped).toBe(true);
    audioWaiting = false;
    session.audioQueueChanged();
    await settle();
    expect(sent).toHaveLength(0);
    expect(useScreenStudyStore.getState()).toMatchObject({
      status: "deleted",
      frames: 0,
      deleting: false,
    });
  });

  it("keeps nothing in browser storage", async () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    const open = vi.fn();
    vi.stubGlobal("indexedDB", { open });
    const session = createSession();
    for (let second = 0; second < 5; second += 1) {
      grabber.level = 10 * second;
      await tickAt(session, second);
    }
    await session.finish();

    expect(sent).toHaveLength(5);
    expect(setItem).not.toHaveBeenCalled();
    expect(open).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });
});
