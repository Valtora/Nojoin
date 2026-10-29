"use client";

import { useCallback, useEffect, useState, type KeyboardEvent } from "react";
import NotesView, { type NotesViewProps } from "./NotesView";
import NotesTemplatePicker from "./NotesTemplatePicker";
import GlossaryOutputView from "./GlossaryOutputView";
import ConfirmationModal from "./ConfirmationModal";
import Button from "./ui/Button";
import { ButtonBase } from "./ui/nativeBases";
import { deleteNotesOutput, exportContent, generateNotes, listNotesOutputs, updateNotesOutput } from "@/lib/api";
import { DEMO_MODE } from "@/lib/demo";
import { getErrorMessage } from "@/lib/errors";
import type { NotesOutput } from "@/types";

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
export default function NotesOutputsView(props: NotesViewProps & { onPrimarySelected?: (primary: boolean) => void }) {
  const [outputs, setOutputs] = useState<NotesOutput[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const selected = outputs.find((output) => output.id === selectedId);
  const extraGenerating = outputs.some((output) => output.notes_status === "generating");
  const generating = props.isGenerating || busy || extraGenerating;
  const labels = outputLabels(outputs);
  const primarySelected = !selected;
  const { onPrimarySelected, recordingId, notes: primaryNotes } = props;
  useEffect(() => { onPrimarySelected?.(primarySelected); }, [onPrimarySelected, primarySelected]);

  const load = useCallback(async (onlyGenerating: boolean) => {
    // The public demo replays captured reads only, and has no extra sets.
    if (DEMO_MODE) return;
    try {
      const fresh = await listNotesOutputs(recordingId);
      // Preserve edits to completed sets, while discovering automatically
      // created outputs as well as refreshing those already generating.
      setOutputs((previous) => onlyGenerating
        ? [...previous.map((output) => output.notes_status === "generating"
          ? fresh.find((item) => item.id === output.id) ?? output : output),
        ...fresh.filter((item) => !previous.some((output) => output.id === item.id))]
        : fresh);
    } catch (cause) {
      console.error("Failed to load notes sets", cause);
    }
  }, [recordingId]);

  // A find and replace or a speaker rename rewrites every set along with the
  // first notes, so a change to those reloads the sets too.
  useEffect(() => { void load(false); }, [load, primaryNotes, props.isGenerating]);

  useEffect(() => {
    if (!extraGenerating && !props.isGenerating) return;
    const timer = setInterval(() => void load(true), 3000);
    return () => clearInterval(timer);
  }, [extraGenerating, props.isGenerating, load]);

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try { await action(); }
    catch (cause) { setError(getErrorMessage(cause, "Could not update notes output.")); }
    finally { setBusy(false); }
  };

  const generate = (
    templateId: number | null, create: boolean, kind: "notes" | "glossary" = "notes", extractMore = false,
  ) => run(async () => {
    const response = await generateNotes(recordingId, templateId,
      create ? { create_output: true, ...(kind === "glossary" ? { kind } : {}) }
        : { output_id: selected?.id, ...(extractMore ? { extract_more: true } : {}) });
    if (response.output) {
      const output = response.output;
      setOutputs((previous) => [...previous.filter((item) => item.id !== output.id), output].sort((a, b) => a.id - b.id));
      setSelectedId(output.id);
    }
  });

  const save = async (outputId: number, notes: string) => {
    try {
      const output = await updateNotesOutput(recordingId, outputId, notes);
      setOutputs((previous) => previous.map((item) => item.id === output.id ? output : item));
    } catch (cause) { setError(getErrorMessage(cause, "Could not save notes output.")); }
  };

  const remove = () => run(async () => {
    if (!selected) return;
    await deleteNotesOutput(recordingId, selected.id);
    setOutputs((previous) => previous.filter((item) => item.id !== selected.id));
    setSelectedId(null);
  });

  const toolbarActions = <>
    {props.notes && <NotesTemplatePicker label="Generate another set" disabled={generating}
      onSelect={(templateId) => void generate(templateId, true)}
      onSelectGlossary={() => void generate(null, true, "glossary")} />}
    {selected && <Button size="sm" variant="ghost" disabled={generating}
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
    {(error || selected?.error_message) && <p role="alert" className="p-3 text-sm text-status-danger-fg">{error || selected?.error_message}</p>}
    <div className="min-h-0 flex-1" role={outputs.length ? "tabpanel" : undefined}
      id="notes-output-panel" aria-labelledby={outputs.length ? selected ? `notes-output-${selected.id}` : "notes-output-primary" : undefined}>
      {selected?.kind === "glossary" ? <GlossaryOutputView key={selected.id} notes={selected.notes}
        isGenerating={generating}
        onRegenerate={() => void generate(null, false)}
        onExtractMore={() => void generate(null, false, "glossary", true)}
        toolbarActions={toolbarActions} /> : selected ? <NotesView {...props} key={selected.id} notes={selected.notes} localHistory fixedTemplate
        activeTemplateId={selected.notes_template_id} isGenerating={busy || props.isGenerating || selected.notes_status === "generating"}
        onNotesChange={(notes) => void save(selected.id, notes)}
        onGenerateNotes={() => generate(null, false)}
        // The same replace as the first notes' (transcript and every set), then
        // this set's new text.
        onFindAndReplace={async (find, replacement, options) => {
          await props.onFindAndReplace(find, replacement, options);
          await load(false);
        }}
        onExport={() => void run(() => exportContent(recordingId, ["notes"], "txt", {}, true, selected.id))}
        toolbarActions={toolbarActions} />
        : <NotesView {...props} key="primary" toolbarActions={toolbarActions} isGenerating={busy || props.isGenerating} />}
    </div>
    <ConfirmationModal isOpen={confirmDelete} onClose={() => setConfirmDelete(false)} onConfirm={() => void remove()}
      title="Delete notes set?" message={`Delete “${selected ? labels.get(selected.id) : "this set"}”? The meeting's first notes will be kept.`}
      confirmText="Delete set" isDangerous />
  </div>;
}
