"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { X, Loader2, Eye, RotateCcw, Sparkles } from "lucide-react";
import {
  NotesTemplate,
  generateNotesStructure,
  getGeneratedNotesStructure,
  previewNotesPrompt,
} from "@/lib/api";
import { useNotificationStore } from "@/lib/notificationStore";

interface NotesTemplateEditorModalProps {
  isOpen: boolean;
  onClose: () => void;
  /** Null when creating a new template. */
  template: NotesTemplate | null;
  builtinSections: string;
  maxSectionsLength: number;
  maxDescriptionLength: number;
  readOnly?: boolean;
  onSave: (payload: {
    name: string;
    description: string;
    sections: string;
  }) => Promise<void>;
}

const POLL_INTERVAL_MS = 2000;
const POLL_TIMEOUT_MS = 240000;

/**
 * Work surface for the *editable half* of the notes prompt.
 *
 * Three panes, left to right: generate, edit, verify. The generator drafts a
 * structure from a plain-language brief; the editor is where the user owns it;
 * the preview shows the assembled prompt with the protected parts included, so
 * it is obvious that fidelity rules, table syntax and the transcript are not
 * theirs to change. The preview renders server-side against a sample transcript
 * and never calls a model.
 */
export default function NotesTemplateEditorModal({
  isOpen,
  onClose,
  template,
  builtinSections,
  maxSectionsLength,
  maxDescriptionLength,
  readOnly = false,
  onSave,
}: NotesTemplateEditorModalProps) {
  const { addNotification } = useNotificationStore();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [sections, setSections] = useState("");
  const [saving, setSaving] = useState(false);
  const [preview, setPreview] = useState<string | null>(null);
  const [loadingPreview, setLoadingPreview] = useState(false);
  const [brief, setBrief] = useState("");
  const [generating, setGenerating] = useState(false);
  // Portals need a DOM to target, which does not exist during SSR.
  const [mounted, setMounted] = useState(false);
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    setMounted(true);
    return () => {
      if (pollTimer.current) clearTimeout(pollTimer.current);
    };
  }, []);

  useEffect(() => {
    if (!isOpen) return;
    setName(template?.name ?? "");
    setDescription(template?.description ?? "");
    setSections(template?.sections ?? builtinSections);
    setPreview(null);
    setBrief("");
  }, [isOpen, template, builtinSections]);

  const handlePreview = useCallback(async () => {
    setLoadingPreview(true);
    try {
      const result = await previewNotesPrompt({ sections });
      setPreview(result.prompt);
    } catch (error) {
      console.error("Failed to build prompt preview", error);
      addNotification({
        type: "error",
        message: "Could not build the prompt preview. Check the structure text.",
      });
    } finally {
      setLoadingPreview(false);
    }
  }, [sections, addNotification]);

  const handleGenerate = async () => {
    if (!brief.trim()) return;
    setGenerating(true);
    try {
      const { job_id } = await generateNotesStructure(brief);
      const deadline = Date.now() + POLL_TIMEOUT_MS;

      const poll = async () => {
        try {
          const result = await getGeneratedNotesStructure(job_id);
          if (result.status === "completed") {
            // Filled in for review, never saved automatically: the structure
            // decides how every future meeting is written up, so it gets read
            // before it is kept.
            setName(result.name ?? "");
            setDescription(result.description ?? "");
            setSections(result.sections ?? "");
            setPreview(null);
            setGenerating(false);
            addNotification({
              type: "success",
              message: "Structure drafted. Review it, then save.",
            });
            return;
          }
          if (result.status === "error") {
            setGenerating(false);
            addNotification({
              type: "error",
              message: result.error || "Could not generate a structure.",
            });
            return;
          }
          if (Date.now() > deadline) {
            setGenerating(false);
            addNotification({
              type: "error",
              message: "The structure is taking too long. Try again.",
            });
            return;
          }
          pollTimer.current = setTimeout(() => void poll(), POLL_INTERVAL_MS);
        } catch (error) {
          console.error("Failed to read the generation job", error);
          setGenerating(false);
          addNotification({
            type: "error",
            message: "Lost track of the generation request. Try again.",
          });
        }
      };

      pollTimer.current = setTimeout(() => void poll(), POLL_INTERVAL_MS);
    } catch (error) {
      console.error("Failed to start structure generation", error);
      setGenerating(false);
      addNotification({
        type: "error",
        message:
          "Could not start generation. Check that an AI provider is configured.",
      });
    }
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await onSave({ name, description, sections });
      onClose();
    } catch (error) {
      console.error("Failed to save notes template", error);
    } finally {
      setSaving(false);
    }
  };

  if (!isOpen || !mounted) return null;

  const overLimit = sections.length > maxSectionsLength;

  // Rendered into document.body rather than in place. SettingsSection applies
  //, and an element with a backdrop-filter becomes the containing
  // block for fixed-position descendants -- so an in-place modal would size
  // itself to the settings card and overlap the sections around it.
  return createPortal(
    <div className="fixed inset-0 z-[100] flex items-center justify-center p-4 sm:p-6 bg-scrim">
      <div className="bg-surface-card rounded-2xl shadow-2xl w-full max-w-[1400px] h-[92vh] overflow-hidden border border-surface-border flex flex-col">
        <div className="flex items-center justify-between px-6 py-4 border-b border-surface-border shrink-0">
          <div>
            <h2 className="text-xl font-semibold text-foreground">
              {readOnly
                ? template?.name
                : template
                  ? "Edit Notes Structure"
                  : "New Notes Structure"}
            </h2>
            <p className="mt-0.5 text-xs contrast-helper">
              Describe what you want, edit the structure, then check the prompt
              it produces.
            </p>
          </div>
          <button
            onClick={onClose}
            className="contrast-helper hover:text-foreground"
            aria-label="Close"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="flex-1 min-h-0 grid gap-6 p-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)_minmax(0,1fr)] overflow-y-auto lg:overflow-hidden">
          {/* Generate */}
          <div className="flex flex-col min-h-0 gap-3">
            <div>
              <h3 className="text-sm font-medium text-contrast-muted">
                Generate
              </h3>
              <p className="mt-1 text-xs contrast-helper">
                Describe the meetings you run and what you need out of them. The
                AI drafts a structure you can edit before saving.
              </p>
            </div>
            <textarea
              value={brief}
              disabled={readOnly || generating}
              onChange={(event) => setBrief(event.target.value)}
              placeholder="e.g. I run weekly user interviews. I need the questions I asked, what the participant did and said, the insights worth acting on, and follow-ups for next time. No action item table."
              className="flex-1 min-h-[9rem] w-full p-3 text-sm rounded-lg border border-control-border bg-surface-page text-foreground focus:ring-2 focus-visible:outline-focus-ring outline-none transition-all disabled:opacity-60 resize-none"
            />
            <button
              type="button"
              onClick={handleGenerate}
              disabled={readOnly || generating || !brief.trim()}
              className="inline-flex items-center justify-center gap-2 px-4 py-2 text-sm rounded-lg bg-action text-foreground hover:bg-action transition-colors disabled:opacity-60"
            >
              {generating ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Sparkles className="w-4 h-4" />
              )}
              {generating ? "Drafting..." : "Generate structure"}
            </button>
            <p className="text-xs contrast-helper">
              Uses your configured AI provider. The draft replaces what is in the
              editor; nothing is saved until you save it.
            </p>
          </div>

          {/* Edit */}
          <div className="flex flex-col min-h-0 gap-4">
            <div>
              <label className="block text-sm font-medium text-contrast-muted mb-2">
                Name
              </label>
              <input
                type="text"
                value={name}
                disabled={readOnly}
                onChange={(event) => setName(event.target.value)}
                placeholder="e.g. User interview notes"
                className="w-full p-2.5 rounded-lg border border-control-border bg-surface-page text-foreground focus:ring-2 focus-visible:outline-focus-ring outline-none transition-all disabled:opacity-60"
              />
            </div>

            <div>
              <div className="flex items-center justify-between mb-2">
                <label className="block text-sm font-medium text-contrast-muted">
                  Description
                </label>
                <span className="text-xs contrast-helper">
                  {description.length} / {maxDescriptionLength}
                </span>
              </div>
              <input
                type="text"
                value={description}
                disabled={readOnly}
                maxLength={maxDescriptionLength}
                onChange={(event) => setDescription(event.target.value)}
                placeholder="e.g. Questions, observations and follow-ups for user interviews"
                className="w-full p-2.5 rounded-lg border border-control-border bg-surface-page text-foreground focus:ring-2 focus-visible:outline-focus-ring outline-none transition-all disabled:opacity-60"
              />
            </div>

            <div className="flex flex-col min-h-0 flex-1">
              <div className="flex items-center justify-between mb-2">
                <label className="block text-sm font-medium text-contrast-muted">
                  Section Structure
                </label>
                <span
                  className={`text-xs ${overLimit ? "text-danger-text" : "contrast-helper"}`}
                >
                  {sections.length} / {maxSectionsLength}
                </span>
              </div>
              <textarea
                value={sections}
                disabled={readOnly}
                onChange={(event) => setSections(event.target.value)}
                spellCheck={false}
                className="flex-1 min-h-[18rem] w-full p-3 font-mono text-xs rounded-lg border border-control-border bg-surface-page text-foreground focus:ring-2 focus-visible:outline-focus-ring outline-none transition-all disabled:opacity-60 resize-none"
              />
              <p className="mt-2 text-xs contrast-helper">
                Markdown headings and their descriptions. This controls what the
                notes contain. Accuracy rules, table formatting and the meeting
                transcript are added automatically and cannot be edited.
              </p>
            </div>

            {!readOnly && (
              <button
                type="button"
                onClick={() => setSections(builtinSections)}
                className="inline-flex items-center gap-2 text-sm contrast-helper hover:text-foreground shrink-0"
              >
                <RotateCcw className="w-4 h-4" />
                Reset to the Nojoin default
              </button>
            )}
          </div>

          {/* Verify */}
          <div className="flex flex-col min-h-0 gap-3">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-medium text-contrast-muted">
                Assembled Prompt
              </h3>
              <button
                type="button"
                onClick={handlePreview}
                disabled={loadingPreview}
                className="inline-flex items-center gap-2 px-3 py-1.5 text-sm rounded-lg bg-surface-inset text-foreground hover:bg-surface-inset transition-colors disabled:opacity-60"
              >
                {loadingPreview ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <Eye className="w-4 h-4" />
                )}
                Preview
              </button>
            </div>
            <pre className="flex-1 min-h-[12rem] overflow-auto whitespace-pre-wrap break-words p-3 rounded-lg border border-surface-border bg-surface-page text-[11px] leading-relaxed text-contrast-muted">
              {preview ??
                "Select Preview to see the exact prompt this structure produces, using a short sample transcript. No AI request is made."}
            </pre>
          </div>
        </div>

        <div className="flex items-center justify-end gap-3 px-6 py-4 border-t border-surface-border shrink-0">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm rounded-lg contrast-helper hover:text-foreground"
          >
            {readOnly ? "Close" : "Cancel"}
          </button>
          {!readOnly && (
            <button
              onClick={handleSave}
              disabled={saving || overLimit || !name.trim() || !sections.trim()}
              className="inline-flex items-center gap-2 px-4 py-2 text-sm rounded-lg bg-action text-foreground hover:bg-action transition-colors disabled:opacity-60"
            >
              {saving && <Loader2 className="w-4 h-4 animate-spin" />}
              Save
            </button>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}
