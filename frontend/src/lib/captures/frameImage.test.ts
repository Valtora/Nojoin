import { describe, expect, it, vi } from "vitest";

import {
  FRAME_MAX_BYTES,
  encodeWithinLimit,
  fileForNote,
  scaledSize,
  transferredFile,
  type ImageEncoder,
} from "./frameImage";

const blobOf = (size: number, type: string) => new Blob([new Uint8Array(size)], { type });

describe("a frame's size and encoding", () => {
  it("keeps a screen's own resolution, and scales only past 8K", () => {
    expect(scaledSize(3840, 2160)).toEqual({ width: 3840, height: 2160 });
    expect(scaledSize(1280, 720)).toEqual({ width: 1280, height: 720 });
    expect(scaledSize(15360, 8640)).toEqual({ width: 7680, height: 4320 });
  });

  it("stores PNG when it fits", async () => {
    const encode = vi.fn<ImageEncoder>(async (type) => blobOf(1_000, type));

    const image = await encodeWithinLimit(encode);

    expect(image.type).toBe("image/png");
    expect(encode).toHaveBeenCalledTimes(1);
  });

  it("falls back to the best JPEG under the limit for a busy screen", async () => {
    const encode = vi.fn<ImageEncoder>(async (type, quality) =>
      type === "image/png"
        ? blobOf(FRAME_MAX_BYTES + 1, type)
        : blobOf(quality === 0.92 ? FRAME_MAX_BYTES + 1 : 900_000, type),
    );

    const image = await encodeWithinLimit(encode);

    expect(image.type).toBe("image/jpeg");
    expect(image.size).toBe(900_000);
    expect(encode.mock.calls.map(([type, quality]) => [type, quality])).toEqual([
      ["image/png", undefined],
      ["image/jpeg", 0.92],
      ["image/jpeg", 0.85],
    ]);
  });

  it("keeps the smallest JPEG rather than lose a capture that is still too large", async () => {
    const encode = vi.fn<ImageEncoder>(async (type) => blobOf(FRAME_MAX_BYTES * 2, type));

    const image = await encodeWithinLimit(encode);

    expect(image.type).toBe("image/jpeg");
    expect(encode).toHaveBeenCalledTimes(4);
  });
});

const clipboard = ({
  text = "",
  files = [] as File[],
}: { text?: string; files?: File[] }): DataTransfer =>
  ({
    getData: (format: string) => (format === "text/plain" ? text : ""),
    files,
  }) as unknown as DataTransfer;

describe("a file pasted or dropped into the note box", () => {
  const screenshot = new File([new Uint8Array(10)], "image.png", { type: "image/png" });
  const pdf = new File([new Uint8Array(10)], "plan.pdf", { type: "application/pdf" });

  it("is the first file on the clipboard, whatever its kind", () => {
    expect(transferredFile(clipboard({ files: [pdf, screenshot] }))).toBe(pdf);
    expect(transferredFile(null)).toBeNull();
  });

  it("gives way to text copied with a picture of it", () => {
    expect(transferredFile(clipboard({ text: "Quarterly plan", files: [screenshot] }))).toBeNull();
  });

  it("is kept as it is whatever its type, and an SVG is never drawn", async () => {
    const archive = new File([new Uint8Array(10)], "slides.zip", { type: "application/zip" });
    const markup = new File(["<svg/>"], "logo.svg", { type: "image/svg+xml" });
    const drawn = vi.fn();
    vi.stubGlobal("createImageBitmap", drawn);

    await expect(fileForNote(screenshot)).resolves.toBe(screenshot);
    await expect(fileForNote(pdf)).resolves.toBe(pdf);
    await expect(fileForNote(archive)).resolves.toBe(archive);
    await expect(fileForNote(markup)).resolves.toBe(markup);
    expect(drawn).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });
});
