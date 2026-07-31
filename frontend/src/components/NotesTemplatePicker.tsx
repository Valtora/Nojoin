"use client";

import { useEffect, useRef, useState } from "react";
import { ChevronDown, Check } from "lucide-react";
import { NotesTemplate, listNotesTemplates } from "@/lib/api";

interface NotesTemplatePickerProps {
  disabled?: boolean;
  /** Template that produced the current notes, if any. */
  activeTemplateId?: number | null;
  onSelect: (notesTemplateId: number | null) => void;
}

/**
 * Structure picker beside Regenerate Notes (issue #137).
 *
 * Loads lazily on first open: most people never change structure per meeting,
 * so the recording page should not pay for the request on every visit.
 */
export default function NotesTemplatePicker({
  disabled = false,
  activeTemplateId = null,
  onSelect,
}: NotesTemplatePickerProps) {
  const [open, setOpen] = useState(false);
  const [templates, setTemplates] = useState<NotesTemplate[] | null>(null);
  const [builtinName, setBuiltinName] = useState("Nojoin default");
  const [builtinDescription, setBuiltinDescription] = useState<string | null>(
    null,
  );
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open || templates) return;
    let cancelled = false;
    void (async () => {
      try {
        const data = await listNotesTemplates();
        if (cancelled) return;
        setTemplates(data.templates);
        setBuiltinName(data.builtin.name);
        setBuiltinDescription(data.builtin.description);
      } catch (error) {
        console.error("Failed to load notes structures", error);
        if (!cancelled) setTemplates([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, templates]);

  useEffect(() => {
    if (!open) return;
    const handleClickOutside = (event: MouseEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [open]);

  const handleSelect = (templateId: number | null) => {
    setOpen(false);
    onSelect(templateId);
  };

  return (
    <div className="relative flex" ref={containerRef}>
      {/* Right half of a segmented control: only the outer corners are rounded,
          and a divider stands in for the gap so neither button clips the other. */}
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        disabled={disabled}
        title="Generate with a different structure"
        aria-label="Choose notes structure"
        className="flex items-center px-2 bg-action text-action-on text-sm rounded-r-md border-l border-action hover:bg-action-hover disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
      >
        <ChevronDown className="w-4 h-4" />
      </button>

      {open && (
        <div className="absolute right-0 z-50 mt-1 w-64 rounded-md border border-surface-border bg-surface-card shadow-float py-1">
          <p className="px-3 py-1.5 text-[11px] uppercase tracking-wide contrast-helper">
            Generate with structure
          </p>
          <button
            type="button"
            onClick={() => handleSelect(null)}
            className="w-full flex items-start justify-between gap-2 px-3 py-2 text-sm text-left text-contrast-muted hover:bg-surface-inset"
          >
            <span className="min-w-0">
              <span className="block truncate">{builtinName}</span>
              {builtinDescription && (
                <span className="block text-xs contrast-helper line-clamp-2">
                  {builtinDescription}
                </span>
              )}
            </span>
            {activeTemplateId === null && (
              <Check className="w-4 h-4 text-action-text shrink-0 mt-0.5" />
            )}
          </button>
          {templates === null ? (
            <p className="px-3 py-2 text-sm contrast-helper">Loading...</p>
          ) : (
            templates.map((template) => (
              <button
                key={template.id}
                type="button"
                onClick={() => handleSelect(template.id)}
                className="w-full flex items-start justify-between gap-2 px-3 py-2 text-sm text-left text-contrast-muted hover:bg-surface-inset"
              >
                <span className="min-w-0">
                  <span className="block truncate">{template.name}</span>
                  {template.description && (
                    <span className="block text-xs contrast-helper line-clamp-2">
                      {template.description}
                    </span>
                  )}
                </span>
                {activeTemplateId === template.id && (
                  <Check className="w-4 h-4 text-action-text shrink-0 mt-0.5" />
                )}
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
}
