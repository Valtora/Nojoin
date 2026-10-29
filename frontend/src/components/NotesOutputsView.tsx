"use client";

import { useCallback, useEffect, useState, type KeyboardEvent } from "react";
import NotesView, { type NotesViewProps } from "./NotesView";
import NotesTemplatePicker from "./NotesTemplatePicker";
import ConfirmationModal from "./ConfirmationModal";
import Button from "./ui/Button";
import { ButtonBase } from "./ui/nativeBases";
import { deleteNotesOutput, exportContent, generateNotes, listNotesOutputs, updateNotesOutput } from "@/lib/api";
import { DEMO_MODE } from "@/lib/demo";
import { getErrorMessage } from "@/lib/errors";
import type { NotesOutput } from "@/types";
import GenerationCoverage from "./GenerationCoverage";

interface NotesOutputsViewProps extends NotesViewProps {
  onPrimarySelected?: (primary: boolean) => void;
  primaryError?: string | null;
  generationMetadata?: NotesOutput["notes_generation_metadata"];
  allowCreateOutput?: boolean;
}

/** Tab labels, numbering a repeated name ("Minutes", "Minutes 2"). */
function outputLabels(outputs: NotesOutput[]): Map<number, string> {
  const seen = new Map<string, number>();
  return new Map(outputs.map((output) => {
    const count = (seen.get(output.name) ?? 0) + 1;
    seen.set(output.name, count);
    return [output.id, count > 1 ? `${output.name} ${count}` : output.name];
  }));
}

/** Extra sets never enter the primary notes editor's history or save path. */
export default function NotesOutputsView(props: NotesOutputsViewProps) {
  const [outputs, setOutputs] = useState<NotesOutput[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [busy, setBusy] = useState<Record<string, boolean>>({});
  const [errors, setErrors] = useState<Record<string, string | null>>({});
  const [confirmDelete, setConfirmDelete] = useState(false);
  const selected = outputs.find((output) => output.id === selectedId);
  const extraGenerating = outputs.some((output) => output.notes_status === "generating");
  const selectedBusy = selected ? busy[selected.id] || selected.notes_status === "generating" : props.isGenerating;
  const selectedError = selected
    ? errors[selected.id] || (selected.notes_status === "error" ? selected.error_message : null)
    : props.primaryError;
  const labels = outputLabels(outputs);
  const primarySelected = !selected;
  const { onPrimarySelected, recordingId, notes: primaryNotes } = props;
  useEffect(() => { onPrimarySelected?.(primarySelected); }, [onPrimarySelected, primarySelected]);

  const load = useCallback(async (onlyGenerating: boolean) => {
    // The public demo replays captured reads only, and has no extra sets.
    if (DEMO_MODE) return;
    try {
      const fresh = await listNotesOutputs(recordingId);
      // While polling, only a generating set can change on the server. The
      // others change only by this user's edits, and a reply that crossed one
      // in flight would reset the editor under them.
      setOutputs((previous) => onlyGenerating
        ? previous.map((output) => output.notes_status === "generating"
          ? fresh.find((item) => item.id === output.id) ?? output : output)
        : fresh);
    } catch (cause) {
      console.error("Failed to load notes sets", cause);
    }
  }, [recordingId]);

  // A find and replace or a speaker rename rewrites every set along with the
  // first notes, so a change to those reloads the sets too.
  useEffect(() => { void load(false); }, [load, primaryNotes]);

  useEffect(() => {
    if (!extraGenerating) return;
    const timer = setInterval(() => void load(true), 3000);
    return () => clearInterval(timer);
  }, [extraGenerating, load]);

  const run = async (target: number | "create", action: () => Promise<void>) => {
    setBusy((previous) => ({ ...previous, [target]: true }));
    setErrors((previous) => ({ ...previous, [target]: null }));
    try { await action(); }
    catch (cause) {
      setErrors((previous) => ({ ...previous, [target]: getErrorMessage(cause, "Could not update notes output.") }));
    }
    finally { setBusy((previous) => ({ ...previous, [target]: false })); }
  };

  const generate = (templateId: number | null, create: boolean) => run(create ? "create" : selected!.id, async () => {
    const response = await generateNotes(recordingId, templateId,
      create ? { create_output: true } : { output_id: selected?.id });
    if (response.output) {
      const output = response.output;
      setOutputs((previous) => [...previous.filter((item) => item.id !== output.id), output].sort((a, b) => a.id - b.id));
      if (create) setSelectedId(output.id);
    }
  });

  const save = async (outputId: number, notes: string) => {
    try {
      const output = await updateNotesOutput(recordingId, outputId, notes);
      setOutputs((previous) => previous.map((item) => item.id === output.id ? output : item));
      setErrors((previous) => ({ ...previous, [outputId]: null }));
    } catch (cause) {
      setErrors((previous) => ({ ...previous, [outputId]: getErrorMessage(cause, "Could not save notes output.") }));
    }
  };

  const remove = () => selected && run(selected.id, async () => {
    if (!selected) return;
    await deleteNotesOutput(recordingId, selected.id);
    setOutputs((previous) => previous.filter((item) => item.id !== selected.id));
    setSelectedId(null);
  });

  const toolbarActions = <>
    {(props.notes || props.allowCreateOutput) && <NotesTemplatePicker label="Generate another set" disabled={busy.create}
      onSelect={(templateId) => void generate(templateId, true)} />}
    {selected && <Button size="sm" variant="ghost" disabled={selectedBusy}
      onClick={() => setConfirmDelete(true)}>Delete set</Button>}
  </>;

  const onTabKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const ids = [null, ...outputs.map((output) => output.id)];
    const index = ids.indexOf(selected?.id ?? null);
    const targets: Record<string, number> = { ArrowLeft: index - 1, ArrowRight: index + 1, Home: 0, End: ids.length - 1 };
    if (!(event.key in targets)) return;
    event.preventDefault();
    const next = (targets[event.key] + ids.length) % ids.length;
    setSelectedId(ids[next]);
    event.currentTarget.querySelectorAll<HTMLElement>('[role="tab"]')[next]?.focus();
  };

  return <div className="flex h-full min-h-0 flex-col">
    {outputs.length > 0 && <div role="tablist" aria-label="Notes outputs" onKeyDown={onTabKeyDown} className="flex flex-wrap gap-1 border-b border-control-border p-2">
      <ButtonBase role="tab" id="notes-output-primary" aria-controls="notes-output-panel" aria-selected={!selected}
        tabIndex={selected ? -1 : 0}
        onClick={() => setSelectedId(null)} className="rounded px-3 py-2 text-sm text-foreground aria-selected:bg-surface-inset">Meeting notes</ButtonBase>
      {outputs.map((output) => <ButtonBase key={output.id} role="tab" id={`notes-output-${output.id}`}
        aria-controls="notes-output-panel" aria-selected={selected?.id === output.id}
        tabIndex={selected?.id === output.id ? 0 : -1}
        onClick={() => setSelectedId(output.id)} className="rounded px-3 py-2 text-sm text-foreground aria-selected:bg-surface-inset">{labels.get(output.id)}</ButtonBase>)}
    </div>}
    {errors.create && <p role="alert" className="p-3 text-sm text-status-danger-fg">{errors.create}</p>}
    {selectedError && <p role="alert" className="p-3 text-sm text-status-danger-fg">{selectedError}</p>}
    <GenerationCoverage metadata={selected ? selected.notes_generation_metadata : props.generationMetadata} />
    <div className="min-h-0 flex-1" role={outputs.length ? "tabpanel" : undefined}
      id="notes-output-panel" aria-labelledby={outputs.length ? selected ? `notes-output-${selected.id}` : "notes-output-primary" : undefined}>
      {selected ? <NotesView {...props} key={selected.id} notes={selected.notes} localHistory fixedTemplate
        activeTemplateId={selected.notes_template_id} isGenerating={Boolean(selectedBusy)}
        onNotesChange={(notes) => void save(selected.id, notes)}
        onGenerateNotes={() => generate(null, false)}
        // The same replace as the first notes' (transcript and every set), then
        // this set's new text.
        onFindAndReplace={async (find, replacement, options) => {
          await props.onFindAndReplace(find, replacement, options);
          await load(false);
        }}
        onExport={() => void run(selected.id, () => exportContent(recordingId, ["notes"], "txt", {}, true, selected.id))}
        toolbarActions={toolbarActions} />
        : <NotesView {...props} key="primary" toolbarActions={toolbarActions} />}
    </div>
    <ConfirmationModal isOpen={confirmDelete} onClose={() => setConfirmDelete(false)} onConfirm={() => void remove()}
      title="Delete notes set?" message={`Delete “${selected ? labels.get(selected.id) : "this set"}”? The meeting's first notes will be kept.`}
      confirmText="Delete set" isDangerous />
  </div>;
}
