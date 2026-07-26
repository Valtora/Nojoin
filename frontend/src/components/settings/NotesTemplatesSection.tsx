"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Plus,
  Pencil,
  Copy,
  Trash2,
  Check,
  AlertCircle,
  Loader2,
} from "lucide-react";
import { Settings } from "@/types";
import {
  NotesTemplate,
  NotesTemplateListResponse,
  copyNotesTemplate,
  createNotesTemplate,
  deleteNotesTemplate,
  listNotesTemplates,
  resetNotesTemplate,
  updateNotesTemplate,
} from "@/lib/api";
import { useNotificationStore } from "@/lib/notificationStore";
import SettingsPanel from "./SettingsPanel";
import SettingsSection from "./SettingsSection";
import NotesTemplateEditorModal from "./NotesTemplateEditorModal";

interface NotesTemplatesSectionProps {
  settings: Settings;
  /** Apply and save immediately (default selection is a discrete control). */
  onPersist: (newSettings: Settings) => void;
  isAdmin?: boolean;
}

/**
 * "Notes structure" AI section (issue #137).
 *
 * Two tiers in one list: install templates an admin maintains for everyone, and
 * the user's own. A regular user sees install templates read-only and copies one
 * to vary it, so shared text cannot change under other people.
 */
export default function NotesTemplatesSection({
  settings,
  onPersist,
  isAdmin = false,
}: NotesTemplatesSectionProps) {
  const { addNotification } = useNotificationStore();
  const [data, setData] = useState<NotesTemplateListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<NotesTemplate | null>(null);
  const [creating, setCreating] = useState(false);
  const [creatingScope, setCreatingScope] = useState<"install" | "personal">(
    "personal",
  );

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      setData(await listNotesTemplates());
    } catch (error) {
      console.error("Failed to load notes templates", error);
      addNotification({
        type: "error",
        message: "Could not load your notes structures.",
      });
    } finally {
      setLoading(false);
    }
  }, [addNotification]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const selectedId = settings.notes_template_id ?? null;

  const handleSelectDefault = (templateId: number | null) => {
    onPersist({ ...settings, notes_template_id: templateId });
  };

  const handleSelectInstallDefault = (templateId: number | null) => {
    onPersist({ ...settings, install_notes_template_id: templateId });
  };

  const handleSave = async (
    template: NotesTemplate | null,
    payload: { name: string; description: string; sections: string },
  ) => {
    try {
      if (template) {
        await updateNotesTemplate(template.id, payload);
      } else {
        await createNotesTemplate({ ...payload, scope: creatingScope });
      }
      await refresh();
    } catch (error) {
      console.error("Failed to save notes template", error);
      addNotification({
        type: "error",
        message:
          "Could not save the structure. Check that it has at least one Markdown heading.",
      });
      throw error;
    }
  };

  const handleCopy = async (template: NotesTemplate) => {
    try {
      await copyNotesTemplate(template.id);
      await refresh();
    } catch (error) {
      console.error("Failed to copy notes template", error);
      addNotification({ type: "error", message: "Could not copy the structure." });
    }
  };

  const handleReset = async (template: NotesTemplate) => {
    try {
      await resetNotesTemplate(template.id);
      await refresh();
      addNotification({
        type: "success",
        message: `${template.name} now matches the current Nojoin default.`,
      });
    } catch (error) {
      console.error("Failed to reset notes template", error);
      addNotification({ type: "error", message: "Could not reset the structure." });
    }
  };

  const handleDelete = async (template: NotesTemplate) => {
    try {
      await deleteNotesTemplate(template.id);
      if (settings.notes_template_id === template.id) {
        handleSelectDefault(null);
      }
      await refresh();
    } catch (error) {
      console.error("Failed to delete notes template", error);
      addNotification({
        type: "error",
        message: "Could not delete the structure.",
      });
    }
  };

  return (
    <SettingsSection
      eyebrow="AI"
      title="Notes structure"
      description="Choose how generated meeting notes are organised. Accuracy rules, table formatting and the transcript itself are fixed; the sections and their emphasis are yours."
      width="regular"
    >
      <SettingsPanel className="mx-auto max-w-3xl space-y-4">
        {loading && !data ? (
          <div className="flex items-center gap-2 text-sm contrast-helper">
            <Loader2 className="w-4 h-4 animate-spin" />
            Loading structures...
          </div>
        ) : (
          <>
            <div className="space-y-2">
              <button
                type="button"
                onClick={() => handleSelectDefault(null)}
                className={`w-full text-left p-3 rounded-lg border transition-colors ${
                  selectedId === null
                    ? "border-orange-500 bg-orange-50 dark:bg-orange-500/10"
                    : "border-gray-200 dark:border-gray-700 hover:border-gray-300 dark:hover:border-gray-600"
                }`}
              >
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <p className="text-sm font-medium text-gray-900 dark:text-white">
                      {data?.builtin.name ?? "Nojoin default"}
                    </p>
                    <p className="text-xs contrast-helper mt-0.5">
                      {data?.builtin.description ??
                        "Summary, decisions, action items, detailed notes."}{" "}
                      Follows the install default when an administrator has set
                      one.
                    </p>
                  </div>
                  {selectedId === null && (
                    <Check className="w-4 h-4 text-orange-500 shrink-0" />
                  )}
                </div>
              </button>

              {data?.templates.map((template) => (
                <div
                  key={template.id}
                  className={`p-3 rounded-lg border transition-colors ${
                    selectedId === template.id
                      ? "border-orange-500 bg-orange-50 dark:bg-orange-500/10"
                      : "border-gray-200 dark:border-gray-700"
                  }`}
                >
                  <div className="flex items-start justify-between gap-3">
                    <button
                      type="button"
                      onClick={() => handleSelectDefault(template.id)}
                      className="text-left flex-1"
                    >
                      <p className="text-sm font-medium text-gray-900 dark:text-white flex items-center gap-2 flex-wrap">
                        {template.name}
                        {template.scope === "install" && (
                          <span className="text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-700 contrast-helper">
                            Install
                          </span>
                        )}
                        {template.is_install_default && (
                          <span className="text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-700 contrast-helper">
                            Install default
                          </span>
                        )}
                      </p>
                      {template.description && (
                        <p className="text-xs contrast-helper mt-0.5">
                          {template.description}
                        </p>
                      )}
                      {template.is_stale && (
                        <p className="text-xs text-amber-600 dark:text-amber-400 mt-1 flex items-center gap-1">
                          <AlertCircle className="w-3.5 h-3.5" />
                          The Nojoin default has improved since this was copied.
                        </p>
                      )}
                    </button>

                    <div className="flex items-center gap-1 shrink-0">
                      {selectedId === template.id && (
                        <Check className="w-4 h-4 text-orange-500 mr-1" />
                      )}
                      <button
                        type="button"
                        onClick={() => setEditing(template)}
                        title={template.is_editable ? "Edit" : "View"}
                        className="p-1.5 rounded contrast-helper hover:text-gray-900 dark:hover:text-white"
                      >
                        <Pencil className="w-4 h-4" />
                      </button>
                      <button
                        type="button"
                        onClick={() => handleCopy(template)}
                        title="Copy to my structures"
                        className="p-1.5 rounded contrast-helper hover:text-gray-900 dark:hover:text-white"
                      >
                        <Copy className="w-4 h-4" />
                      </button>
                      {template.is_editable && (
                        <button
                          type="button"
                          onClick={() => handleDelete(template)}
                          title="Delete"
                          className="p-1.5 rounded contrast-helper hover:text-red-500"
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      )}
                    </div>
                  </div>

                  {template.is_stale && template.is_editable && (
                    <button
                      type="button"
                      onClick={() => handleReset(template)}
                      className="mt-2 text-xs text-orange-600 dark:text-orange-400 hover:underline"
                    >
                      Reset to the current default
                    </button>
                  )}

                  {isAdmin && template.scope === "install" && (
                    <button
                      type="button"
                      onClick={() =>
                        handleSelectInstallDefault(
                          template.is_install_default ? null : template.id,
                        )
                      }
                      className="mt-2 text-xs contrast-helper hover:text-gray-900 dark:hover:text-white"
                    >
                      {template.is_install_default
                        ? "Remove as install default"
                        : "Use as install default"}
                    </button>
                  )}
                </div>
              ))}
            </div>

            <div className="flex flex-wrap gap-2 pt-2">
              <button
                type="button"
                onClick={() => {
                  setCreatingScope("personal");
                  setCreating(true);
                }}
                className="inline-flex items-center gap-2 px-3 py-2 text-sm rounded-lg bg-gray-100 dark:bg-gray-700 text-gray-800 dark:text-gray-100 hover:bg-gray-200 dark:hover:bg-gray-600 transition-colors"
              >
                <Plus className="w-4 h-4" />
                New structure
              </button>
              {isAdmin && (
                <button
                  type="button"
                  onClick={() => {
                    setCreatingScope("install");
                    setCreating(true);
                  }}
                  className="inline-flex items-center gap-2 px-3 py-2 text-sm rounded-lg bg-gray-100 dark:bg-gray-700 text-gray-800 dark:text-gray-100 hover:bg-gray-200 dark:hover:bg-gray-600 transition-colors"
                >
                  <Plus className="w-4 h-4" />
                  New install structure
                </button>
              )}
            </div>
          </>
        )}
      </SettingsPanel>

      <NotesTemplateEditorModal
        isOpen={Boolean(editing) || creating}
        onClose={() => {
          setEditing(null);
          setCreating(false);
        }}
        template={editing}
        readOnly={Boolean(editing && !editing.is_editable)}
        builtinSections={data?.builtin.sections ?? ""}
        maxSectionsLength={data?.limits.max_sections_length ?? 8000}
        maxDescriptionLength={data?.limits.max_description_length ?? 200}
        onSave={(payload) => handleSave(editing, payload)}
      />
    </SettingsSection>
  );
}
