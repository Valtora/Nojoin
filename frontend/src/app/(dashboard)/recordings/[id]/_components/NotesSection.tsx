"use client";

import { useState } from "react";
import { FileText, RefreshCw, X } from "lucide-react";

import NotesView from "@/components/NotesView";
import type { Recording } from "@/types";

interface NotesSectionProps {
  active: boolean;
  recording: Recording;
  isGenerating: boolean;
  canUndo: boolean;
  canRedo: boolean;
  onNotesChange: (notes: string) => void;
  onGenerateNotes: (notesTemplateId?: number | null) => Promise<void>;
  onFindAndReplace: (
    find: string,
    replace: string,
    options?: { caseSensitive?: boolean; useRegex?: boolean },
  ) => void | Promise<void>;
  onUndo: () => void;
  onRedo: () => void;
  onExport: () => void;
}

export default function NotesSection({
  active,
  recording,
  isGenerating,
  canUndo,
  canRedo,
  onNotesChange,
  onGenerateNotes,
  onFindAndReplace,
  onUndo,
  onRedo,
  onExport,
}: NotesSectionProps) {
  // Dismissal is local and deliberately not persisted: the banner is advice,
  // not a task, and it reappears on reload while the notes remain stale.
  const [dismissed, setDismissed] = useState(false);
  const showStaleBanner =
    !isGenerating &&
    !dismissed &&
    !!recording.transcript?.notes_stale_documents &&
    !!recording.transcript?.notes;

  return (
    <div
      className={`absolute inset-0 flex flex-col ${active ? "z-10 visible" : "z-0 invisible"}`}
    >
      {showStaleBanner && (
        <div className="flex items-start gap-3 border-b border-amber-200 bg-amber-50 px-4 py-3 dark:border-amber-500/20 dark:bg-amber-900/20">
          <FileText className="mt-0.5 h-4 w-4 flex-shrink-0 text-amber-600 dark:text-amber-400" />
          <p className="min-w-0 flex-1 text-sm text-amber-800 dark:text-amber-300">
            A document was added after these notes were written, so they do not
            reflect it yet. Regenerating will overwrite any edits you have made.
          </p>
          <button
            type="button"
            onClick={() => void onGenerateNotes()}
            className="inline-flex flex-shrink-0 items-center gap-1.5 rounded-lg bg-amber-600 px-3 py-1.5 text-xs font-semibold text-white transition-colors hover:bg-amber-700"
          >
            <RefreshCw className="h-3.5 w-3.5" />
            Regenerate
          </button>
          <button
            type="button"
            onClick={() => setDismissed(true)}
            aria-label="Dismiss"
            className="flex-shrink-0 rounded-lg p-1 text-amber-600 transition-colors hover:bg-amber-100 dark:text-amber-400 dark:hover:bg-amber-900/40"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      )}
      <NotesView
        recordingId={recording.id}
        notes={recording.transcript?.notes || null}
        onNotesChange={onNotesChange}
        onGenerateNotes={onGenerateNotes}
        onFindAndReplace={onFindAndReplace}
        onUndo={onUndo}
        onRedo={onRedo}
        canUndo={canUndo}
        canRedo={canRedo}
        isGenerating={isGenerating}
        onExport={onExport}
      />
    </div>
  );
}
