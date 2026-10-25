/*
 * The screen study's frame reader (#653), run as a dedicated worker.
 *
 * The page hands it the readable side of a MediaStreamTrackProcessor on a
 * clone of the shared screen's video track, or the clone itself where only a
 * worker can make the processor. It keeps the newest frame and closes the rest,
 * which works with the Nojoin tab hidden, as it is through a meeting.
 *
 * On "sample" it draws the newest frame at most 1280 px on its long edge and
 * answers with a 64 x 36 grid of block brightness averages, which the page
 * compares with the last frame it kept. On "encode" it answers with that same
 * drawing as a JPEG at quality 0.8. Pixels leave this worker only as that
 * JPEG, sent back to the page that asked for it.
 *
 * A static file, like the PCM tap worklet, so it needs no bundler support and
 * loads only when a study starts.
 */
"use strict";

const LONG_EDGE_PX = 1280;
const COLUMNS = 64;
const ROWS = 36;
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
  const sums = new Float64Array(cells);
  const counts = new Uint32Array(cells);
  const columnOf = new Uint16Array(width);
  for (let x = 0; x < width; x += 1) {
    columnOf[x] = Math.min(COLUMNS - 1, Math.floor((x * COLUMNS) / width));
  }
  for (let y = 0; y < height; y += 1) {
    const rowStart = Math.min(ROWS - 1, Math.floor((y * ROWS) / height)) * COLUMNS;
    let index = y * width * 4;
    for (let x = 0; x < width; x += 1) {
      const cell = rowStart + columnOf[x];
      sums[cell] += 0.299 * pixels[index] + 0.587 * pixels[index + 1] + 0.114 * pixels[index + 2];
      counts[cell] += 1;
      index += 4;
    }
  }
  const grid = new Uint8Array(cells);
  for (let cell = 0; cell < cells; cell += 1) {
    grid[cell] = counts[cell] ? Math.round(sums[cell] / counts[cell]) : 0;
  }
  return grid;
};

const sample = (id) => {
  if (!latest) {
    self.postMessage({ type: "sample", id, empty: true });
    return;
  }
  const frameWidth = latest.displayWidth;
  const frameHeight = latest.displayHeight;
  const scale = Math.min(1, LONG_EDGE_PX / Math.max(frameWidth, frameHeight));
  const width = Math.max(1, Math.round(frameWidth * scale));
  const height = Math.max(1, Math.round(frameHeight * scale));
  if (!canvas || canvas.width !== width || canvas.height !== height) {
    canvas = new OffscreenCanvas(width, height);
    context = canvas.getContext("2d", { willReadFrequently: true });
  }
  context.drawImage(latest, 0, 0, width, height);
  const grid = blockGrid(context.getImageData(0, 0, width, height).data, width, height);
  drawn = true;
  self.postMessage({ type: "sample", id, grid, width, height }, [grid.buffer]);
};

const encode = async (id) => {
  if (!drawn) {
    fail("Nothing was sampled to encode.", id);
    return;
  }
  const blob = await canvas.convertToBlob({ type: "image/jpeg", quality: JPEG_QUALITY });
  self.postMessage({ type: "jpeg", id, blob });
};

const stop = () => {
  stopped = true;
  if (reader) {
    reader.cancel().catch(() => {});
  }
  if (latest) {
    latest.close();
    latest = null;
  }
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
        stop();
        break;
      default:
        break;
    }
  } catch (error) {
    fail(describe(error), message.id);
  }
};
