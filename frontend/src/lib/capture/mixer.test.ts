// @vitest-environment node

import { describe, expect, it, vi } from "vitest";

import {
  computeAutomaticGainTarget,
  createCaptureMixer as createRealCaptureMixer,
  mixToMonoAmplitude,
  type CreateCaptureMixerOptions,
} from "./mixer";
import { PCM_TAP_MODULE_URL, PCM_TAP_PROCESSOR_NAME } from "./pcm/tapSource";

class FakeAudioNode {
  connections: FakeAudioNode[] = [];

  disconnected = false;

  connect(node: FakeAudioNode) {
    this.connections.push(node);
    return node;
  }

  disconnect() {
    this.disconnected = true;
    this.connections = [];
  }
}

class FakeAnalyserNode extends FakeAudioNode {
  fftSize = 0;

  smoothingTimeConstant = 0;

  samples = new Uint8Array(256).fill(128);

  getByteTimeDomainData(target: Uint8Array<ArrayBuffer>) {
    target.set(this.samples.slice(0, target.length));
  }
}

class FakeGainNode extends FakeAudioNode {
  gain = { value: 1 };

  channelCount = 2;

  channelCountMode: ChannelCountMode = "max";
}

class FakeWorkletPort {
  sent: unknown[] = [];

  onmessage: ((event: MessageEvent) => void) | null = null;

  postMessage(message: unknown) {
    this.sent.push(message);
  }
}

class FakeAudioWorkletNode extends FakeAudioNode {
  port = new FakeWorkletPort();

  constructor(
    readonly name: string,
    readonly options: AudioWorkletNodeOptions,
  ) {
    super();
  }
}

class FakeChannelMergerNode extends FakeAudioNode {}

class FakeMediaStreamSourceNode extends FakeAudioNode {
  constructor(readonly stream: MediaStream) {
    super();
  }
}

class FakeAudioContext {
  state: AudioContextState = "suspended";

  sampleRate = 48_000;

  loadedModules: string[] = [];

  failModuleLoad = false;

  audioWorklet = {
    addModule: async (url: string) => {
      if (this.failModuleLoad) {
        throw new Error("blocked");
      }
      this.loadedModules.push(url);
    },
  };

  tapNodes: FakeAudioWorkletNode[] = [];

  resumed = false;

  closed = false;

  sources: FakeMediaStreamSourceNode[] = [];

  createMediaStreamSource(stream: MediaStream) {
    const source = new FakeMediaStreamSourceNode(stream);
    this.sources.push(source);
    return source;
  }

  createAnalyser() {
    return new FakeAnalyserNode();
  }

  createGain() {
    return new FakeGainNode();
  }

  createChannelMerger() {
    return new FakeChannelMergerNode();
  }

  async resume() {
    this.resumed = true;
    this.state = "running";
  }

  async close() {
    this.closed = true;
  }
}

const setAlternatingSamples = (
  analyser: AnalyserNode,
  lowSample: number,
  highSample: number,
) => {
  const fakeAnalyser = analyser as unknown as FakeAnalyserNode;
  fakeAnalyser.samples = Uint8Array.from(
    { length: fakeAnalyser.fftSize },
    (_, index) => (index % 2 === 0 ? lowSample : highSample),
  );
};

const createCaptureMixer = (options: CreateCaptureMixerOptions) =>
  createRealCaptureMixer({
    audioWorkletNodeFactory: (context, name, nodeOptions) => {
      const node = new FakeAudioWorkletNode(name, nodeOptions);
      (context as unknown as FakeAudioContext).tapNodes.push(node);
      return node as unknown as AudioWorkletNode;
    },
    ...options,
  });

describe("capture mixer", () => {
  it("replaces only the microphone source while preserving its gain and the shared graph", async () => {
    const context = new FakeAudioContext();
    const display = { getAudioTracks: () => [{}] } as unknown as MediaStream;
    const mixer = await createCaptureMixer({
      displayStream: display,
      microphoneStream: {} as MediaStream,
      microphoneGain: 1.7,
      audioContextFactory: () => context as unknown as AudioContext,
    });
    const [systemSource, oldMic] = context.sources;
    const microphoneConnections = [...oldMic.connections];
    const systemConnections = [...systemSource.connections];
    const pcmSource = mixer.pcmSource;
    const analyser = mixer.microphoneAnalyser;
    const tap = context.tapNodes[0];

    mixer.detachMicrophone();
    mixer.detachMicrophone();
    expect(oldMic.connections).toEqual([]);
    expect(systemSource.connections).toEqual(systemConnections);

    const replacement = {} as MediaStream;
    mixer.replaceMicrophone(replacement);

    expect(context.sources[2].stream).toBe(replacement);
    expect(context.sources[2].connections).toEqual(microphoneConnections);
    expect(mixer.microphoneAnalyser).toBe(analyser);
    expect(mixer.pcmSource).toBe(pcmSource);
    expect(context.tapNodes).toEqual([tap]);
    expect(mixer.getMicrophoneGain()).toBe(1.7);
    expect(systemSource.disconnected).toBe(false);
    expect(context.closed).toBe(false);
    await mixer.dispose();
    expect(context.sources[2].disconnected).toBe(true);
  });

  it("restarts a context the browser suspended", async () => {
    const context = new FakeAudioContext();
    const mixer = await createCaptureMixer({
      microphoneStream: {} as MediaStream,
      audioContextFactory: () => context as unknown as AudioContext,
    });
    context.state = "suspended";
    context.resumed = false;

    await expect(mixer.resumeContext(2_000)).resolves.toBe(true);

    expect(context.resumed).toBe(true);
  });

  it("gives up on a context that stays interrupted after the timeout", async () => {
    vi.useFakeTimers();
    try {
      const context = new FakeAudioContext();
      const mixer = await createCaptureMixer({
        microphoneStream: {} as MediaStream,
        audioContextFactory: () => context as unknown as AudioContext,
      });
      // WebKit's non-standard "interrupted" state, whose resume() can stay pending.
      context.state = "interrupted" as AudioContextState;
      context.resume = () => new Promise<void>(() => {});

      let settled = false;
      const resuming = mixer.resumeContext(2_000).finally(() => { settled = true; });
      await vi.advanceTimersByTimeAsync(1_999);
      expect(settled).toBe(false);
      await vi.advanceTimersByTimeAsync(1);

      await expect(resuming).resolves.toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });

  it("waits for a context that starts rendering again after a moment", async () => {
    vi.useFakeTimers();
    try {
      const context = new FakeAudioContext();
      const mixer = await createCaptureMixer({
        microphoneStream: {} as MediaStream,
        audioContextFactory: () => context as unknown as AudioContext,
      });
      context.state = "suspended";
      context.resume = () => new Promise<void>((resolve) => {
        setTimeout(() => { context.state = "running"; resolve(); }, 100);
      });

      let settled = false;
      const resuming = mixer.resumeContext(2_000).finally(() => { settled = true; });
      await vi.advanceTimersByTimeAsync(99);
      expect(settled).toBe(false);
      await vi.advanceTimersByTimeAsync(1);

      await expect(resuming).resolves.toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });

  it("can dispose a mixer after its microphone was detached", async () => {
    const context = new FakeAudioContext();
    const mixer = await createCaptureMixer({
      microphoneStream: {} as MediaStream,
      audioContextFactory: () => context as unknown as AudioContext,
    });
    mixer.detachMicrophone();
    await mixer.dispose();
    expect(context.closed).toBe(true);
    expect(context.tapNodes[0].port.sent).toContainEqual({ type: "stop" });
  });

  it("taps the two-channel mix between the merger and the mixed analyser", async () => {
    const context = new FakeAudioContext();
    const mixer = await createCaptureMixer({
      displayStream: {} as MediaStream,
      microphoneStream: {} as MediaStream,
      audioContextFactory: () => context as unknown as AudioContext,
    });

    const [tap] = context.tapNodes;
    expect(context.loadedModules).toEqual([PCM_TAP_MODULE_URL]);
    expect(tap.name).toBe(PCM_TAP_PROCESSOR_NAME);
    expect(tap.options).toMatchObject({
      channelCount: 2,
      channelCountMode: "explicit",
      channelInterpretation: "discrete",
      outputChannelCount: [2],
    });
    expect(tap.connections).toEqual([mixer.mixedAnalyser]);
    // Nothing after the mixed analyser, so the graph pulls it, and the tap with it.
    expect((mixer.mixedAnalyser as unknown as FakeAudioNode).connections).toEqual([]);
    expect(mixer.pcmSource.sampleRate).toBe(48_000);
    expect(mixer.pcmSource.channelCount).toBe(2);
  });

  it("stops the tap when disposed", async () => {
    const context = new FakeAudioContext();
    const mixer = await createCaptureMixer({
      displayStream: null,
      microphoneStream: {} as MediaStream,
      audioContextFactory: () => context as unknown as AudioContext,
    });

    await mixer.dispose();

    expect(context.tapNodes[0].port.sent).toContainEqual({ type: "stop" });
    expect(context.tapNodes[0].disconnected).toBe(true);
  });

  it("closes the context when the capture module cannot load", async () => {
    const context = new FakeAudioContext();
    context.failModuleLoad = true;

    await expect(
      createCaptureMixer({
        displayStream: null,
        microphoneStream: {} as MediaStream,
        audioContextFactory: () => context as unknown as AudioContext,
      }),
    ).rejects.toThrow("could not load its audio capture module");
    expect(context.closed).toBe(true);
  });

  it("resumes a suspended context and starts with neutral automatic gain", async () => {
    const context = new FakeAudioContext();
    const mixer = await createCaptureMixer({
      displayStream: {} as MediaStream,
      microphoneStream: {} as MediaStream,
      audioContextFactory: () => context as unknown as AudioContext,
    });

    expect(context.resumed).toBe(true);
    expect(mixer.getSystemGain()).toBe(1);
    expect(mixer.getMicrophoneGain()).toBe(1);

    await mixer.dispose();

    expect(context.closed).toBe(true);
  });

  it("creates a silent system channel for microphone-only capture", async () => {
    const context = new FakeAudioContext();
    const mixer = await createCaptureMixer({
      displayStream: null,
      microphoneStream: {} as MediaStream,
      audioContextFactory: () => context as unknown as AudioContext,
    });

    expect(mixer.getSystemGain()).toBe(1);
    expect(mixer.getMicrophoneGain()).toBe(1);

    await mixer.dispose();

    expect(context.closed).toBe(true);
  });

  it("adjusts source gain from analyser levels", async () => {
    const mixer = await createCaptureMixer({
      displayStream: {} as MediaStream,
      microphoneStream: {} as MediaStream,
      audioContextFactory: () => new FakeAudioContext() as unknown as AudioContext,
    });

    setAlternatingSamples(mixer.systemAnalyser, 0, 255);
    setAlternatingSamples(mixer.microphoneAnalyser, 120, 136);
    setAlternatingSamples(mixer.mixedAnalyser, 128, 128);

    mixer.updateAutomaticGain();

    expect(mixer.getSystemGain()).toBeLessThan(1);
    expect(mixer.getMicrophoneGain()).toBeGreaterThan(1);
  });

  it("applies explicit baseline source gains", async () => {
    const mixer = await createCaptureMixer({
      displayStream: {} as MediaStream,
      microphoneStream: {} as MediaStream,
      systemGain: 1.6,
      microphoneGain: 2.2,
      audioContextFactory: () => new FakeAudioContext() as unknown as AudioContext,
    });

    expect(mixer.getSystemGain()).toBeCloseTo(1.6, 5);
    expect(mixer.getMicrophoneGain()).toBeCloseTo(2.2, 5);

    mixer.applySettings({ systemGain: 0.75, microphoneGain: 1.25 });

    expect(mixer.getSystemGain()).toBeCloseTo(0.75, 5);
    expect(mixer.getMicrophoneGain()).toBeCloseTo(1.25, 5);
  });

  it("computes bounded automatic gain targets", () => {
    expect(computeAutomaticGainTarget({ rms: 0, peak: 0 })).toBe(1);
    expect(computeAutomaticGainTarget({ rms: 0.02, peak: 0.04 })).toBe(1.8);
    expect(computeAutomaticGainTarget({ rms: 0.9, peak: 1 })).toBe(0.15);
  });

  it("downmixes a synthetic two-source fixture into mono amplitude", () => {
    expect(mixToMonoAmplitude(0.8, 0.2, 1.5, 0.5)).toBeCloseTo(0.65, 5);
    expect(mixToMonoAmplitude(0.6, 0.4, 2.5, -1)).toBeCloseTo(0.75, 5);
  });
});
