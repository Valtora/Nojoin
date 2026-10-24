import type { RecordingId } from "@/types";
import api, { API_BASE_URL } from "./client";
import type { Document } from "./documents";

/**
 * An image taken during a meeting: a frame of the shared screen, or an image
 * pasted into the note box. Stored as a document of its own kind, at its time
 * on the audio timeline, the clock notes run on. It is read and searchable
 * like any document, and reaches the notes only while `use_in_notes` is on.
 */
export interface Capture extends Document {
  kind: "capture";
  capture_ms: number;
  use_in_notes: boolean;
}

export const getCaptures = async (recordingId: RecordingId): Promise<Capture[]> => {
  const response = await api.get<Capture[]>(`/recordings/${recordingId}/captures`);
  return response.data;
};

export const uploadCapture = async (
  recordingId: RecordingId,
  image: Blob,
  captureMs: number,
): Promise<Capture> => {
  const formData = new FormData();
  const extension = image.type === "image/jpeg" ? "jpg" : "png";
  formData.append("file", image, `capture.${extension}`);
  formData.append("capture_ms", String(Math.max(0, Math.round(captureMs))));
  const response = await api.post<Capture>(
    `/recordings/${recordingId}/captures`,
    formData,
    { headers: { "Content-Type": "multipart/form-data" } },
  );
  return response.data;
};

export const setCaptureUseInNotes = async (
  recordingId: RecordingId,
  captureId: number,
  useInNotes: boolean,
): Promise<Capture> => {
  const response = await api.patch<Capture>(
    `/recordings/${recordingId}/captures/${captureId}`,
    { use_in_notes: useInNotes },
  );
  return response.data;
};

/**
 * Where an `<img>` reads a capture from. The browser sends the session cookie
 * with these as it does with every request to the API's origin.
 */
export const capturePreviewUrl = (documentId: number): string =>
  `${API_BASE_URL}/documents/${documentId}/preview`;

export const captureImageUrl = (documentId: number): string =>
  `${API_BASE_URL}/documents/${documentId}/download`;
