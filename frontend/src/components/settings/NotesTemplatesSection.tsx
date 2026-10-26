"use client";

import { ButtonBase } from "@/components/ui/nativeBases";
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
import SettingsBlock from "./SettingsBlock";
import SettingsCard from "./SettingsCard";
import SettingsStatusBadge from "./SettingsStatusBadge";
import { SETTINGS_BUTTON_SECONDARY } from "./settingsControls";

const ICON_BUTTON =
  "inline-flex h-8 w-8 items-center justify-center rounded-lg contrast-helper transition-colors hover:bg-surface-inset hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring";
import NotesTemplateEditorModal from "./NotesTemplateEditorModal";

interface NotesTemplatesSectionProps {
  settings: Settings;
  /**
   * Apply and save immediately (default selection is a discrete control).
   * Resolves once the save has settled, so the install default can refetch.
   */
  onPersist: (newSettings: Settings) => void | Promise<void>;
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
  const [savingInstallDefault, setSavingInstallDefault] = useState(false);

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

  // Unlike the per-user default -- whose tick is rendered from `settings`, so
  // it updates optimistically -- the install default badge comes from
  // `is_install_default` on the server's template list. Without refetching, a
  // save that worked perfectly still leaves the row unchanged and reads as a
  // dead link (issue #149).
  const handleSelectInstallDefault = async (templateId: number | null) => {
    setSavingInstallDefault(true);
    try {
      await onPersist({ ...settings, install_notes_template_id: templateId });
    } catch (error) {
      // The caller reports the failure to the user; refetching below is what
      // stops the row from claiming a default the server never stored.
      console.error("Failed to set the install default notes structure", error);
    } finally {
      await refresh();
      setSavingInstallDefault(false);
    }
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
    <SettingsCard
      id="notes-structure"
      title="Notes structure"
      description="Choose how generated meeting notes are organised. Accuracy rules, table formatting and the transcript itself are fixed; the sections and their emphasis are yours."
    >
      <SettingsBlock className="space-y-4">
        {loading && !data ? (
          <div className="flex items-center gap-2 text-sm contrast-helper">
            <Loader2 className="w-4 h-4 animate-spin" />
            Loading structures...
          </div>
        ) : (
          <>
            <div className="space-y-2">
              <ButtonBase
                data-ui-fill={selectedId === null ? "action-tint" : undefined}
                type="button"
                onClick={() => handleSelectDefault(null)}
                className={`w-full text-left p-3 rounded-lg border transition-colors ${
                  selectedId === null
                    ? "border-action bg-action-tint"
                    : "border-control-border hover:bg-surface-inset"
                }`}
              >
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <p className="text-sm font-medium text-foreground">
                      {data?.builtin.name ?? "Nojoin default"}
                    </p>
                    <p className="text-xs contrast-helper mt-0.5">
                      {data?.builtin.description ??
                        "Summary, decisions, action items, detailed notes."}{" "}
                      Follows the shared default when your administrator has set
                      one.
                    </p>
                  </div>
                  {selectedId === null && (
                    <Check className="w-4 h-4 text-action-text shrink-0" />
                  )}
                </div>
              </ButtonBase>

              {data?.templates.map((template) => (
                <div
                  key={template.id}
                  className={`p-3 rounded-lg border transition-colors ${
                    selectedId === template.id
                      ? "border-action bg-action-tint"
                      : "border-control-border"
                  }`}
                >
                  <div className="flex items-start justify-between gap-3">
                    <ButtonBase
                      type="button"
                      onClick={() => handleSelectDefault(template.id)}
                      className="text-left flex-1"
                    >
                      <p className="text-sm font-medium text-foreground flex items-center gap-2 flex-wrap">
                        {template.name}
                        {template.scope === "install" && (
                          <SettingsStatusBadge tone="neutral" className="px-2 py-0.5">
                            Shared
                          </SettingsStatusBadge>
                        )}
                        {template.is_install_default && (
                          <SettingsStatusBadge tone="info" className="px-2 py-0.5">
                            Shared default
                          </SettingsStatusBadge>
                        )}
                      </p>
                      {template.description && (
                        <p className="text-xs contrast-helper mt-0.5">
                          {template.description}
                        </p>
                      )}
                      {template.is_stale && (
                        <p className="text-xs text-status-warning-fg mt-1 flex items-center gap-1">
                          <AlertCircle className="w-3.5 h-3.5" />
                          The Nojoin default has improved since this was copied.
                        </p>
                      )}
                    </ButtonBase>

                    <div className="flex items-center gap-1 shrink-0">
                      {selectedId === template.id && (
                        <Check className="w-4 h-4 text-action-text mr-1" />
                      )}
                      <ButtonBase
                        data-ui-hover="inset"
                        chrome="action"
                        type="button"
                        onClick={() => setEditing(template)}
                        title={template.is_editable ? "Edit" : "View"}
                        aria-label={`${template.is_editable ? "Edit" : "View"} ${template.name}`}
                        className={ICON_BUTTON}
                      >
                        <Pencil className="w-4 h-4" />
                      </ButtonBase>
                      <ButtonBase
                        data-ui-hover="inset"
                        chrome="action"
                        type="button"
                        onClick={() => handleCopy(template)}
                        title="Copy to my structures"
                        aria-label={`Copy ${template.name} to my structures`}
                        className={ICON_BUTTON}
                      >
                        <Copy className="w-4 h-4" />
                      </ButtonBase>
                      {template.is_editable && (
                        <ButtonBase
                          data-ui-hover="inset"
                          chrome="action"
                          type="button"
                          onClick={() => handleDelete(template)}
                          title="Delete"
                          aria-label={`Delete ${template.name}`}
                          className={ICON_BUTTON}
                        >
                          <Trash2 className="w-4 h-4" />
                        </ButtonBase>
                      )}
                    </div>
                  </div>

                  {template.is_stale && template.is_editable && (
                    <ButtonBase
                      type="button"
                      onClick={() => handleReset(template)}
                      className="mt-2 text-xs text-action-text hover:underline"
                    >
                      Reset to the current default
                    </ButtonBase>
                  )}

                  {isAdmin && template.scope === "install" && (
                    <ButtonBase
                      type="button"
                      disabled={savingInstallDefault}
                      onClick={() =>
                        void handleSelectInstallDefault(
                          template.is_install_default ? null : template.id,
                        )
                      }
                      className="mt-2 text-xs contrast-helper hover:text-foreground disabled:opacity-60"
                    >
                      {template.is_install_default
                        ? "Stop using as the shared default"
                        : "Make the shared default"}
                    </ButtonBase>
                  )}
                </div>
              ))}
            </div>

            <div className="flex flex-wrap gap-2 pt-2">
              <ButtonBase
                data-ui-fill="card"
                data-ui-hover="inset"
                data-ui-active="inset"
                data-ui-border="full"
                chrome="action"
                type="button"
                onClick={() => {
                  setCreatingScope("personal");
                  setCreating(true);
                }}
                className={SETTINGS_BUTTON_SECONDARY}
              >
                <Plus className="h-4 w-4" aria-hidden="true" />
                New structure
              </ButtonBase>
              {isAdmin && (
                <ButtonBase
                  data-ui-fill="card"
                  data-ui-hover="inset"
                  data-ui-active="inset"
                  data-ui-border="full"
                  chrome="action"
                  type="button"
                  onClick={() => {
                    setCreatingScope("install");
                    setCreating(true);
                  }}
                  className={SETTINGS_BUTTON_SECONDARY}
                >
                  <Plus className="h-4 w-4" aria-hidden="true" />
                  New shared structure
                </ButtonBase>
              )}
            </div>
          </>
        )}
      </SettingsBlock>

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
    </SettingsCard>
  );
}
