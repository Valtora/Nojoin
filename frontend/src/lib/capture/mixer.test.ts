import { describe, expect, it } from "vitest";

import { createCaptureMixer, mixToMonoAmplitude } from "./mixer";

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
}

class FakeGainNode extends FakeAudioNode {
  gain = { value: 1 };

  channelCount = 2;

  channelCountMode: ChannelCountMode = "max";
}

class FakeMediaStreamDestinationNode extends FakeAudioNode {
  stream = {} as MediaStream;
}

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

  async resume() {
    this.resumed = true;
    this.state = "running";
  }

  async close() {
    this.closed = true;
  }
}

describe("capture mixer", () => {
  it("resumes a suspended context and propagates gain changes", async () => {
    const context = new FakeAudioContext();
    const mixer = await createCaptureMixer({
      displayStream: {} as MediaStream,
      microphoneStream: {} as MediaStream,
      systemGain: 1.5,
      microphoneGain: 0.5,
      audioContextFactory: () => context as unknown as AudioContext,
    });

    expect(context.resumed).toBe(true);
    expect(mixer.getSystemGain()).toBe(1.5);
    expect(mixer.getMicrophoneGain()).toBe(0.5);

    mixer.setSystemGain(3);
    mixer.setMicrophoneGain(-1);

    expect(mixer.getSystemGain()).toBe(2);
    expect(mixer.getMicrophoneGain()).toBe(0);

    await mixer.dispose();

    expect(context.closed).toBe(true);
  });

  it("downmixes a synthetic two-source fixture into mono amplitude", () => {
    expect(mixToMonoAmplitude(0.8, 0.2, 1.5, 0.5)).toBeCloseTo(0.65, 5);
    expect(mixToMonoAmplitude(0.6, 0.4, 2.5, -1)).toBeCloseTo(0.6, 5);
  });
});