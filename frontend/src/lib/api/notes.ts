import type {
  RecordingId,
  TimelineNote,
  TimelineNoteCreate,
  TimelineNoteUpdate,
} from "@/types";
import api from "./client";

export const getNotes = async (
  recordingId: RecordingId,
): Promise<{
  notes: string | null;
  notes_template_id?: number | null;
  notes_template_sections?: string | null;
}> => {
  const response = await api.get<{
    notes: string | null;
    notes_template_id?: number | null;
    notes_template_sections?: string | null;
  }>(`/transcripts/${recordingId}/notes`);
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

const timelineNotesPath = (recordingId: RecordingId) =>
  `/transcripts/${recordingId}/timeline-notes`;

/** Every note on the recording, in timeline order. */
export const listTimelineNotes = async (
  recordingId: RecordingId,
  signal?: AbortSignal,
): Promise<TimelineNote[]> => {
  const response = await api.get<TimelineNote[]>(
    timelineNotesPath(recordingId),
    {
      signal,
    },
  );
  return response.data;
};

/** Creates a note. Retrying with the same client `id` returns the first one. */
export const createTimelineNote = async (
  recordingId: RecordingId,
  note: TimelineNoteCreate,
): Promise<TimelineNote> => {
  const response = await api.post<TimelineNote>(
    timelineNotesPath(recordingId),
    note,
  );
  return response.data;
};

/**
 * Edits a note; 409 (detail `TimelineNoteConflictDetail`) if it changed since,
 * 410 if it was deleted (detail `code` says whether by this write's token).
 */
export const updateTimelineNote = async (
  recordingId: RecordingId,
  noteId: string,
  update: TimelineNoteUpdate,
): Promise<TimelineNote> => {
  const response = await api.patch<TimelineNote>(
    `${timelineNotesPath(recordingId)}/${noteId}`,
    update,
  );
  return response.data;
};

/**
 * Deletes a note; with `expectedRevision`, 409 if it changed since. A delete
 * already made under the same `writeToken` succeeds again.
 */
export const deleteTimelineNote = async (
  recordingId: RecordingId,
  noteId: string,
  conditions: { expectedRevision?: number; writeToken?: string } = {},
): Promise<void> => {
  await api.delete(`${timelineNotesPath(recordingId)}/${noteId}`, {
    params: {
      expected_revision: conditions.expectedRevision,
      write_token: conditions.writeToken,
    },
  });
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
  notesTemplateId?: number | null,
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
  }>(`/transcripts/${recordingId}/notes/generate`, {
    // null when no template was picked, so the backend falls through to the
    // user default, the install default and then the built-in structure.
    notes_template_id: notesTemplateId ?? null,
  });
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
