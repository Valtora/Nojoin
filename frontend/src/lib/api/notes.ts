import type {
  NoteAttachment,
  NoteFileRemoval,
  NotesOutput,
  RecordingId,
  TimelineNote,
  TimelineNoteCreate,
  TimelineNoteUpdate,
} from "@/types";
import api, { API_BASE_URL } from "./client";

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

/** Stores a file on a saved note, switched off. A note holds one: a second is a 409. */
export const attachFileToNote = async (
  recordingId: RecordingId,
  noteId: string,
  file: File,
): Promise<NoteAttachment> => {
  const formData = new FormData();
  formData.append("file", file, file.name);
  const response = await api.post<NoteAttachment>(
    `${timelineNotesPath(recordingId)}/${noteId}/attachment`,
    formData,
    { headers: { "Content-Type": "multipart/form-data" } },
  );
  return response.data;
};

/** Turns a note's file on for the AI, which reads it then, or off. */
export const setNoteFileUseInNotes = async (
  recordingId: RecordingId,
  noteId: string,
  useInNotes: boolean,
): Promise<NoteAttachment> => {
  const response = await api.patch<NoteAttachment>(
    `${timelineNotesPath(recordingId)}/${noteId}/attachment`,
    { use_in_notes: useInNotes },
  );
  return response.data;
};

/**
 * Removes a note's file. A note whose text is still only what came with the
 * file, "Screen capture" on a capture or the file's name, goes with it (`note`
 * is null); any other note stays without it and takes no other. 404 or 410
 * when the file or its note is gone already.
 */
export const removeNoteFile = async (
  recordingId: RecordingId,
  noteId: string,
): Promise<NoteFileRemoval> => {
  const response = await api.delete<NoteFileRemoval>(
    `${timelineNotesPath(recordingId)}/${noteId}/attachment`,
  );
  return response.data;
};

/** Where an `<img>` reads a document from; the browser sends the session cookie. */
export const documentPreviewUrl = (documentId: number): string =>
  `${API_BASE_URL}/documents/${documentId}/preview`;

export const documentFileUrl = (documentId: number): string =>
  `${API_BASE_URL}/documents/${documentId}/download`;

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
  output?: {
    create_output?: boolean;
    output_id?: number;
    kind?: "notes" | "glossary";
    extract_more?: boolean;
  },
): Promise<{
  status: string;
  notes_status?: string;
  error_message?: string | null;
  message?: string;
  output?: NotesOutput | null;
}> => {
  const response = await api.post<{
    status: string;
    notes_status?: string;
    error_message?: string | null;
    message?: string;
    output?: NotesOutput | null;
  }>(`/transcripts/${recordingId}/notes/generate`, {
    // null when no template was picked, so the backend falls through to the
    // user default, the install default and then the built-in structure. For
    // a new extra set, null is the built-in structure.
    notes_template_id: notesTemplateId ?? null,
    ...output,
  });
  return response.data;
};

export const listNotesOutputs = async (recordingId: RecordingId): Promise<NotesOutput[]> => {
  const response = await api.get<NotesOutput[]>(`/transcripts/${recordingId}/notes/outputs`);
  return response.data;
};

export const updateNotesOutput = async (recordingId: RecordingId, outputId: number, notes: string): Promise<NotesOutput> => {
  const response = await api.put<NotesOutput>(`/transcripts/${recordingId}/notes/outputs/${outputId}`, { notes });
  return response.data;
};

export const deleteNotesOutput = async (recordingId: RecordingId, outputId: number): Promise<void> => {
  await api.delete(`/transcripts/${recordingId}/notes/outputs/${outputId}`);
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
