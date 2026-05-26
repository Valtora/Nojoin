import { describe, expect, it } from "vitest";

import {
  computeAutomaticGainTarget,
  createCaptureMixer,
  mixToMonoAmplitude,
} from "./mixer";

class FakeAudioNode {
  connections: FakeAudioNode[] = [];

  disconnected = false;

  connect(node: FakeAudioNode) {
    this.connections.push(node);
    return node;
  }

  disconnect() {
    this.disconnected = true;
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

class FakeMediaStreamDestinationNode extends FakeAudioNode {
  stream = {} as MediaStream;
}

class FakeChannelMergerNode extends FakeAudioNode {}

class FakeMediaStreamSourceNode extends FakeAudioNode {
  constructor(readonly stream: MediaStream) {
    super();
  }
}

class FakeAudioContext {
  state: AudioContextState = "suspended";

  resumed = false;

  closed = false;

  createMediaStreamDestination() {
    return new FakeMediaStreamDestinationNode();
  }

  createMediaStreamSource(stream: MediaStream) {
    return new FakeMediaStreamSourceNode(stream);
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

describe("capture mixer", () => {
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

  it("computes bounded automatic gain targets", () => {
    expect(computeAutomaticGainTarget({ rms: 0, peak: 0 })).toBe(1);
    expect(computeAutomaticGainTarget({ rms: 0.02, peak: 0.04 })).toBe(1.8);
    expect(computeAutomaticGainTarget({ rms: 0.9, peak: 1 })).toBe(0.15);
  });

  it("downmixes a synthetic two-source fixture into mono amplitude", () => {
    expect(mixToMonoAmplitude(0.8, 0.2, 1.5, 0.5)).toBeCloseTo(0.65, 5);
    expect(mixToMonoAmplitude(0.6, 0.4, 2.5, -1)).toBeCloseTo(0.6, 5);
  });
});