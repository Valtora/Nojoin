import { describe, expect, it, vi } from "vitest";

import {
  FRAME_MAX_BYTES,
  encodeWithinLimit,
  pastedImage,
  scaledSize,
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
    items: files.map((file) => ({ kind: "file", type: file.type, getAsFile: () => file })),
  }) as unknown as DataTransfer;

describe("an image pasted into the note box", () => {
  const screenshot = new File([new Uint8Array(10)], "image.png", { type: "image/png" });

  it("is taken from the clipboard", () => {
    expect(pastedImage(clipboard({ files: [screenshot] }))).toBe(screenshot);
  });

  it("gives way to text copied with a picture of it", () => {
    expect(pastedImage(clipboard({ text: "Quarterly plan", files: [screenshot] }))).toBeNull();
  });

  it("is not a file of another kind", () => {
    const pdf = new File([new Uint8Array(10)], "plan.pdf", { type: "application/pdf" });

    expect(pastedImage(clipboard({ files: [pdf] }))).toBeNull();
    expect(pastedImage(null)).toBeNull();
  });
});
