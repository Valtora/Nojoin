import type { RecordingId } from "@/types";
import api from "./client";

export interface Document {
  id: number;
  recording_id: RecordingId;
  title: string;
  file_path: string;
  file_type: string;
  status: "PENDING" | "PROCESSING" | "READY" | "ERROR";
  error_message?: string;
  created_at: string;
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
): Promise<Document> => {
  const formData = new FormData();
  formData.append("file", file);
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

export const deleteDocument = async (documentId: number): Promise<void> => {
  await api.delete(`/documents/${documentId}`);
};
