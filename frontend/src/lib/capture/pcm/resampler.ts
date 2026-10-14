/**
 * Streaming band-limited resampler for capture audio.
 *
 * The mixer runs at whatever rate the AudioContext chose (48 kHz or 44.1 kHz in
 * practice) and the backend expects 16 kHz. A windowed-sinc kernel low-pass
 * filters below the output Nyquist before decimating, so speech does not alias.
 * The context is left at its native rate on purpose: asking for a 16 kHz
 * context resamples the MediaStream sources inside the browser, and not every
 * supported browser does that.
 *
 * The output count is exact: after `flush`, a stream of N input frames has
 * produced ceil(N * outputRate / inputRate) output frames, one for every output
 * time inside the input, however the input was split into blocks. Segment
 * durations depend on that.
 */

/** Zero crossings of the sinc kept on each side of the centre tap. */
const ZERO_CROSSINGS = 12;

/** Kernel table resolution, in entries per input sample. */
const TABLE_RESOLUTION = 256;

/** Kaiser window shape. Higher trades transition width for stop-band depth. */
const KAISER_BETA = 8;

/** Cut-off as a fraction of the output Nyquist frequency. */
const CUTOFF_FRACTION = 0.92;

const besselI0 = (x: number) => {
  let sum = 1;
  let term = 1;
  const halfX = x / 2;
  for (let k = 1; k < 50; k += 1) {
    term *= (halfX / k) * (halfX / k);
    sum += term;
    if (term < sum * 1e-12) {
      break;
    }
  }
  return sum;
};

const buildKernelTable = (cutoff: number, halfWidth: number) => {
  const size = Math.ceil(halfWidth * TABLE_RESOLUTION) + 2;
  const table = new Float64Array(size);
  const windowNorm = besselI0(KAISER_BETA);
  for (let index = 0; index < size; index += 1) {
    const x = index / TABLE_RESOLUTION;
    if (x >= halfWidth) {
      table[index] = 0;
      continue;
    }
    const ratio = x / halfWidth;
    const window = besselI0(KAISER_BETA * Math.sqrt(1 - ratio * ratio)) / windowNorm;
    const argument = 2 * cutoff * x;
    const sinc = argument === 0 ? 1 : Math.sin(Math.PI * argument) / (Math.PI * argument);
    table[index] = 2 * cutoff * sinc * window;
  }
  return table;
};

export class StreamingResampler {
  readonly inputRate: number;

  readonly outputRate: number;

  readonly channels: number;

  private readonly passthrough: boolean;

  private readonly halfWidth: number;

  private readonly table: Float64Array;

  /** Input history per channel, starting at absolute input frame `historyStart`. */
  private history: Float32Array[];

  private historyLength = 0;

  private historyStart = 0;

  private inputFrames = 0;

  private outputFrames = 0;

  constructor(inputRate: number, outputRate: number, channels: number) {
    if (!Number.isInteger(inputRate) || !Number.isInteger(outputRate)) {
      throw new Error("Sample rates must be whole numbers.");
    }
    this.inputRate = inputRate;
    this.outputRate = outputRate;
    this.channels = channels;
    this.passthrough = inputRate === outputRate;
    const cutoff = (0.5 * Math.min(1, outputRate / inputRate)) * CUTOFF_FRACTION;
    this.halfWidth = ZERO_CROSSINGS / (2 * cutoff);
    this.table = this.passthrough ? new Float64Array(0) : buildKernelTable(cutoff, this.halfWidth);
    this.history = Array.from({ length: channels }, () => new Float32Array(8192));
  }

  /** Total output frames produced so far. */
  get producedFrames() {
    return this.outputFrames;
  }

  /** Feeds a block of input, one array per channel, and returns what it completes. */
  process(block: Float32Array[]): Float32Array[] {
    const frames = block[0]?.length ?? 0;
    if (this.passthrough) {
      this.inputFrames += frames;
      this.outputFrames += frames;
      return block.map((channel) => channel.slice());
    }
    this.append(block, frames);
    return this.drain(false);
  }

  /** Emits every output frame the input so far defines, treating the future as silence. */
  flush(): Float32Array[] {
    if (this.passthrough) {
      return Array.from({ length: this.channels }, () => new Float32Array(0));
    }
    return this.drain(true);
  }

  private append(block: Float32Array[], frames: number) {
    const needed = this.historyLength + frames;
    if (needed > this.history[0].length) {
      const capacity = Math.max(needed, this.history[0].length * 2);
      this.history = this.history.map((channel) => {
        const grown = new Float32Array(capacity);
        grown.set(channel.subarray(0, this.historyLength));
        return grown;
      });
    }
    for (let channel = 0; channel < this.channels; channel += 1) {
      this.history[channel].set(block[channel] ?? new Float32Array(frames), this.historyLength);
    }
    this.historyLength += frames;
    this.inputFrames += frames;
  }

  private drain(final: boolean): Float32Array[] {
    const reach = Math.ceil(this.halfWidth);
    // Output n sits at input time n * inputRate / outputRate. It can be made
    // once the input runs `reach` frames past that time. At the end of the
    // stream everything past the last frame is silence, so every output whose
    // time falls inside the input can be made.
    const horizon = final ? this.inputFrames : this.inputFrames - reach;
    const lastOutput = Math.floor((horizon * this.outputRate - 1) / this.inputRate);
    const count = Math.max(0, lastOutput - this.outputFrames + 1);
    const output = Array.from({ length: this.channels }, () => new Float32Array(count));
    const taps = 2 * reach;
    const weights = new Float64Array(taps);

    for (let produced = 0; produced < count; produced += 1) {
      const n = this.outputFrames + produced;
      const numerator = n * this.inputRate;
      const base = Math.floor(numerator / this.outputRate);
      const fraction = (numerator - base * this.outputRate) / this.outputRate;
      const first = base - reach + 1;
      let weightSum = 0;
      for (let tap = 0; tap < taps; tap += 1) {
        const distance = Math.abs(first + tap - base - fraction) * TABLE_RESOLUTION;
        const lower = Math.floor(distance);
        const weight =
          lower + 1 < this.table.length
            ? this.table[lower] + (this.table[lower + 1] - this.table[lower]) * (distance - lower)
            : 0;
        weights[tap] = weight;
        weightSum += weight;
      }
      // Taps before the stream or past what has arrived read as silence.
      const startTap = Math.max(0, this.historyStart - first);
      const endTap = Math.min(taps, this.historyStart + this.historyLength - first);
      const offset = first - this.historyStart;
      for (let channel = 0; channel < this.channels; channel += 1) {
        const samples = this.history[channel];
        let sum = 0;
        for (let tap = startTap; tap < endTap; tap += 1) {
          sum += weights[tap] * samples[offset + tap];
        }
        output[channel][produced] = weightSum === 0 ? 0 : sum / weightSum;
      }
    }
    this.outputFrames += count;
    this.discardConsumedHistory(reach);
    return output;
  }

  private discardConsumedHistory(reach: number) {
    const nextBase = Math.floor((this.outputFrames * this.inputRate) / this.outputRate);
    const keepFrom = Math.max(this.historyStart, nextBase - reach);
    const drop = Math.min(keepFrom - this.historyStart, this.historyLength);
    if (drop <= 0) {
      return;
    }
    for (let channel = 0; channel < this.channels; channel += 1) {
      this.history[channel].copyWithin(0, drop, this.historyLength);
    }
    this.historyLength -= drop;
    this.historyStart += drop;
  }
}
