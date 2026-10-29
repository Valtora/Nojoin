"use client";

import { useEffect, useState, type KeyboardEvent } from "react";
import NotesView, { type NotesViewProps } from "./NotesView";
import NotesTemplatePicker from "./NotesTemplatePicker";
import ConfirmationModal from "./ConfirmationModal";
import Button from "./ui/Button";
import { ButtonBase } from "./ui/nativeBases";
import { deleteNotesOutput, exportContent, generateNotes, listNotesOutputs, updateNotesOutput } from "@/lib/api";
import { getErrorMessage } from "@/lib/errors";
import type { NotesOutput } from "@/types";

/** Extra sets never enter the primary notes editor's history or save path. */
export default function NotesOutputsView(props: NotesViewProps & { onPrimarySelected?: (primary: boolean) => void }) {
  const [outputs, setOutputs] = useState<NotesOutput[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const selected = outputs.find((output) => output.id === selectedId);
  const generating = props.isGenerating || busy || outputs.some((output) => output.notes_status === "generating");
  const primarySelected = !selected;
  const onPrimarySelected = props.onPrimarySelected;
  useEffect(() => { onPrimarySelected?.(primarySelected); }, [onPrimarySelected, primarySelected]);

  useEffect(() => {
    let cancelled = false;
    const refresh = async () => {
      try {
        const result = await listNotesOutputs(props.recordingId);
        if (!cancelled) setOutputs(result);
      } catch (cause) {
        if (!cancelled) setError(getErrorMessage(cause, "Could not load notes outputs."));
      }
    };
    void refresh();
    const timer = setInterval(() => void refresh(), 3000);
    return () => { cancelled = true; clearInterval(timer); };
  }, [props.recordingId]);

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try { await action(); }
    catch (cause) { setError(getErrorMessage(cause, "Could not update notes output.")); }
    finally { setBusy(false); }
  };

  const generate = (templateId: number | null, create: boolean) => run(async () => {
    const response = await generateNotes(props.recordingId, templateId,
      create ? { create_output: true } : { output_id: selected?.id });
    if (response.output) {
      const output = response.output;
      setOutputs((previous) => [...previous.filter((item) => item.id !== output.id), output].sort((a, b) => a.id - b.id));
      setSelectedId(output.id);
    }
  });

  const save = async (outputId: number, notes: string) => {
    try {
      const output = await updateNotesOutput(props.recordingId, outputId, notes);
      setOutputs((previous) => previous.map((item) => item.id === output.id ? output : item));
    } catch (cause) { setError(getErrorMessage(cause, "Could not save notes output.")); }
  };

  const remove = () => run(async () => {
    if (!selected) return;
    await deleteNotesOutput(props.recordingId, selected.id);
    setOutputs((previous) => previous.filter((item) => item.id !== selected.id));
    setSelectedId(null);
  });

  const toolbarActions = <>
    {props.notes && <NotesTemplatePicker label="Generate another set" disabled={generating}
      onSelect={(templateId) => void generate(templateId, true)} />}
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
        onClick={() => setSelectedId(output.id)} className="rounded px-3 py-2 text-sm text-foreground aria-selected:bg-surface-inset">{output.name}</ButtonBase>)}
    </div>}
    {(error || selected?.error_message) && <p role="alert" className="p-3 text-sm text-status-danger-fg">{error || selected?.error_message}</p>}
    <div className="min-h-0 flex-1" role={outputs.length ? "tabpanel" : undefined}
      id="notes-output-panel" aria-labelledby={outputs.length ? selected ? `notes-output-${selected.id}` : "notes-output-primary" : undefined}>
      {selected ? <NotesView {...props} key={selected.id} notes={selected.notes} localHistory fixedTemplate
        activeTemplateId={selected.notes_template_id} isGenerating={busy || props.isGenerating || selected.notes_status === "generating"}
        onNotesChange={(notes) => void save(selected.id, notes)}
        onGenerateNotes={() => generate(null, false)}
        onFindAndReplace={async (find, replacement, options) => {
          const pattern = options?.useRegex ? find : find.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
          await save(selected.id, (selected.notes || "").replace(new RegExp(pattern, options?.caseSensitive ? "g" : "gi"), replacement));
        }}
        onExport={() => void run(() => exportContent(props.recordingId, ["notes"], "txt", {}, true, selected.id))}
        toolbarActions={toolbarActions} />
        : <NotesView {...props} key="primary" toolbarActions={toolbarActions} isGenerating={busy || props.isGenerating} />}
    </div>
    <ConfirmationModal isOpen={confirmDelete} onClose={() => setConfirmDelete(false)} onConfirm={() => void remove()}
      title="Delete notes set?" message={`Delete “${selected?.name || "this set"}”? The meeting's first notes will be kept.`}
      confirmText="Delete set" isDangerous />
  </div>;
}
