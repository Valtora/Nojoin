// @vitest-environment node

import { readFileSync } from "node:fs";
import path from "node:path";
import vm from "node:vm";

import { describe, expect, it } from "vitest";

import { ChangeRule, GRID_CHANNELS, GRID_COLUMNS, GRID_ROWS } from "./changeRule";

// The frame reader is a static worker script, so it runs here in a sandbox
// with a stand-in canvas whose pixels the test paints.
const WORKER = path.resolve(__dirname, "../../../../public/workers/speaker-frames.js");
const WIDTH = 1920;
const HEIGHT = 1080;
const STAGE = 31;

interface Drawn {
  enabled: boolean;
  quality: string;
}

interface Reply {
  type: string;
  grid?: Uint8Array;
}

class Scene {
  pixels = new Uint8ClampedArray(WIDTH * HEIGHT * 4);

  drawn: Drawn[] = [];

  canvases: [number, number][] = [];

  fill(level: number) {
    for (let index = 0; index < this.pixels.length; index += 4) {
      this.pixels.set([level, level, level, 255], index);
    }
  }

  /** A line one pixel wide down the whole of one 30 x 30 block. */
  line(rgb: [number, number, number]) {
    for (let y = 390; y < 420; y += 1) {
      this.pixels.set([...rgb, 255], (y * WIDTH + 645) * 4);
    }
  }
}

const runWorker = (scene: Scene, source: [number, number] = [3840, 2160]) => {
  const replies: Reply[] = [];
  const self = {
    onmessage: null as ((event: { data: unknown }) => void) | null,
    postMessage: (message: Reply) => replies.push(message),
    close: () => {},
  };
  // Browsers start a 2D context smoothing at "low" quality.
  class Context {
    imageSmoothingEnabled = true;

    imageSmoothingQuality = "low";

    drawImage() {
      scene.drawn.push({ enabled: this.imageSmoothingEnabled, quality: this.imageSmoothingQuality });
    }

    getImageData() {
      return { data: scene.pixels };
    }
  }
  class OffscreenCanvas {
    constructor(
      public width: number,
      public height: number,
    ) {
      scene.canvases.push([width, height]);
    }

    getContext() {
      return new Context();
    }
  }
  const frame = {
    timestamp: 0,
    displayWidth: source[0],
    displayHeight: source[1],
    close: () => {},
  };
  const readable = {
    getReader: () => {
      let sent = false;
      return {
        read: () => {
          if (sent) {
            return new Promise(() => {});
          }
          sent = true;
          return Promise.resolve({ value: frame, done: false });
        },
        cancel: () => Promise.resolve(),
      };
    },
  };
  vm.runInNewContext(readFileSync(WORKER, "utf8"), { self, OffscreenCanvas, performance });
  const send = (data: unknown) => self.onmessage?.({ data });
  send({ type: "start", readable });
  return {
    async sample(): Promise<Uint8Array> {
      await new Promise((resolve) => setTimeout(resolve, 0));
      send({ type: "sample", id: replies.length });
      const reply = replies[replies.length - 1];
      if (reply?.type !== "sample" || !reply.grid) {
        throw new Error("the worker sent no grid");
      }
      return reply.grid;
    },
  };
};

const keptAfter = async (paint: (scene: Scene) => void) => {
  const scene = new Scene();
  scene.fill(STAGE);
  const worker = runWorker(scene);
  const rule = new ChangeRule();
  const before = await worker.sample();
  rule.kept(before, 0);
  paint(scene);
  return { kept: rule.shouldKeep(await worker.sample(), 1_000), before, scene };
};

describe("screen study frame reader", () => {
  it("shrinks frames with high-quality smoothing", async () => {
    const { scene } = await keptAfter(() => {});
    expect(scene.drawn.length).toBeGreaterThan(0);
    expect(scene.drawn.every((d) => d.enabled && d.quality === "high")).toBe(true);
  });

  it("draws at most 1920 px on the long side, and a smaller screen at its own size", async () => {
    const large = new Scene();
    await runWorker(large).sample();
    expect(large.canvases).toEqual([[1920, 1080]]);
    const small = new Scene();
    await runWorker(small, [1280, 720]).sample();
    expect(small.canvases).toEqual([[1280, 720]]);
  });

  it("answers a red, green and blue average for each block", async () => {
    const { before } = await keptAfter(() => {});
    expect(before.length).toBe(GRID_COLUMNS * GRID_ROWS * GRID_CHANNELS);
    expect([...before].every((level) => level === STAGE)).toBe(true);
  });

  it("counts a thin coloured ring lighting up as a change", async () => {
    // Teams' ring, 2 px wide on a 4K screen at 100%, once shrunk to 1920 px:
    // a pixel of its colour over the dark stage. It moves the block's
    // brightness by four levels and its blue by seven.
    const ring: [number, number, number] = [127, 133, 245];
    const { kept } = await keptAfter((scene) => scene.line(ring));
    expect(kept).toBe(true);
  });

  it("does not count a grey line of the same strength, such as a cursor blink", async () => {
    const { kept } = await keptAfter((scene) => scene.line([144, 144, 144]));
    expect(kept).toBe(false);
  });

  it("does not count noise of a few levels in every pixel", async () => {
    const { kept } = await keptAfter((scene) => {
      let seed = 7;
      for (let index = 0; index < scene.pixels.length; index += 4) {
        for (let channel = 0; channel < 3; channel += 1) {
          seed = (seed * 1_103_515_245 + 12_345) % 2_147_483_648;
          scene.pixels[index + channel] = STAGE + (seed % 7) - 3;
        }
      }
    });
    expect(kept).toBe(false);
  });
});
