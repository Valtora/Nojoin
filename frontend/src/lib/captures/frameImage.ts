/**
 * Turning the shared screen, or a pasted or dropped file, into a note's file.
 *
 * A frame is taken at the share's own resolution and stored as PNG, which
 * keeps text on a slide sharp for the vision model and for OCR. Only a frame
 * whose PNG would pass `FRAME_MAX_BYTES` (a busy 4K screen, a photo) is stored
 * as JPEG instead, small enough for every vision provider to take. A note
 * takes a file of any type; a picture of another type than PNG or JPEG is
 * drawn again as one of those.
 */


/**
 * Well under the tightest per-image limit a vision provider sets: the Claude
 * API's 10 MB of base64, about 7.5 MB of file.
 */
export const FRAME_MAX_BYTES = 4 * 1024 * 1024;

/** An 8K screen fits; anything larger is scaled down to this on its long edge. */
export const FRAME_MAX_EDGE_PX = 7680;

/** How long to wait for the share to deliver a frame. */
const GRAB_TIMEOUT_MS = 3_000;

/** The JPEG qualities tried in turn once PNG is too large. */
const JPEG_QUALITIES = [0.92, 0.85, 0.75] as const;

export type ImageEncoder = (type: "image/png" | "image/jpeg", quality?: number) => Promise<Blob>;

/** A width and height scaled to fit `maxEdge`, never enlarged. */
export const scaledSize = (
  width: number,
  height: number,
  maxEdge = FRAME_MAX_EDGE_PX,
): { width: number; height: number } => {
  const scale = Math.min(1, maxEdge / Math.max(width, height, 1));
  return {
    width: Math.max(1, Math.round(width * scale)),
    height: Math.max(1, Math.round(height * scale)),
  };
};

/**
 * PNG when it fits under `maxBytes`, else the best JPEG that does. The last
 * JPEG tried is returned even past the limit, so a capture is never lost
 * for being large.
 */
export const encodeWithinLimit = async (
  encode: ImageEncoder,
  maxBytes = FRAME_MAX_BYTES,
): Promise<Blob> => {
  const png = await encode("image/png");
  if (png.size <= maxBytes) return png;
  let jpeg = png;
  for (const quality of JPEG_QUALITIES) {
    jpeg = await encode("image/jpeg", quality);
    if (jpeg.size <= maxBytes) return jpeg;
  }
  return jpeg;
};

const canvasEncoder =
  (canvas: HTMLCanvasElement): ImageEncoder =>
  (type, quality) =>
    new Promise((resolve, reject) => {
      canvas.toBlob(
        (blob) => (blob ? resolve(blob) : reject(new Error("The image could not be encoded."))),
        type,
        quality,
      );
    });

type Drawable = ImageBitmap | HTMLVideoElement;

const sizeOf = (source: Drawable) =>
  source instanceof HTMLVideoElement
    ? { width: source.videoWidth, height: source.videoHeight }
    : { width: source.width, height: source.height };

/** Draws `source` at `maxEdge` and encodes it within the size limit. */
export const encodeImage = async (source: Drawable, maxEdge = FRAME_MAX_EDGE_PX): Promise<Blob> => {
  const natural = sizeOf(source);
  if (!natural.width || !natural.height) throw new Error("The image is empty.");
  const { width, height } = scaledSize(natural.width, natural.height, maxEdge);
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d");
  if (!context) throw new Error("This browser cannot draw the image.");
  context.drawImage(source, 0, 0, width, height);
  return encodeWithinLimit(canvasEncoder(canvas));
};

/**
 * One still of the shared screen at its full resolution, as PNG or JPEG,
 * drawn through a muted, unattached video element. The element gets the
 * share's latest frame as soon as it attaches, even when the shared surface
 * is still and sends no new ones, where ImageCapture's grabFrame can wait.
 * Never stops `track`: that would end the share and the recording with it.
 */
export const grabDisplayFrame = async (track: MediaStreamTrack): Promise<Blob> => {
  const video = document.createElement("video");
  video.muted = true;
  video.playsInline = true;
  video.srcObject = new MediaStream([track]);
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    await new Promise<void>((resolve, reject) => {
      timer = setTimeout(() => reject(new Error("The shared screen sent no picture.")), GRAB_TIMEOUT_MS);
      video.onloadeddata = () => resolve();
      video.onerror = () => reject(new Error("The shared screen could not be read."));
      void video.play().catch(reject);
    });
    return await encodeImage(video);
  } finally {
    clearTimeout(timer);
    // Detach only. The track belongs to the recording.
    video.pause();
    video.srcObject = null;
  }
};

/** A picture of another type than PNG or JPEG, or past the size limit, to draw again. */
const redrawable = (file: File) =>
  file.type.startsWith("image/") &&
  // SVG is markup, kept as a file to download and never drawn.
  file.type !== "image/svg+xml" &&
  !((file.type === "image/png" || file.type === "image/jpeg") && file.size <= FRAME_MAX_BYTES);

/**
 * A pasted or dropped file as its note holds it. A note takes a file of any
 * type: a picture the browser can draw is drawn again as PNG or JPEG, so it
 * is shown and can be read, and every other file is kept as it is.
 */
export const fileForNote = async (file: File): Promise<File> => {
  if (!redrawable(file)) return file;
  try {
    const bitmap = await createImageBitmap(file);
    try {
      const image = await encodeImage(bitmap);
      const stem = file.name.replace(/\.[^.]*$/, "") || "image";
      return new File([image], `${stem}.${image.type === "image/jpeg" ? "jpg" : "png"}`, {
        type: image.type,
      });
    } finally {
      bitmap.close();
    }
  } catch {
    return file;
  }
};

/** The first file on a paste or a drop, unless a paste also carries text. */
export const transferredFile = (data: DataTransfer | null): File | null => {
  if (!data) return null;
  // Text copied from a page can come with a picture of it; the text wins.
  if (data.getData("text/plain")) return null;
  return data.files?.[0] ?? null;
};
