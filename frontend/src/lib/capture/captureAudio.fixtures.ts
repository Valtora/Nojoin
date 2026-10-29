/** Graph-aware audio fixture: real PCM worklet, stream identity and stopped-track silence. */
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";

import type { CreateCaptureMixerOptions } from "./mixer";
import type { PcmTapPort } from "./pcm/tapSource";

interface Processor {
  port: PcmTapPort;
  process(inputs: Float32Array[][], outputs: Float32Array[][]): boolean;
}

/** Runs the shipped worklet with asynchronous, ordered message delivery. */
export class CaptureWorklet {
  readonly port: PcmTapPort;
  private readonly processor: Processor;
  holdAcknowledgements = false;
  private acknowledgements: (() => void)[] = [];

  constructor() {
    this.port = {
      onmessage: null,
      postMessage: (data) => queueMicrotask(() => {
        this.processor.port.onmessage?.({ data } as MessageEvent);
      }),
    };
    const deliver = (data: { type: string }) => {
      const send = () => this.port.onmessage?.({ data } as MessageEvent);
      if (data.type === "flushed" && this.holdAcknowledgements) this.acknowledgements.push(send);
      else queueMicrotask(send);
    };
    let ProcessorClass!: new (options: AudioWorkletNodeOptions) => Processor;
    runInNewContext(readFileSync("public/audio-worklets/pcm-tap.js", "utf8"), {
      AudioWorkletProcessor: class { port = { onmessage: null, postMessage: deliver }; },
      registerProcessor: (_name: string, implementation: typeof ProcessorClass) => {
        ProcessorClass = implementation;
      },
      Float32Array,
    });
    this.processor = new ProcessorClass({ processorOptions: { blockFrames: 4096, channelCount: 2 } });
  }

  acknowledge() {
    this.acknowledgements.splice(0).forEach((send) => send());
  }

  get pendingAcknowledgements() { return this.acknowledgements.length; }

  render(channels: Float32Array[]) {
    const output = channels.map((channel) => new Float32Array(channel.length));
    this.processor.process([channels], [output]);
    return output;
  }
}

type Connection = { source: AudioNodeFixture; input: number };

class AudioNodeFixture {
  inputs: Connection[] = [];
  outputs = new Set<AudioNodeFixture>();
  gain = { value: 1 };
  fftSize = 256;
  smoothingTimeConstant = 0;
  private samples: Float32Array = new Float32Array(128);

  constructor(
    readonly kind: "source" | "gain" | "analyser" | "merger" | "worklet",
    private readonly signal: () => number = () => 0,
    private readonly worklet?: CaptureWorklet,
  ) {}

  connect(target: AudioNodeFixture, output = 0, input = 0) {
    if (output !== 0) throw new Error("The capture fixture supports only output zero.");
    target.inputs.push({ source: this, input });
    this.outputs.add(target);
    return target;
  }

  disconnect() {
    this.outputs.forEach((target) => {
      target.inputs = target.inputs.filter(({ source }) => source !== this);
    });
    this.outputs.clear();
  }

  read(frames: number, cache: Map<AudioNodeFixture, Float32Array[]>): Float32Array[] {
    const cached = cache.get(this);
    if (cached) return cached;
    let channels: Float32Array[];
    if (this.kind === "source") {
      channels = [new Float32Array(frames).fill(this.signal())];
    } else if (this.kind === "merger") {
      channels = [new Float32Array(frames), new Float32Array(frames)];
      this.inputs.forEach(({ source, input }) => channels[input].set(source.read(frames, cache)[0]));
    } else {
      channels = this.inputs[0]?.source.read(frames, cache).map((channel) => channel.slice()) ??
        [new Float32Array(frames)];
      if (this.kind === "gain") channels.forEach((channel) => {
        for (let i = 0; i < frames; i += 1) channel[i] *= this.gain.value;
      });
      if (this.worklet) channels = this.worklet.render(channels);
    }
    this.samples = channels.length === 2
      ? Float32Array.from(channels[0], (value, i) => (value + channels[1][i]) / 2)
      : channels[0];
    cache.set(this, channels);
    return channels;
  }

  getByteTimeDomainData(target: Uint8Array) {
    target.forEach((_value, index) => {
      target[index] = Math.max(0, Math.min(255, Math.round(128 + this.samples[index % this.samples.length] * 128)));
    });
  }
}

/** Evaluates connections made by the real mixer, including merger channel indices. */
export class CaptureAudioGraph {
  state: AudioContextState = "running";
  readonly sampleRate = 16_000;
  readonly worklet = new CaptureWorklet();
  readonly signals = new Map<MediaStream, number>();
  private readonly analysers: AudioNodeFixture[] = [];
  audioWorklet = { addModule: async () => {} };

  createMediaStreamSource(stream: MediaStream) {
    return new AudioNodeFixture("source", () => stream.getAudioTracks().some(
      (track) => track.readyState === "live" && track.enabled,
    ) ? this.signals.get(stream) ?? 0 : 0);
  }

  createGain() { return new AudioNodeFixture("gain"); }
  createChannelMerger() { return new AudioNodeFixture("merger"); }
  createAnalyser() {
    const analyser = new AudioNodeFixture("analyser");
    this.analysers.push(analyser);
    return analyser;
  }
  async resume() { this.state = "running"; }
  async close() { this.state = "closed"; }

  mixerOptions(): Pick<CreateCaptureMixerOptions, "audioContextFactory" | "audioWorkletNodeFactory"> {
    return {
      audioContextFactory: () => this as unknown as AudioContext,
      audioWorkletNodeFactory: () => Object.assign(
        new AudioNodeFixture("worklet", undefined, this.worklet), { port: this.worklet.port },
      ) as unknown as AudioWorkletNode,
    };
  }

  render(frames: number) {
    for (let offset = 0; offset < frames; offset += 128) {
      const cache = new Map<AudioNodeFixture, Float32Array[]>();
      this.analysers.forEach((analyser) => analyser.read(Math.min(128, frames - offset), cache));
    }
  }
}
