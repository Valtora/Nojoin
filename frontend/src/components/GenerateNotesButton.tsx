"use client";

import { memo } from "react";
import { Loader2, Sparkles } from "lucide-react";

import { ButtonBase } from "@/components/ui/nativeBases";
import { loadingButtonProps } from "@/components/ui/loadingButton";

interface GenerateNotesButtonProps {
  /** "Generate Notes" or "Regenerate Notes". */
  label: string;
  isGenerating: boolean;
  /** The step a long meeting's notes are on, shown in place of "Generating...". */
  step: string | null;
  onGenerate: (notesTemplateId?: number | null) => Promise<void>;
}

/**
 * The Notes toolbar's main button. While notes generate it reads the step they
 * are on. It is as wide as its text, so it may change width from step to step,
 * and is cut short only when the toolbar runs out of room, with the whole text
 * in the tooltip. A step change redraws this button alone.
 */
function GenerateNotesButton({ label, isGenerating, step, onGenerate }: GenerateNotesButtonProps) {
  const busyText = step ?? "Generating...";
  return (
    <>
      <ButtonBase
        data-ui-fill="action"
        data-ui-hover="action-hover" chrome="action"
        {...loadingButtonProps(isGenerating, undefined, () => onGenerate())}
        className="flex min-w-0 max-w-full items-center gap-1.5 px-3 py-1.5 bg-action text-action-on text-sm rounded-l-md hover:bg-action-hover disabled:opacity-50 disabled:cursor-not-allowed transition-colors aria-disabled:opacity-50 aria-disabled:cursor-not-allowed"
        title={isGenerating ? busyText : "Generate Notes with AI"}
      >
        {isGenerating ? (
          <Loader2 className="w-4 h-4 shrink-0 animate-spin" />
        ) : (
          <Sparkles className="w-4 h-4 shrink-0" />
        )}
        <span className="min-w-0 truncate">{isGenerating ? busyText : label}</span>
      </ButtonBase>
      {/* A button's changing text is not reliably read out, so the step is
          also a polite status while notes generate. */}
      {isGenerating ? (
        <span role="status" className="sr-only">{step ?? ""}</span>
      ) : null}
    </>
  );
}

export default memo(GenerateNotesButton);
