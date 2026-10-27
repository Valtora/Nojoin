"use client";

import { ButtonBase } from "@/components/ui/nativeBases";
import { useState } from "react";
import { FileText, RefreshCw, X } from "lucide-react";

import NotesView from "@/components/NotesView";
import RichTextEditor from "@/components/RichTextEditor";
import type { Recording } from "@/types";

interface NotesSectionProps {
  active: boolean;
  recording: Recording;
  canExport?: boolean;
  inFlight?: boolean;
  hasRetainedPublication?: boolean;
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
  canExport = true,
  inFlight = false,
  hasRetainedPublication = false,
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
    !inFlight &&
    !isGenerating &&
    !dismissed &&
    !!recording.transcript?.notes_stale_documents &&
    !!recording.transcript?.notes;

  return (
    <div
      role="tabpanel"
      id="panel-notes"
      aria-labelledby="tab-notes"
      className={`absolute inset-0 flex flex-col ${active ? "z-10 visible" : "z-0 invisible"}`}
    >
      {showStaleBanner && (
        <div className="flex items-start gap-3 border-b border-status-warning-border bg-status-warning-bg px-4 py-3">
          <FileText className="mt-0.5 h-4 w-4 flex-shrink-0 text-status-warning-fg" />
          <p className="min-w-0 flex-1 text-sm text-status-warning-fg">
            A document was added, or a note&apos;s file turned on or off for the
            notes, after these notes were written, so they do not reflect it yet.
            Regenerating will overwrite any edits you have made.
          </p>
          <ButtonBase
            data-ui-fill="status-warning"
            type="button"
            onClick={() => void onGenerateNotes()}
            className="inline-flex flex-shrink-0 items-center gap-1.5 rounded-lg bg-status-warning-bg px-3 py-1.5 text-xs font-semibold text-status-warning-fg transition-colors hover:bg-status-warning-bg"
          >
            <RefreshCw className="h-3.5 w-3.5" />
            Regenerate
          </ButtonBase>
          <ButtonBase
            type="button"
            onClick={() => setDismissed(true)}
            aria-label="Dismiss"
            className="flex-shrink-0 rounded-lg p-1 text-status-warning-fg transition-colors hover:bg-status-warning-bg"
          >
            <X className="h-4 w-4" />
          </ButtonBase>
        </div>
      )}
      {inFlight && hasRetainedPublication && recording.transcript?.notes ? (
        <RichTextEditor content={recording.transcript.notes} editable={false} onChange={() => undefined} />
      ) : inFlight ? <p className="m-auto p-6 text-center text-sm text-contrast-helper">Meeting notes will be available once processing finishes. Add your own notes beneath the live transcript.</p> : <NotesView
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
        disableExport={!canExport}
      />}
    </div>
  );
}
