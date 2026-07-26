import api from "./client";

/**
 * Meeting-notes structures (issue #137).
 *
 * A template holds only the *editable* half of the notes prompt: the section
 * structure. The fidelity rules, table syntax and JSON contract around it are
 * fixed by the backend and are not represented here.
 */
export interface NotesTemplate {
  id: number;
  name: string;
  /** One line on what the structure is for; null when the user left it blank. */
  description: string | null;
  sections: string;
  scope: "install" | "personal";
  user_id: number | null;
  /** Which shipped structure version this was forked from, or null if written from scratch. */
  builtin_version: number | null;
  is_editable: boolean;
  /** The shipped structure has improved since this template forked from it. */
  is_stale: boolean;
  is_install_default: boolean;
  is_user_default: boolean;
}

export interface NotesTemplateListResponse {
  templates: NotesTemplate[];
  builtin: {
    name: string;
    description: string;
    sections: string;
    version: number;
  };
  limits: {
    max_sections_length: number;
    max_description_length: number;
    max_glossary_length: number;
    max_templates_per_scope: number;
  };
  is_admin: boolean;
}

export const listNotesTemplates = async (): Promise<NotesTemplateListResponse> => {
  const response = await api.get<NotesTemplateListResponse>("/notes-templates");
  return response.data;
};

export const createNotesTemplate = async (payload: {
  name: string;
  description?: string | null;
  sections: string;
  scope?: "install" | "personal";
  builtin_version?: number | null;
}): Promise<NotesTemplate> => {
  const response = await api.post<NotesTemplate>("/notes-templates", payload);
  return response.data;
};

export const updateNotesTemplate = async (
  templateId: number,
  payload: { name?: string; description?: string | null; sections?: string },
): Promise<NotesTemplate> => {
  const response = await api.put<NotesTemplate>(
    `/notes-templates/${templateId}`,
    payload,
  );
  return response.data;
};

export const resetNotesTemplate = async (
  templateId: number,
): Promise<NotesTemplate> => {
  const response = await api.post<NotesTemplate>(
    `/notes-templates/${templateId}/reset`,
  );
  return response.data;
};

export const copyNotesTemplate = async (
  templateId: number,
): Promise<NotesTemplate> => {
  const response = await api.post<NotesTemplate>(
    `/notes-templates/${templateId}/copy`,
  );
  return response.data;
};

export const deleteNotesTemplate = async (templateId: number): Promise<void> => {
  await api.delete(`/notes-templates/${templateId}`);
};

export const previewNotesPrompt = async (payload: {
  sections?: string | null;
  glossary?: string | null;
}): Promise<{ prompt: string; editable_sections: string }> => {
  const response = await api.post<{
    prompt: string;
    editable_sections: string;
  }>("/notes-templates/preview", payload);
  return response.data;
};

export interface GeneratedNotesStructure {
  status: "pending" | "completed" | "error";
  name?: string;
  description?: string;
  sections?: string;
  error?: string;
}

/**
 * Start a structure-generation job. Returns a job id to poll — the work runs on
 * the worker, because the repo keeps LLM calls off the API request path.
 */
export const generateNotesStructure = async (
  brief: string,
): Promise<{ job_id: string; status: string }> => {
  const response = await api.post<{ job_id: string; status: string }>(
    "/notes-templates/generate",
    { brief },
  );
  return response.data;
};

/** Poll a generation job until it reports completed or error. */
export const getGeneratedNotesStructure = async (
  jobId: string,
): Promise<GeneratedNotesStructure> => {
  const response = await api.get<GeneratedNotesStructure>(
    `/notes-templates/generate/${jobId}`,
  );
  return response.data;
};
