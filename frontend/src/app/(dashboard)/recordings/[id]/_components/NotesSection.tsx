"use client";

import NotesView from "@/components/NotesView";
import type { Recording } from "@/types";

interface NotesSectionProps {
  active: boolean;
  recording: Recording;
  isGenerating: boolean;
  canUndo: boolean;
  canRedo: boolean;
  onNotesChange: (notes: string) => void;
  onGenerateNotes: () => Promise<void>;
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
  return (
    <div
      className={`absolute inset-0 flex flex-col ${active ? "z-10 visible" : "z-0 invisible"}`}
    >
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
