import type { RecordingId } from "@/types";
import api from "./client";

export const getNotes = async (
  recordingId: RecordingId,
): Promise<{ notes: string | null }> => {
  const response = await api.get<{ notes: string | null }>(
    `/transcripts/${recordingId}/notes`,
  );
  return response.data;
};

export const updateNotes = async (
  recordingId: RecordingId,
  notes: string,
): Promise<{ notes: string; status: string }> => {
  const response = await api.put<{ notes: string; status: string }>(
    `/transcripts/${recordingId}/notes`,
    { notes },
  );
  return response.data;
};

export const getUserNotes = async (
  recordingId: RecordingId,
): Promise<{ user_notes: string | null }> => {
  const response = await api.get<{ user_notes: string | null }>(
    `/transcripts/${recordingId}/user-notes`,
  );
  return response.data;
};

export const updateUserNotes = async (
  recordingId: RecordingId,
  userNotes: string,
): Promise<{ user_notes: string | null; status: string }> => {
  const response = await api.put<{ user_notes: string | null; status: string }>(
    `/transcripts/${recordingId}/user-notes`,
    { user_notes: userNotes },
  );
  return response.data;
};

export const updateMeetingEdgeFocus = async (
  recordingId: RecordingId,
  meetingEdgeFocus: string,
): Promise<{ meeting_edge_focus: string | null; status: string }> => {
  const response = await api.put<{
    meeting_edge_focus: string | null;
    status: string;
  }>(`/transcripts/${recordingId}/meeting-edge-focus`, {
    meeting_edge_focus: meetingEdgeFocus,
  });
  return response.data;
};

export const generateNotes = async (
  recordingId: RecordingId,
): Promise<{
  status: string;
  notes_status?: string;
  error_message?: string | null;
  message?: string;
}> => {
  const response = await api.post<{
    status: string;
    notes_status?: string;
    error_message?: string | null;
    message?: string;
  }>(
    `/transcripts/${recordingId}/notes/generate`,
  );
  return response.data;
};

export const findAndReplaceNotes = async (
  recordingId: RecordingId,
  find: string,
  replace: string,
  options: { caseSensitive?: boolean; useRegex?: boolean } = {},
): Promise<void> => {
  // Use the main replace endpoint since it applies to both transcript and notes
  await api.post(`/transcripts/${recordingId}/replace`, {
    find_text: find,
    replace_text: replace,
    case_sensitive: options.caseSensitive ?? false,
    use_regex: options.useRegex ?? false,
  });
};
