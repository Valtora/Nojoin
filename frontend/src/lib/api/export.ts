import type { RecordingId } from "@/types";
import api from "./client";

export type ExportContentType = "transcript" | "notes" | "both" | "audio";
export type ExportFormat = "txt" | "pdf" | "docx";

export const exportAudio = async (
  recordingId: RecordingId,
  recordingName: string,
): Promise<void> => {
  try {
    const response = await api.get(`/recordings/${recordingId}/stream`, {
      responseType: "blob",
    });

    const url = window.URL.createObjectURL(new Blob([response.data], { type: "audio/mpeg" }));
    const link = document.createElement("a");
    link.href = url;

    // Sanitize filename similar to backend
    const sanitizedName = recordingName.replace(/[^a-zA-Z0-9 \-_.]/g, "").trim();
    const filename = `${sanitizedName || `recording_${recordingId}`}.mp3`;

    link.setAttribute("download", filename);
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.URL.revokeObjectURL(url);

    } catch (error: unknown) {
    console.error("[exportAudio] Error during audio export:", error);
    throw error;
  }
};

export const exportContent = async (
  recordingId: RecordingId,
  contentType: ExportContentType = "transcript",
  format: ExportFormat = "txt",
): Promise<void> => {
  try {
    const response = await api.get(`/transcripts/${recordingId}/export`, {
      params: {
        content_type: contentType,
        export_format: format,
      },
      responseType: "blob",
    });

    // Create a link and click it to download
    const url = window.URL.createObjectURL(new Blob([response.data]));
    const link = document.createElement("a");
    link.href = url;

    // Extract filename from header if possible, or generate one
    const contentDisposition = response.headers["content-disposition"];
    let filename = `export-${recordingId}.${format}`;

    if (contentDisposition) {
      // Try to match filename="name"
      const filenameMatch = contentDisposition.match(/filename="([^"]+)"/);
      if (filenameMatch && filenameMatch.length === 2) {
        filename = filenameMatch[1];
      } else {
        // Try to match filename=name
        const filenameSimpleMatch =
          contentDisposition.match(/filename=([^;]+)/);
        if (filenameSimpleMatch && filenameSimpleMatch.length === 2) {
          filename = filenameSimpleMatch[1].trim();
        }
      }
    }

    link.setAttribute("download", filename);
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.URL.revokeObjectURL(url);

    } catch (error: unknown) {
    console.error("[exportContent] Error during export:", error);
    throw error;
  }
};
