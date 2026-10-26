/*
 * The screen study's frame reader (#653), run as a dedicated worker.
 *
 * The page hands it the readable side of a MediaStreamTrackProcessor on a
 * clone of the shared screen's video track, or the clone itself where only a
 * worker can make the processor. It keeps the newest frame and closes the rest,
 * which works with the Nojoin tab hidden, as it is through a meeting.
 *
 * On "sample" it draws the newest frame at most 1920 px on its long edge, with
 * high-quality smoothing so a line a pixel or two wide on a 4K screen is
 * averaged into the smaller frame rather than skipped, and answers with a
 * 64 x 36 grid of block averages of red, green and blue, which the page
 * compares with the last frame it kept, the frame's timestamp and when it drew
 * it. On "encode" it answers with that same drawing as a JPEG at quality 0.8.
 * Pixels leave this worker only as that JPEG, sent back to the page that asked
 * for it. When the stream ends it says "ended"; on "stop" it closes its
 * stream and answers "stopped".
 *
 * A static file, like the PCM tap worklet, so it needs no bundler support and
 * loads only when a study starts.
 */
"use strict";

const LONG_EDGE_PX = 1920;
const COLUMNS = 64;
const ROWS = 36;
// Red, green and blue, in that order within each block.
const CHANNELS = 3;
const JPEG_QUALITY = 0.8;

let reader = null;
let latest = null;
let framesSeen = 0;
let stopped = false;
let canvas = null;
let context = null;
let drawn = false;

const describe = (error) =>
  error && typeof error.message === "string" ? error.message : String(error);

const fail = (message, id) => {
  self.postMessage({ type: "error", id, message });
};

const pump = async (readable) => {
  reader = readable.getReader();
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done || stopped) {
        if (value) {
          value.close();
        }
        if (done && !stopped) {
          // The share ended: tell the page, so it stops sampling.
          self.postMessage({ type: "ended" });
        }
        break;
      }
      framesSeen += 1;
      if (latest) {
        latest.close();
      }
      latest = value;
    }
  } catch (error) {
    if (!stopped) {
      fail(`The shared screen could not be read: ${describe(error)}`);
    }
  }
};

const blockGrid = (pixels, width, height) => {
  const cells = COLUMNS * ROWS;
  // Integer sums: a block holds a few hundred pixels of at most 255 each.
  const sums = new Uint32Array(cells * CHANNELS);
  const offsetOf = new Uint16Array(width);
  const blockWidth = new Uint32Array(COLUMNS);
  const blockHeight = new Uint32Array(ROWS);
  for (let x = 0; x < width; x += 1) {
    const column = Math.min(COLUMNS - 1, Math.floor((x * COLUMNS) / width));
    offsetOf[x] = column * CHANNELS;
    blockWidth[column] += 1;
  }
  for (let y = 0; y < height; y += 1) {
    const row = Math.min(ROWS - 1, Math.floor((y * ROWS) / height));
    blockHeight[row] += 1;
    const rowStart = row * COLUMNS * CHANNELS;
    let index = y * width * 4;
    for (let x = 0; x < width; x += 1) {
      const at = rowStart + offsetOf[x];
      sums[at] += pixels[index];
      sums[at + 1] += pixels[index + 1];
      sums[at + 2] += pixels[index + 2];
      index += 4;
    }
  }
  const grid = new Uint8Array(cells * CHANNELS);
  for (let row = 0; row < ROWS; row += 1) {
    for (let column = 0; column < COLUMNS; column += 1) {
      const count = blockWidth[column] * blockHeight[row];
      const at = (row * COLUMNS + column) * CHANNELS;
      for (let channel = 0; channel < CHANNELS; channel += 1) {
        grid[at + channel] = count ? Math.round(sums[at + channel] / count) : 0;
      }
    }
  }
  return grid;
};

const sample = (id) => {
  if (!latest) {
    self.postMessage({ type: "sample", id, empty: true });
    return;
  }
  const timestamp = latest.timestamp;
  const frameWidth = latest.displayWidth;
  const frameHeight = latest.displayHeight;
  const scale = Math.min(1, LONG_EDGE_PX / Math.max(frameWidth, frameHeight));
  const width = Math.max(1, Math.round(frameWidth * scale));
  const height = Math.max(1, Math.round(frameHeight * scale));
  if (!canvas || canvas.width !== width || canvas.height !== height) {
    canvas = new OffscreenCanvas(width, height);
    context = canvas.getContext("2d", { willReadFrequently: true });
  }
  // The default smoothing can sample the frame without averaging it, which
  // drops a thin line, such as a meeting app's ring around the speaking tile,
  // whenever it falls between the pixels it samples.
  context.imageSmoothingEnabled = true;
  context.imageSmoothingQuality = "high";
  context.drawImage(latest, 0, 0, width, height);
  const grabbedAt = performance.timeOrigin + performance.now();
  const grid = blockGrid(context.getImageData(0, 0, width, height).data, width, height);
  drawn = true;
  self.postMessage({ type: "sample", id, grid, width, height, timestamp, grabbedAt }, [
    grid.buffer,
  ]);
};

const encode = async (id) => {
  if (!drawn) {
    fail("Nothing was sampled to encode.", id);
    return;
  }
  const blob = await canvas.convertToBlob({ type: "image/jpeg", quality: JPEG_QUALITY });
  self.postMessage({ type: "jpeg", id, blob });
};

const stop = async () => {
  stopped = true;
  if (latest) {
    latest.close();
    latest = null;
  }
  if (reader) {
    await reader.cancel().catch(() => {});
  }
  self.postMessage({ type: "stopped" });
  self.close();
};

self.onmessage = (event) => {
  const message = event.data || {};
  try {
    switch (message.type) {
      case "start":
        void pump(message.readable);
        break;
      case "start-track":
        // MediaStreamTrackProcessor is a worker API in the specification.
        void pump(new MediaStreamTrackProcessor({ track: message.track }).readable);
        break;
      case "sample":
        sample(message.id);
        break;
      case "encode":
        encode(message.id).catch((error) => fail(describe(error), message.id));
        break;
      case "stats":
        self.postMessage({ type: "stats", id: message.id, framesSeen });
        break;
      case "stop":
        void stop();
        break;
      default:
        break;
    }
  } catch (error) {
    fail(describe(error), message.id);
  }
};
