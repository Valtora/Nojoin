/**
 * Turning the shared screen, or a pasted image, into the file a capture is.
 *
 * A frame is taken at the share's own resolution and stored as PNG, which
 * keeps text on a slide sharp for the vision model and for OCR. Only a frame
 * whose PNG would pass `FRAME_MAX_BYTES` (a busy 4K screen, a photo) is stored
 * as JPEG instead, small enough for every vision provider to take. The server
 * accepts PNG and JPEG only, so a pasted image of any other type is drawn
 * again as one of those.
 */

/**
 * Well under the tightest per-image limit a vision provider sets: the Claude
 * API's 10 MB of base64, about 7.5 MB of file.
 */
export const FRAME_MAX_BYTES = 4 * 1024 * 1024;

/** An 8K screen fits; anything larger is scaled down to this on its long edge. */
export const FRAME_MAX_EDGE_PX = 7680;

/** How long to wait for the share to deliver a frame before trying another way. */
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
 * JPEG tried is returned even past the limit: the server takes up to 20 MB,
 * so a capture is never lost for being large.
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

type Drawable = ImageBitmap | HTMLVideoElement | HTMLImageElement;

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

/** What Chrome's ImageCapture offers; not in TypeScript's DOM library. */
interface FrameGrabber {
  grabFrame(): Promise<ImageBitmap>;
}

type FrameGrabberConstructor = new (track: MediaStreamTrack) => FrameGrabber;

const withTimeout = <T>(promise: Promise<T>, ms: number): Promise<T> =>
  new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("The shared screen sent no picture.")), ms);
    promise.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (error: unknown) => {
        clearTimeout(timer);
        reject(error);
      },
    );
  });

/**
 * The frame on screen now, drawn through a muted, unattached video element.
 * The element gets the share's latest frame as soon as it attaches, even when
 * the shared surface is still and sends no new ones.
 */
const grabThroughVideo = async (track: MediaStreamTrack): Promise<Blob> => {
  const video = document.createElement("video");
  video.muted = true;
  video.playsInline = true;
  video.srcObject = new MediaStream([track]);
  try {
    await withTimeout(
      new Promise<void>((resolve, reject) => {
        video.onloadeddata = () => resolve();
        video.onerror = () => reject(new Error("The shared screen could not be read."));
        void video.play().catch(reject);
      }),
      GRAB_TIMEOUT_MS,
    );
    return await encodeImage(video);
  } finally {
    // Detach only. The track belongs to the recording.
    video.pause();
    video.srcObject = null;
  }
};

/**
 * One still of the shared screen at its full resolution, as PNG or JPEG.
 * Never stops `track`: that would end the share and the recording with it.
 */
export const grabDisplayFrame = async (track: MediaStreamTrack): Promise<Blob> => {
  const Grabber = (globalThis as { ImageCapture?: FrameGrabberConstructor }).ImageCapture;
  if (Grabber) {
    try {
      const bitmap = await withTimeout(new Grabber(track).grabFrame(), GRAB_TIMEOUT_MS);
      try {
        return await encodeImage(bitmap);
      } finally {
        bitmap.close();
      }
    } catch {
      // A still surface can leave grabFrame waiting; the video element does not.
    }
  }
  return grabThroughVideo(track);
};

/** Whether a pasted file can be stored as it is. */
const storableAsIs = (image: Blob) =>
  (image.type === "image/png" || image.type === "image/jpeg") && image.size <= FRAME_MAX_BYTES;

/**
 * A pasted image as a capture's file: kept as it is when it is a PNG or JPEG
 * under the size limit, drawn again otherwise.
 */
export const imageForCapture = async (image: Blob): Promise<Blob> => {
  if (storableAsIs(image)) return image;
  const bitmap = await createImageBitmap(image);
  try {
    return await encodeImage(bitmap);
  } finally {
    bitmap.close();
  }
};

/** A small JPEG of the image as a data URL, to show while it uploads. */
export const previewDataUrl = async (image: Blob, maxEdge = 320): Promise<string | null> => {
  try {
    const bitmap = await createImageBitmap(image);
    try {
      const { width, height } = scaledSize(bitmap.width, bitmap.height, maxEdge);
      const canvas = document.createElement("canvas");
      canvas.width = width;
      canvas.height = height;
      canvas.getContext("2d")?.drawImage(bitmap, 0, 0, width, height);
      return canvas.toDataURL("image/jpeg", 0.7);
    } finally {
      bitmap.close();
    }
  } catch {
    return null;
  }
};

/** The first image on a clipboard paste, unless it also carries text to paste. */
export const pastedImage = (data: DataTransfer | null): File | null => {
  if (!data) return null;
  // Text copied from a page can come with a picture of it; the text wins.
  if (data.getData("text/plain")) return null;
  for (const item of Array.from(data.items ?? [])) {
    if (item.kind === "file" && item.type.startsWith("image/")) {
      const file = item.getAsFile();
      if (file) return file;
    }
  }
  return null;
};
