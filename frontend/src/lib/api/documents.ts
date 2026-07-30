import type { RecordingId } from "@/types";
import api from "./client";

/** File extensions the backend will parse. Mirrors SUPPORTED_EXTENSIONS. */
export const SUPPORTED_DOCUMENT_FORMATS = [
  ".pdf",
  ".pptx",
  ".docx",
  ".xlsx",
  ".csv",
  ".txt",
  ".md",
  ".png",
  ".jpg",
  ".jpeg",
] as const;

/** Formats with no text layer, so they are unreadable without a vision model. */
export const VISION_ONLY_DOCUMENT_FORMATS = [".png", ".jpg", ".jpeg"] as const;

/** Above this, the upload modal estimates parse cost before confirming. */
export const DOCUMENT_SIZE_WARNING_BYTES = 20 * 1024 * 1024;

export interface Document {
  id: number;
  recording_id: RecordingId;
  title: string;
  file_path: string;
  file_type: string;
  status: "PENDING" | "PROCESSING" | "READY" | "ERROR";
  error_message?: string;
  /** What was requested, not what happened; see parse_warning. */
  parse_mode: "VISUAL" | "STRUCTURAL";
  /**
   * Non-fatal degradation. The document is READY and searchable, but was
   * parsed without visual analysis and this says why.
   */
  parse_warning?: string | null;
  page_count?: number | null;
  pages_parsed: number;
  created_at: string;
}

export interface VisionSupport {
  provider: string;
  model?: string | null;
  /**
   * null means the provider cannot be asked, which is every hosted API. Treat
   * it as "proceed and find out", never as a refusal.
   */
  supported: boolean | null;
}

export const getDocuments = async (
  recordingId: RecordingId,
): Promise<Document[]> => {
  const response = await api.get<Document[]>(
    `/recordings/${recordingId}/documents`,
  );
  return response.data;
};

export const uploadDocument = async (
  recordingId: RecordingId,
  file: File,
  options?: { deepParse?: boolean },
): Promise<Document> => {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("deep_parse", String(options?.deepParse ?? true));
  const response = await api.post<Document>(
    `/recordings/${recordingId}/documents`,
    formData,
    {
      headers: {
        "Content-Type": "multipart/form-data",
      },
    },
  );
  return response.data;
};

export const reparseDocument = async (
  documentId: number,
  options?: { deepParse?: boolean },
): Promise<Document> => {
  const response = await api.post<Document>(
    `/documents/${documentId}/reparse`,
    null,
    { params: { deep_parse: options?.deepParse ?? true } },
  );
  return response.data;
};

export const getVisionSupport = async (
  provider: string,
  model?: string | null,
): Promise<VisionSupport> => {
  const response = await api.get<VisionSupport>("/llm/vision-support", {
    params: { provider, ...(model ? { model } : {}) },
  });
  return response.data;
};

export const deleteDocument = async (documentId: number): Promise<void> => {
  await api.delete(`/documents/${documentId}`);
};
