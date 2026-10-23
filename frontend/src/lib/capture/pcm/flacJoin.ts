/**
 * Joins capture segments, each a self-contained FLAC file, into one FLAC file.
 *
 * Audio left out of a recording is offered as a download (issue #605), and
 * the browser holds it as 2 s segments. Each segment is a stream of its own:
 * its frames are numbered from 0, and it ends on a block shorter than the rest.
 * Placed end to end, those numbers would repeat and the short blocks would sit
 * mid-stream, so every frame header is rewritten with its place in the joined
 * stream and its CRC-8, and the frame's CRC-16, are computed again. The
 * subframes are copied byte for byte, so nothing is decoded or re-encoded and
 * the result holds exactly the samples recorded.
 *
 * Blocks of more than one size make the result a variable-blocksize stream,
 * whose headers carry the number of their first sample rather than a frame
 * number. FLAC allows either, and libFLAC, ffmpeg and browsers read both.
 */

import { crc16, crc8 } from "./flacEncoder";

const FLAC_MARKER = [0x66, 0x4c, 0x61, 0x43];

const STREAMINFO_TYPE = 0;

const STREAMINFO_LENGTH = 34;

/** The largest number a frame header can code: 36 bits, in seven bytes. */
const MAX_CODED_NUMBER = 2 ** 36 - 1;

interface StreamFormat {
  sampleRate: number;
  channels: number;
  bitsPerSample: number;
}

interface SegmentInfo extends StreamFormat {
  totalFrames: number;
  /** Where the first frame begins, after the metadata blocks. */
  audioStart: number;
}

interface FrameHeader {
  blockSize: number;
  /** Where the coded frame or sample number begins. */
  numberStart: number;
  /** Where it ends, and any block size or sample rate written after it begins. */
  numberEnd: number;
  /** Where the CRC-8 sits. */
  crcAt: number;
}

interface Frame {
  bytes: Uint8Array;
  start: number;
  end: number;
  header: FrameHeader;
}

export interface JoinedFlac {
  bytes: Uint8Array<ArrayBuffer>;
  /** Samples per channel. */
  frames: number;
  sampleRate: number;
  /** How many of the segments given went into the file. */
  segments: number;
}

const readSegmentInfo = (bytes: Uint8Array): SegmentInfo | null => {
  if (bytes.length < 8 + STREAMINFO_LENGTH || FLAC_MARKER.some((byte, index) => bytes[index] !== byte)) {
    return null;
  }
  let position = 4;
  let info: Omit<SegmentInfo, "audioStart"> | null = null;
  for (;;) {
    if (position + 4 > bytes.length) {
      return null;
    }
    const last = (bytes[position] & 0x80) !== 0;
    const type = bytes[position] & 0x7f;
    const length = (bytes[position + 1] << 16) | (bytes[position + 2] << 8) | bytes[position + 3];
    const data = position + 4;
    if (data + length > bytes.length) {
      return null;
    }
    if (type === STREAMINFO_TYPE && length === STREAMINFO_LENGTH) {
      const d = (offset: number) => bytes[data + offset];
      info = {
        sampleRate: (d(10) << 12) | (d(11) << 4) | (d(12) >> 4),
        channels: ((d(12) >> 1) & 0x07) + 1,
        bitsPerSample: (((d(12) & 0x01) << 4) | (d(13) >> 4)) + 1,
        totalFrames:
          (d(13) & 0x0f) * 2 ** 32 + ((d(14) << 24) >>> 0) + (d(15) << 16) + (d(16) << 8) + d(17),
      };
    }
    position = data + length;
    if (last) {
      break;
    }
  }
  return info ? { ...info, audioStart: position } : null;
};

/** The length of the coded number whose first byte is `lead`, or null if none starts so. */
const codedNumberLength = (lead: number) => {
  if (lead < 0x80) {
    return 1;
  }
  let ones = 0;
  while (ones < 8 && (lead << ones) & 0x80) {
    ones += 1;
  }
  return ones >= 2 && ones <= 7 ? ones : null;
};

const codedNumber = (value: number): number[] => {
  if (value < 0x80) {
    return [value];
  }
  const limits = [2 ** 11, 2 ** 16, 2 ** 21, 2 ** 26, 2 ** 31, 2 ** 36];
  const continuation = limits.findIndex((limit) => value < limit) + 1;
  const lead = (0xff << (7 - continuation)) & 0xff;
  const bytes = [lead | Math.floor(value / 2 ** (6 * continuation))];
  for (let index = continuation - 1; index >= 0; index -= 1) {
    bytes.push(0x80 | (Math.floor(value / 2 ** (6 * index)) & 0x3f));
  }
  return bytes;
};

const parseFrameHeader = (bytes: Uint8Array, start: number): FrameHeader | null => {
  if (start + 5 > bytes.length || bytes[start] !== 0xff || (bytes[start + 1] & 0xfe) !== 0xf8) {
    return null;
  }
  const blockSizeCode = bytes[start + 2] >> 4;
  const sampleRateCode = bytes[start + 2] & 0x0f;
  const channelCode = bytes[start + 3] >> 4;
  if (blockSizeCode === 0 || sampleRateCode === 0x0f || channelCode > 10 || (bytes[start + 3] & 1)) {
    return null;
  }
  const numberStart = start + 4;
  const numberLength = codedNumberLength(bytes[numberStart]);
  if (numberLength === null || numberStart + numberLength > bytes.length) {
    return null;
  }
  for (let index = 1; index < numberLength; index += 1) {
    if ((bytes[numberStart + index] & 0xc0) !== 0x80) {
      return null;
    }
  }
  const numberEnd = numberStart + numberLength;
  let position = numberEnd;
  let blockSize: number;
  if (blockSizeCode === 0b0001) {
    blockSize = 192;
  } else if (blockSizeCode <= 0b0101) {
    blockSize = 576 << (blockSizeCode - 2);
  } else if (blockSizeCode === 0b0110) {
    blockSize = bytes[position] + 1;
    position += 1;
  } else if (blockSizeCode === 0b0111) {
    blockSize = ((bytes[position] << 8) | bytes[position + 1]) + 1;
    position += 2;
  } else {
    blockSize = 256 << (blockSizeCode - 8);
  }
  if (sampleRateCode === 0b1100) {
    position += 1;
  } else if (sampleRateCode === 0b1101 || sampleRateCode === 0b1110) {
    position += 2;
  }
  if (position >= bytes.length || crc8(bytes.subarray(start, position)) !== bytes[position]) {
    return null;
  }
  return { blockSize, numberStart, numberEnd, crcAt: position };
};

/**
 * Where the frame starting at `start` ends: at the next frame header, or at
 * the end of the file, wherever the bytes before it pass the frame's CRC-16.
 * A frame followed by its own CRC-16 leaves the CRC at zero, so a sync code
 * that turns up inside a frame's audio is passed over.
 */
const findFrameEnd = (bytes: Uint8Array, start: number, header: FrameHeader) => {
  for (let candidate = header.crcAt + 1; candidate + 1 < bytes.length; candidate += 1) {
    if (
      bytes[candidate] === 0xff &&
      (bytes[candidate + 1] & 0xfe) === 0xf8 &&
      parseFrameHeader(bytes, candidate) &&
      crc16(bytes.subarray(start, candidate)) === 0
    ) {
      return candidate;
    }
  }
  return crc16(bytes.subarray(start)) === 0 ? bytes.length : null;
};

/** The segment's frames, or null when it is not a whole, well-formed stream. */
const readFrames = (bytes: Uint8Array, info: SegmentInfo): Frame[] | null => {
  const frames: Frame[] = [];
  let samples = 0;
  let position = info.audioStart;
  while (position < bytes.length) {
    const header = parseFrameHeader(bytes, position);
    const end = header ? findFrameEnd(bytes, position, header) : null;
    if (!header || end === null) {
      return null;
    }
    frames.push({ bytes, start: position, end, header });
    samples += header.blockSize;
    position = end;
  }
  // A STREAMINFO total of 0 means unknown; any other must match.
  if (frames.length === 0 || (info.totalFrames !== 0 && samples !== info.totalFrames)) {
    return null;
  }
  return frames;
};

const sameFormat = (left: StreamFormat, right: StreamFormat) =>
  left.sampleRate === right.sampleRate &&
  left.channels === right.channels &&
  left.bitsPerSample === right.bitsPerSample;

const streamInfoBlock = (
  format: StreamFormat,
  minBlockSize: number,
  maxBlockSize: number,
  totalFrames: number,
) => {
  const block = new Uint8Array(4 + STREAMINFO_LENGTH);
  // The only metadata block, so it is the last one. Frame sizes and the MD5
  // signature are left at zero, which FLAC defines as unknown.
  block.set([0x80, 0, 0, STREAMINFO_LENGTH]);
  const data = 4;
  block[data] = minBlockSize >> 8;
  block[data + 1] = minBlockSize & 0xff;
  block[data + 2] = maxBlockSize >> 8;
  block[data + 3] = maxBlockSize & 0xff;
  block[data + 10] = format.sampleRate >> 12;
  block[data + 11] = (format.sampleRate >> 4) & 0xff;
  block[data + 12] =
    ((format.sampleRate & 0x0f) << 4) | ((format.channels - 1) << 1) | ((format.bitsPerSample - 1) >> 4);
  block[data + 13] =
    (((format.bitsPerSample - 1) & 0x0f) << 4) | (Math.floor(totalFrames / 2 ** 32) & 0x0f);
  block[data + 14] = (totalFrames >>> 24) & 0xff;
  block[data + 15] = (totalFrames >>> 16) & 0xff;
  block[data + 16] = (totalFrames >>> 8) & 0xff;
  block[data + 17] = totalFrames & 0xff;
  return block;
};

/** The frame as it sits in the joined stream: numbered `number`, with fresh CRCs. */
const renumberFrame = (frame: Frame, variableBlocking: boolean, number: number) => {
  const { bytes, start, end, header } = frame;
  const coded = codedNumber(number);
  const headerLength = 4 + coded.length + (header.crcAt - header.numberEnd) + 1;
  const audioLength = end - 2 - (header.crcAt + 1);
  const out = new Uint8Array(headerLength + audioLength + 2);
  out.set([0xff, variableBlocking ? 0xf9 : 0xf8, bytes[start + 2], bytes[start + 3]]);
  out.set(coded, 4);
  out.set(bytes.subarray(header.numberEnd, header.crcAt), 4 + coded.length);
  out[headerLength - 1] = crc8(out.subarray(0, headerLength - 1));
  out.set(bytes.subarray(header.crcAt + 1, end - 2), headerLength);
  const crc = crc16(out.subarray(0, out.length - 2));
  out[out.length - 2] = crc >> 8;
  out[out.length - 1] = crc & 0xff;
  return out;
};

/**
 * Joins FLAC segments, in the order given, into one FLAC file.
 *
 * A segment that is not a whole FLAC stream, or whose sample rate, channels
 * or sample size differ from the first readable one, is left out, so a
 * damaged segment costs only its own audio. Null when none can be read.
 */
export const joinFlacSegments = (segments: Uint8Array[]): JoinedFlac | null => {
  let format: StreamFormat | null = null;
  const frames: Frame[] = [];
  let joined = 0;
  for (const bytes of segments) {
    const info = readSegmentInfo(bytes);
    if (!info || (format && !sameFormat(format, info))) {
      continue;
    }
    const segmentFrames = readFrames(bytes, info);
    if (!segmentFrames) {
      continue;
    }
    format ??= info;
    frames.push(...segmentFrames);
    joined += 1;
  }
  if (!format || frames.length === 0) {
    return null;
  }

  const blockSizes = frames.map((frame) => frame.header.blockSize);
  // FLAC's minimum block size leaves out the last block, which may be short,
  // and a stream whose other blocks are all one size can keep frame numbers.
  const leading = blockSizes.length > 1 ? blockSizes.slice(0, -1) : blockSizes;
  const minBlockSize = leading.reduce((least, size) => Math.min(least, size));
  const maxBlockSize = blockSizes.reduce((most, size) => Math.max(most, size));
  const variableBlocking = minBlockSize !== maxBlockSize;
  const totalFrames = blockSizes.reduce((total, size) => total + size, 0);
  if (totalFrames > MAX_CODED_NUMBER) {
    throw new Error("The joined audio is too long for one FLAC stream.");
  }

  const parts: Uint8Array[] = [
    Uint8Array.from(FLAC_MARKER),
    streamInfoBlock(format, minBlockSize, maxBlockSize, totalFrames),
  ];
  let firstSample = 0;
  frames.forEach((frame, index) => {
    parts.push(renumberFrame(frame, variableBlocking, variableBlocking ? firstSample : index));
    firstSample += frame.header.blockSize;
  });

  const bytes = new Uint8Array(parts.reduce((total, part) => total + part.length, 0));
  let offset = 0;
  for (const part of parts) {
    bytes.set(part, offset);
    offset += part.length;
  }
  return { bytes, frames: totalFrames, sampleRate: format.sampleRate, segments: joined };
};
