/**
 * FLAC encoder for 16-bit capture segments.
 *
 * Browser capture uploads each 2 s segment as a self-contained FLAC file:
 * lossless and sample-exact like PCM, at about a third of PCM's size for
 * speech, and a channel that carries silence (no shared tab audio, or the
 * microphone muted) costs a few bytes a block. Browsers ship no FLAC encoder,
 * so this is a small one: fixed predictors of order 0 to 4 with partitioned
 * Rice residuals, which is all the format needs to compress speech well.
 * Compression choices only change the size; every block falls back to verbatim
 * samples when that is smaller, so the output is always a valid stream.
 *
 * The backend decodes with libFLAC, which checks both frame CRCs, so a stream
 * this writes wrongly fails loudly there rather than playing back as noise.
 */

export const FLAC_MIME_TYPE = "audio/flac";

/** Frames per FLAC block: 256 ms at 16 kHz, a standard FLAC size. */
const BLOCK_SIZE = 4096;

/** FLAC's smallest legal block, except for the last one of a stream. */
export const FLAC_MIN_FRAMES = 16;

const BITS_PER_SAMPLE = 16;

/** 15 is the escape code in the 4-bit Rice parameter field. */
const MAX_RICE_PARAMETER = 14;

const MAX_PARTITION_ORDER = 8;

const MAX_FIXED_ORDER = 4;

const SAMPLE_RATE_CODES: Record<number, number> = {
  8000: 0b0100,
  16000: 0b0101,
  22050: 0b0110,
  24000: 0b0111,
  32000: 0b1000,
  44100: 0b1001,
  48000: 0b1010,
  96000: 0b1011,
};

const buildCrcTable = (polynomial: number, width: number) => {
  const table = new Uint16Array(256);
  const topBit = 1 << (width - 1);
  const mask = (1 << width) - 1;
  for (let byte = 0; byte < 256; byte += 1) {
    let crc = byte << (width - 8);
    for (let bit = 0; bit < 8; bit += 1) {
      crc = crc & topBit ? ((crc << 1) ^ polynomial) & mask : (crc << 1) & mask;
    }
    table[byte] = crc;
  }
  return table;
};

const CRC8_TABLE = buildCrcTable(0x07, 8);
const CRC16_TABLE = buildCrcTable(0x8005, 16);

export const crc8 = (bytes: Uint8Array) => {
  let crc = 0;
  for (const byte of bytes) {
    crc = CRC8_TABLE[crc ^ byte];
  }
  return crc;
};

export const crc16 = (bytes: Uint8Array) => {
  let crc = 0;
  for (const byte of bytes) {
    crc = ((crc << 8) & 0xffff) ^ CRC16_TABLE[(crc >> 8) ^ byte];
  }
  return crc;
};

class BitWriter {
  private buffer: Uint8Array;

  private length = 0;

  private current = 0;

  private bitPosition = 0;

  constructor(capacity: number) {
    this.buffer = new Uint8Array(Math.max(capacity, 64));
  }

  get byteLength() {
    return this.length;
  }

  slice(start: number, end = this.length) {
    return this.buffer.subarray(start, end);
  }

  private pushByte(byte: number) {
    if (this.length === this.buffer.length) {
      const grown = new Uint8Array(this.buffer.length * 2);
      grown.set(this.buffer);
      this.buffer = grown;
    }
    this.buffer[this.length] = byte;
    this.length += 1;
  }

  /** Writes the low `bits` bits of a non-negative `value`, most significant first. */
  writeBits(value: number, bits: number) {
    let remaining = bits;
    while (remaining > 0) {
      const free = 8 - this.bitPosition;
      const take = Math.min(free, remaining);
      const shift = remaining - take;
      const chunk = Math.floor(value / 2 ** shift) % (1 << take);
      this.current |= chunk << (free - take);
      this.bitPosition += take;
      remaining -= take;
      if (this.bitPosition === 8) {
        this.pushByte(this.current);
        this.current = 0;
        this.bitPosition = 0;
      }
    }
  }

  writeSigned(value: number, bits: number) {
    this.writeBits(value < 0 ? value + 2 ** bits : value, bits);
  }

  /** `count` zero bits followed by a one. */
  writeUnary(count: number) {
    let zeros = count;
    while (zeros > 0) {
      const take = Math.min(8 - this.bitPosition, zeros);
      this.bitPosition += take;
      zeros -= take;
      if (this.bitPosition === 8) {
        this.pushByte(this.current);
        this.current = 0;
        this.bitPosition = 0;
      }
    }
    this.writeBits(1, 1);
  }

  alignToByte() {
    if (this.bitPosition > 0) {
      this.pushByte(this.current);
      this.current = 0;
      this.bitPosition = 0;
    }
  }

  toBytes() {
    this.alignToByte();
    return this.buffer.slice(0, this.length);
  }
}

const writeUtf8Number = (writer: BitWriter, value: number) => {
  if (value < 0x80) {
    writer.writeBits(value, 8);
    return;
  }
  const limits = [0x800, 0x10000, 0x200000, 0x4000000, 0x80000000];
  const continuation = limits.findIndex((limit) => value < limit) + 1;
  if (continuation === 0) {
    throw new Error("FLAC frame number is out of range.");
  }
  const leadMarker = (0xff << (7 - continuation)) & 0xff;
  writer.writeBits(leadMarker | Math.floor(value / 2 ** (6 * continuation)), 8);
  for (let index = continuation - 1; index >= 0; index -= 1) {
    writer.writeBits(0x80 | (Math.floor(value / 2 ** (6 * index)) & 0x3f), 8);
  }
};

const zigzag = (residual: number) => (residual >= 0 ? residual * 2 : -residual * 2 - 1);

const fixedResiduals = (samples: Int32Array, order: number) => {
  const count = samples.length - order;
  const residuals = new Int32Array(Math.max(count, 0));
  for (let index = order; index < samples.length; index += 1) {
    const x = samples;
    let prediction = 0;
    switch (order) {
      case 1:
        prediction = x[index - 1];
        break;
      case 2:
        prediction = 2 * x[index - 1] - x[index - 2];
        break;
      case 3:
        prediction = 3 * x[index - 1] - 3 * x[index - 2] + x[index - 3];
        break;
      case 4:
        prediction = 4 * x[index - 1] - 6 * x[index - 2] + 4 * x[index - 3] - x[index - 4];
        break;
      default:
        prediction = 0;
    }
    residuals[index - order] = x[index] - prediction;
  }
  return residuals;
};

interface RiceLayout {
  partitionOrder: number;
  parameters: number[];
  bits: number;
}

const riceCost = (sum: number, count: number) => {
  // Estimated with sum >> k standing in for the sum of each value >> k. The
  // estimate only steers the choice; the stream is exact whatever it picks.
  const mean = count > 0 ? sum / count : 0;
  let parameter = mean > 1 ? Math.min(MAX_RICE_PARAMETER, Math.floor(Math.log2(mean))) : 0;
  let bits = count * (parameter + 1) + Math.floor(sum / 2 ** parameter);
  if (parameter < MAX_RICE_PARAMETER) {
    const next = count * (parameter + 2) + Math.floor(sum / 2 ** (parameter + 1));
    if (next < bits) {
      parameter += 1;
      bits = next;
    }
  }
  return { parameter, bits };
};

const chooseRiceLayout = (
  zigzagged: Float64Array,
  blockSize: number,
  order: number,
): RiceLayout => {
  let maxOrder = 0;
  while (
    maxOrder < MAX_PARTITION_ORDER &&
    blockSize % (1 << (maxOrder + 1)) === 0 &&
    blockSize >> (maxOrder + 1) > order
  ) {
    maxOrder += 1;
  }

  // Sums for the finest partitioning, then merged pairwise for coarser ones.
  // Partition 0 is short by `order` samples at every level, so merging the
  // finest partitions gives exactly the coarser partitions.
  const finest = 1 << maxOrder;
  const finestSize = blockSize >> maxOrder;
  let sums = new Float64Array(finest);
  let counts = new Float64Array(finest);
  for (let partition = 0; partition < finest; partition += 1) {
    const start = partition === 0 ? 0 : partition * finestSize - order;
    const end = (partition + 1) * finestSize - order;
    let sum = 0;
    for (let index = start; index < end; index += 1) {
      sum += zigzagged[index];
    }
    sums[partition] = sum;
    counts[partition] = end - start;
  }

  let best: RiceLayout | null = null;
  for (let partitionOrder = maxOrder; partitionOrder >= 0; partitionOrder -= 1) {
    const parameters: number[] = [];
    let bits = 0;
    for (let partition = 0; partition < sums.length; partition += 1) {
      const cost = riceCost(sums[partition], counts[partition]);
      parameters.push(cost.parameter);
      bits += 4 + cost.bits;
    }
    if (!best || bits < best.bits) {
      best = { partitionOrder, parameters, bits };
    }
    if (partitionOrder > 0) {
      const merged = sums.length / 2;
      const mergedSums = new Float64Array(merged);
      const mergedCounts = new Float64Array(merged);
      for (let index = 0; index < merged; index += 1) {
        mergedSums[index] = sums[2 * index] + sums[2 * index + 1];
        mergedCounts[index] = counts[2 * index] + counts[2 * index + 1];
      }
      sums = mergedSums;
      counts = mergedCounts;
    }
  }
  return best as RiceLayout;
};

const writeResiduals = (
  writer: BitWriter,
  zigzagged: Float64Array,
  blockSize: number,
  order: number,
  layout: RiceLayout,
) => {
  writer.writeBits(0b00, 2);
  writer.writeBits(layout.partitionOrder, 4);
  const partitions = 1 << layout.partitionOrder;
  const size = blockSize >> layout.partitionOrder;
  for (let partition = 0; partition < partitions; partition += 1) {
    const parameter = layout.parameters[partition];
    writer.writeBits(parameter, 4);
    const start = partition === 0 ? 0 : partition * size - order;
    const end = (partition + 1) * size - order;
    const divisor = 2 ** parameter;
    for (let index = start; index < end; index += 1) {
      const value = zigzagged[index];
      writer.writeUnary(Math.floor(value / divisor));
      if (parameter > 0) {
        writer.writeBits(value % divisor, parameter);
      }
    }
  }
};

const writeSubframe = (writer: BitWriter, samples: Int32Array) => {
  const blockSize = samples.length;
  if (samples.every((value) => value === samples[0])) {
    writer.writeBits(0b00000000, 8);
    writer.writeSigned(samples[0], BITS_PER_SAMPLE);
    return;
  }

  let bestOrder = -1;
  let bestBits = 8 + blockSize * BITS_PER_SAMPLE;
  let bestLayout: RiceLayout | null = null;
  let bestZigzagged: Float64Array | null = null;
  for (let order = 0; order <= Math.min(MAX_FIXED_ORDER, blockSize - 1); order += 1) {
    const residuals = fixedResiduals(samples, order);
    const zigzagged = Float64Array.from(residuals, zigzag);
    const layout = chooseRiceLayout(zigzagged, blockSize, order);
    const bits = 8 + order * BITS_PER_SAMPLE + 6 + layout.bits;
    if (bits < bestBits) {
      bestOrder = order;
      bestBits = bits;
      bestLayout = layout;
      bestZigzagged = zigzagged;
    }
  }

  if (bestOrder < 0 || !bestLayout || !bestZigzagged) {
    writer.writeBits(0b00000010, 8);
    for (const sample of samples) {
      writer.writeSigned(sample, BITS_PER_SAMPLE);
    }
    return;
  }

  writer.writeBits((0b001000 | bestOrder) << 1, 8);
  for (let index = 0; index < bestOrder; index += 1) {
    writer.writeSigned(samples[index], BITS_PER_SAMPLE);
  }
  writeResiduals(writer, bestZigzagged, blockSize, bestOrder, bestLayout);
};

const writeFrame = (
  writer: BitWriter,
  interleaved: Int16Array,
  channels: number,
  sampleRate: number,
  frameNumber: number,
  firstFrame: number,
  blockSize: number,
) => {
  writer.alignToByte();
  const frameStart = writer.byteLength;
  const blockSizeCode =
    blockSize === BLOCK_SIZE ? 0b1100 : blockSize <= 256 ? 0b0110 : 0b0111;

  writer.writeBits(0b11111111111110, 14);
  writer.writeBits(0, 1);
  writer.writeBits(0, 1);
  writer.writeBits(blockSizeCode, 4);
  writer.writeBits(SAMPLE_RATE_CODES[sampleRate] ?? 0b0000, 4);
  writer.writeBits(channels - 1, 4);
  writer.writeBits(0b100, 3);
  writer.writeBits(0, 1);
  writeUtf8Number(writer, frameNumber);
  if (blockSizeCode === 0b0110) {
    writer.writeBits(blockSize - 1, 8);
  } else if (blockSizeCode === 0b0111) {
    writer.writeBits(blockSize - 1, 16);
  }
  writer.writeBits(crc8(writer.slice(frameStart)), 8);

  const channelSamples = new Int32Array(blockSize);
  for (let channel = 0; channel < channels; channel += 1) {
    for (let index = 0; index < blockSize; index += 1) {
      channelSamples[index] = interleaved[(firstFrame + index) * channels + channel];
    }
    writeSubframe(writer, channelSamples);
  }

  writer.alignToByte();
  writer.writeBits(crc16(writer.slice(frameStart)), 16);
};

/**
 * Encodes interleaved 16-bit PCM as one FLAC file.
 *
 * Needs at least `FLAC_MIN_FRAMES` frames: FLAC has no legal stream shorter
 * than one minimum block.
 */
export const encodeFlac = (
  interleaved: Int16Array,
  channels: number,
  sampleRate: number,
): Uint8Array => {
  if (channels < 1 || channels > 8) {
    throw new Error("FLAC carries one to eight channels.");
  }
  const frames = Math.floor(interleaved.length / channels);
  if (frames < FLAC_MIN_FRAMES) {
    throw new Error(`FLAC needs at least ${FLAC_MIN_FRAMES} frames.`);
  }

  const writer = new BitWriter(interleaved.length + 1024);
  writer.writeBits(0x664c6143, 32);

  const streamBlockSize = frames > BLOCK_SIZE ? BLOCK_SIZE : frames;
  writer.writeBits(0b1, 1);
  writer.writeBits(0, 7);
  writer.writeBits(34, 24);
  writer.writeBits(streamBlockSize, 16);
  writer.writeBits(streamBlockSize, 16);
  writer.writeBits(0, 24);
  writer.writeBits(0, 24);
  writer.writeBits(sampleRate, 20);
  writer.writeBits(channels - 1, 3);
  writer.writeBits(BITS_PER_SAMPLE - 1, 5);
  writer.writeBits(frames, 36);
  for (let index = 0; index < 4; index += 1) {
    writer.writeBits(0, 32);
  }

  for (
    let frameNumber = 0, firstFrame = 0;
    firstFrame < frames;
    frameNumber += 1, firstFrame += BLOCK_SIZE
  ) {
    const blockSize = Math.min(BLOCK_SIZE, frames - firstFrame);
    writeFrame(
      writer,
      interleaved,
      channels,
      sampleRate,
      frameNumber,
      firstFrame,
      blockSize,
    );
  }

  return writer.toBytes();
};
