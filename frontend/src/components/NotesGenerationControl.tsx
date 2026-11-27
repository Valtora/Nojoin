"use client";

import { memo, useCallback, useEffect, useRef, useState } from "react";
import GenerateNotesButton from "./GenerateNotesButton";
import NotesTemplatePicker from "./NotesTemplatePicker";
import { ButtonBase } from "@/components/ui/nativeBases";
import { loadingButtonProps } from "@/components/ui/loadingButton";

interface NotesGenerationControlProps {
  label: string;
  isGenerating: boolean;
  step: string | null;
  notesRunId?: string | null;
  activeTemplateId?: number | null;
  onGenerate: (templateId?: number | null) => Promise<void>;
  onCancel?: (notesRunId: string) => Promise<void>;
}

/** Pending cancellation belongs to this control, rather than the recording page. */
function NotesGenerationControl({ label, isGenerating, step, notesRunId, activeTemplateId,
  onGenerate, onCancel }: NotesGenerationControlProps) {
  const [pendingRun, setPendingRun] = useState<string | null>(null);
  const pending = useRef<string | null>(null);
  const control = useRef<HTMLDivElement>(null);
  const restoreFocus = useRef(false);
  const handleCancel = useCallback(async () => {
    if (!notesRunId || !onCancel || pending.current === notesRunId) return;
    const run = notesRunId;
    pending.current = run;
    restoreFocus.current = !!control.current?.contains(document.activeElement);
    setPendingRun(run);
    try {
      await onCancel(run);
    } finally {
      if (pending.current === run) {
        pending.current = null;
        setPendingRun(null);
      }
    }
  }, [notesRunId, onCancel]);

  // Cancel gives way to the structure arrow once the run ends, which would
  // drop its keyboard focus on the page body: hand it to the generate button.
  useEffect(() => {
    if (isGenerating || !restoreFocus.current) return;
    restoreFocus.current = false;
    const focused = document.activeElement;
    if (focused && focused !== document.body && !control.current?.contains(focused)) return;
    control.current?.querySelector("button")?.focus();
  }, [isGenerating]);

  const cancelling = pendingRun !== null && pendingRun === notesRunId;
  return <div ref={control} className="flex min-w-0 max-w-full items-stretch">
    <GenerateNotesButton label={label} isGenerating={isGenerating} step={step} onGenerate={onGenerate} />
    {isGenerating && notesRunId && onCancel ? <ButtonBase
      type="button" data-ui-fill="action" data-ui-hover="action-hover" chrome="action"
      {...loadingButtonProps(cancelling, undefined, () => void handleCancel())}
      className="flex items-center px-3 bg-action text-action-on text-sm rounded-r-md border-l border-action hover:bg-action-hover disabled:opacity-50 disabled:cursor-not-allowed aria-disabled:opacity-50 aria-disabled:cursor-not-allowed transition-colors"
    >{cancelling ? "Cancelling..." : "Cancel"}</ButtonBase> : <NotesTemplatePicker
      disabled={isGenerating} activeTemplateId={activeTemplateId} onSelect={onGenerate} />}
  </div>;
}

export default memo(NotesGenerationControl);
