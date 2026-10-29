import { vi } from "vitest";

/**
 * The browser media a real CaptureController records through: desktop Chrome's
 * user agent, getDisplayMedia and getUserMedia, and an AudioContext whose PCM
 * tap answers every flush. No audio arrives, so a capture records nothing, as
 * in a silent meeting.
 *
 *     beforeEach(() => { browser = installCaptureBrowser(); });
 *     afterEach(uninstallCaptureBrowser);
 */

const CHROME_USER_AGENT =
  "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36";

class FakeAudioNode {
  connect(node: unknown) {
    return node;
  }

  disconnect() {}
}

class FakeAnalyserNode extends FakeAudioNode {
  fftSize = 0;

  smoothingTimeConstant = 0;

  getByteTimeDomainData(target: Uint8Array) {
    target.fill(128);
  }
}

class FakeGainNode extends FakeAudioNode {
  gain = { value: 1 };
}

class FakeTapPort {
  onmessage: ((event: MessageEvent) => void) | null = null;

  postMessage(message: { type?: string; id?: number }) {
    if (message.type === "flush") {
      queueMicrotask(() =>
        this.onmessage?.({ data: { type: "flushed", id: message.id } } as MessageEvent),
      );
    }
  }
}

class FakeAudioWorkletNode extends FakeAudioNode {
  port = new FakeTapPort();
}

class FakeAudioContext {
  state: AudioContextState = "running";

  sampleRate = 48_000;

  audioWorklet = { addModule: async () => {} };

  createMediaStreamSource() {
    return new FakeAudioNode();
  }

  createAnalyser() {
    return new FakeAnalyserNode();
  }

  createGain() {
    return new FakeGainNode();
  }

  createChannelMerger() {
    return new FakeAudioNode();
  }

  async resume() {}

  async close() {}
}

const fakeTrack = (kind: "audio" | "video", label: string) => {
  const endedListeners = new Set<() => void>();
  return {
    kind,
    label,
    enabled: true,
    muted: false,
    readyState: "live",
    stop: vi.fn(),
    getSettings: () => ({}),
    addEventListener: (type: string, listener: () => void) => {
      if (type === "ended") endedListeners.add(listener);
    },
    removeEventListener: (type: string, listener: () => void) => {
      if (type === "ended") endedListeners.delete(listener);
    },
    /** The browser ending the track, as its "Stop sharing" bar does. */
    end: () => endedListeners.forEach((listener) => listener()),
  } as unknown as MediaStreamTrack & { end: () => void };
};

const fakeStream = (tracks: MediaStreamTrack[]) =>
  ({
    getTracks: () => tracks,
    getAudioTracks: () => tracks.filter((track) => track.kind === "audio"),
    getVideoTracks: () => tracks.filter((track) => track.kind === "video"),
  }) as unknown as MediaStream;

export interface FakeCaptureBrowser {
  mediaDevices: {
    getUserMedia: ReturnType<typeof vi.fn>;
    getDisplayMedia: ReturnType<typeof vi.fn>;
    enumerateDevices: ReturnType<typeof vi.fn>;
  };
  /** The microphone track of the latest grant. */
  microphone: () => MediaStreamTrack | null;
  /** The user ends the latest screen share from the browser's own bar. */
  endScreenShare: () => void;
}

export const installCaptureBrowser = (): FakeCaptureBrowser => {
  let microphone: MediaStreamTrack | null = null;
  let sharedTab: ReturnType<typeof fakeTrack> | null = null;
  const mediaDevices = {
    getUserMedia: vi.fn(async () => {
      microphone = fakeTrack("audio", "Microphone");
      return fakeStream([microphone]);
    }),
    getDisplayMedia: vi.fn(async () => {
      sharedTab = fakeTrack("video", "Meeting tab");
      return fakeStream([fakeTrack("audio", "Tab audio"), sharedTab]);
    }),
    enumerateDevices: vi.fn(async () => []),
  };
  vi.stubGlobal("AudioContext", FakeAudioContext);
  vi.stubGlobal("AudioWorkletNode", FakeAudioWorkletNode);
  Object.defineProperty(navigator, "mediaDevices", { value: mediaDevices, configurable: true });
  Object.defineProperty(navigator, "userAgent", { value: CHROME_USER_AGENT, configurable: true });
  return {
    mediaDevices,
    microphone: () => microphone,
    endScreenShare: () => sharedTab?.end(),
  };
};

export const uninstallCaptureBrowser = () => {
  vi.unstubAllGlobals();
  Reflect.deleteProperty(navigator, "mediaDevices");
  Reflect.deleteProperty(navigator, "userAgent");
};
