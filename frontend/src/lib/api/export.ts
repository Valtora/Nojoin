import type { RecordingId } from "@/types";
import api from "./client";

/** The parts a document export can carry. The file always orders them notes, analytics, transcript. */
export type ExportSection = "notes" | "analytics" | "transcript";
export type ExportFormat = "txt" | "pdf" | "docx";

/** What the export dialog asks for: a document of some sections, or the audio. */
export type ExportSelection =
  | {
      kind: "document";
      sections: ExportSection[];
      format: ExportFormat;
      /** The reader's own notes, where they fall in the transcript section. */
      includeNotes: boolean;
    }
  | { kind: "audio" };

export const exportAudio = async (
  recordingId: RecordingId,
  recordingName: string,
): Promise<void> => {
  try {
    const response = await api.get(`/recordings/${recordingId}/stream`, {
      responseType: "blob",
      // Only a 200 carries the whole audio. A 202 means the playable copy is
      // still being made, and its body is a JSON notice that must not be saved
      // as an .mp3. A 206 would be only part of the file. Both are rejected
      // like any error.
      validateStatus: (status) => status === 200,
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

/**
 * Download a document export.
 *
 * `speakerColors` is the recording view's speaker colour map (palette keys by
 * diarisation label and name). Automatic colours exist only in the browser, so
 * sending them is what lets the file use the colours the reader sees here.
 */
export const exportContent = async (
  recordingId: RecordingId,
  sections: ExportSection[],
  format: ExportFormat = "pdf",
  speakerColors: Record<string, string> = {},
  includeNotes = true,
): Promise<void> => {
  try {
    const response = await api.get(`/transcripts/${recordingId}/export`, {
      params: {
        sections,
        export_format: format,
        speaker_colors: JSON.stringify(speakerColors),
        include_notes: includeNotes,
      },
      // Repeated keys (sections=notes&sections=analytics), which is how
      // FastAPI reads a list, rather than axios's default sections[]=notes.
      paramsSerializer: { indexes: null },
      responseType: "blob",
    });

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
