/**
 * A FLAC decoder for tests: just the subset `encodeFlac` writes.
 *
 * STREAMINFO, fixed-blocksize frames, and constant, verbatim and fixed
 * subframes with Rice-coded residuals. It checks both CRCs, so a round trip
 * through it proves the stream is well formed as well as lossless. The backend
 * test of the golden fixture checks the same stream against libFLAC.
 */

import { crc16, crc8 } from "./flacEncoder";

class BitReader {
  private position = 0;

  constructor(private readonly bytes: Uint8Array) {}

  get bytePosition() {
    return this.position >> 3;
  }

  get exhausted() {
    return this.position >= this.bytes.length * 8;
  }

  readBits(count: number): number {
    let value = 0;
    for (let index = 0; index < count; index += 1) {
      const byte = this.bytes[this.position >> 3];
      if (byte === undefined) {
        throw new Error("Unexpected end of FLAC stream.");
      }
      const bit = (byte >> (7 - (this.position & 7))) & 1;
      value = value * 2 + bit;
      this.position += 1;
    }
    return value;
  }

  readSigned(count: number) {
    const value = this.readBits(count);
    return value >= 2 ** (count - 1) ? value - 2 ** count : value;
  }

  readUnary() {
    let count = 0;
    while (this.readBits(1) === 0) {
      count += 1;
    }
    return count;
  }

  alignToByte() {
    this.position = Math.ceil(this.position / 8) * 8;
  }
}

const readUtf8Number = (reader: BitReader) => {
  const lead = reader.readBits(8);
  if (lead < 0x80) {
    return lead;
  }
  let continuation = 0;
  while ((lead << continuation) & 0x40) {
    continuation += 1;
  }
  let value = lead & (0x3f >> continuation);
  for (let index = 0; index < continuation; index += 1) {
    value = value * 64 + (reader.readBits(8) & 0x3f);
  }
  return value;
};

const predict = (samples: number[], order: number, index: number) => {
  const x = samples;
  switch (order) {
    case 0:
      return 0;
    case 1:
      return x[index - 1];
    case 2:
      return 2 * x[index - 1] - x[index - 2];
    case 3:
      return 3 * x[index - 1] - 3 * x[index - 2] + x[index - 3];
    default:
      return 4 * x[index - 1] - 6 * x[index - 2] + 4 * x[index - 3] - x[index - 4];
  }
};

const readSubframe = (reader: BitReader, blockSize: number, bits: number) => {
  if (reader.readBits(1) !== 0) {
    throw new Error("Subframe padding bit is set.");
  }
  const type = reader.readBits(6);
  if (reader.readBits(1) !== 0) {
    throw new Error("Wasted bits are not written by the encoder.");
  }
  if (type === 0) {
    const value = reader.readSigned(bits);
    return new Array<number>(blockSize).fill(value);
  }
  if (type === 1) {
    return Array.from({ length: blockSize }, () => reader.readSigned(bits));
  }
  if ((type & 0b111000) !== 0b001000) {
    throw new Error(`Unexpected subframe type ${type}.`);
  }
  const order = type & 0b111;
  const samples: number[] = [];
  for (let index = 0; index < order; index += 1) {
    samples.push(reader.readSigned(bits));
  }
  if (reader.readBits(2) !== 0) {
    throw new Error("Only 4-bit Rice parameters are written.");
  }
  const partitionOrder = reader.readBits(4);
  const partitions = 1 << partitionOrder;
  const size = blockSize >> partitionOrder;
  for (let partition = 0; partition < partitions; partition += 1) {
    const parameter = reader.readBits(4);
    const count = partition === 0 ? size - order : size;
    for (let index = 0; index < count; index += 1) {
      const quotient = reader.readUnary();
      const remainder = parameter > 0 ? reader.readBits(parameter) : 0;
      const folded = quotient * 2 ** parameter + remainder;
      const residual = folded % 2 === 0 ? folded / 2 : -(folded + 1) / 2;
      samples.push(residual + predict(samples, order, samples.length));
    }
  }
  return samples;
};

export interface DecodedFlac {
  sampleRate: number;
  channels: number;
  bitsPerSample: number;
  totalFrames: number;
  minBlockSize: number;
  maxBlockSize: number;
  samples: Int16Array;
}

export const decodeFlac = (bytes: Uint8Array): DecodedFlac => {
  const reader = new BitReader(bytes);
  if (reader.readBits(32) !== 0x664c6143) {
    throw new Error("Missing fLaC marker.");
  }
  let last = false;
  let info: Omit<DecodedFlac, "samples"> | null = null;
  while (!last) {
    last = reader.readBits(1) === 1;
    const type = reader.readBits(7);
    const length = reader.readBits(24);
    if (type === 0) {
      const minBlockSize = reader.readBits(16);
      const maxBlockSize = reader.readBits(16);
      reader.readBits(24);
      reader.readBits(24);
      const sampleRate = reader.readBits(20);
      const channels = reader.readBits(3) + 1;
      const bitsPerSample = reader.readBits(5) + 1;
      const totalFrames = reader.readBits(36);
      reader.readBits(128);
      info = { sampleRate, channels, bitsPerSample, totalFrames, minBlockSize, maxBlockSize };
    } else {
      reader.readBits(length * 8);
    }
  }
  if (!info) {
    throw new Error("Missing STREAMINFO.");
  }

  const samples = new Int16Array(info.totalFrames * info.channels);
  let written = 0;
  let expectedFrameNumber = 0;
  while (!reader.exhausted) {
    const frameStart = reader.bytePosition;
    if (reader.readBits(14) !== 0b11111111111110) {
      throw new Error("Lost frame sync.");
    }
    reader.readBits(2);
    const blockSizeCode = reader.readBits(4);
    reader.readBits(4);
    const channels = reader.readBits(4) + 1;
    reader.readBits(4);
    const frameNumber = readUtf8Number(reader);
    if (frameNumber !== expectedFrameNumber) {
      throw new Error(`Frame ${frameNumber} where ${expectedFrameNumber} was expected.`);
    }
    expectedFrameNumber += 1;
    let blockSize = 4096;
    if (blockSizeCode === 0b0110) {
      blockSize = reader.readBits(8) + 1;
    } else if (blockSizeCode === 0b0111) {
      blockSize = reader.readBits(16) + 1;
    } else if (blockSizeCode !== 0b1100) {
      throw new Error(`Unexpected block size code ${blockSizeCode}.`);
    }
    const headerCrc = crc8(bytes.subarray(frameStart, reader.bytePosition));
    if (reader.readBits(8) !== headerCrc) {
      throw new Error("Frame header CRC mismatch.");
    }
    const channelSamples = Array.from({ length: channels }, () =>
      readSubframe(reader, blockSize, info.bitsPerSample),
    );
    reader.alignToByte();
    const frameCrc = crc16(bytes.subarray(frameStart, reader.bytePosition));
    if (reader.readBits(16) !== frameCrc) {
      throw new Error("Frame CRC mismatch.");
    }
    for (let index = 0; index < blockSize; index += 1) {
      for (let channel = 0; channel < channels; channel += 1) {
        samples[written] = channelSamples[channel][index];
        written += 1;
      }
    }
  }
  if (written !== samples.length) {
    throw new Error(`Decoded ${written} samples, STREAMINFO promised ${samples.length}.`);
  }
  return { ...info, samples };
};
