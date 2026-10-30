"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal, flushSync } from "react-dom";
import { getGlobalSpeakers } from "@/lib/api";
import type { GlobalSpeaker, RecordingSpeaker, TranscriptSegment, TranscriptSpeakerAssignment } from "@/types";
import { splitWords } from "@/lib/transcript/settling";
import Button from "../ui/Button";
import { SelectBase, TextareaBase } from "../ui/nativeBases";
import SpeakerAssignmentPopover from "../SpeakerAssignmentPopover";
import { useAnchoredPanel } from "../ui/useAnchoredPanel";
import { usePanelDismissal } from "../ui/usePanelDismissal";

export interface LiveLineHandlers {
  editText: (segment: TranscriptSegment, text: string) => Promise<boolean>;
  changeSpeaker: (segment: TranscriptSegment, assignment: TranscriptSpeakerAssignment) => Promise<boolean>;
  split: (segment: TranscriptSegment, wordId: string) => Promise<boolean>;
}

export type LiveEditKind = "editText" | "changeSpeaker" | "split";

/** An explicit text editor, speaker picker, or genuine-word boundary picker. */
export default function LiveLineEditor({
  kind, segment, anchor, handlers, speakers = [], liveSpeakerLabels, onClose,
}: {
  kind: LiveEditKind;
  segment: TranscriptSegment;
  anchor: HTMLElement;
  handlers: LiveLineHandlers;
  speakers?: readonly RecordingSpeaker[];
  liveSpeakerLabels?: Readonly<Record<string, string>>;
  onClose: () => void;
}) {
  const [text, setText] = useState(segment.text);
  const [people, setPeople] = useState<GlobalSpeaker[]>([]);
  const words = splitWords(segment);
  const [wordId, setWordId] = useState(words[0]?.id ?? "");
  const [saving, setSaving] = useState(false);
  const textEditorRef = useRef<HTMLTextAreaElement>(null);
  const splitPickerRef = useRef<HTMLSelectElement>(null);
  const anchorRef = useMemo(() => ({ current: anchor }), [anchor]);
  const { panelRef, panelStyle } = useAnchoredPanel<HTMLDivElement>(true, anchorRef);
  const placed = panelStyle.visibility === "visible";
  useEffect(() => {
    // Anchored panels mount hidden for measurement; focus only after placement.
    if (kind === "editText" && placed) textEditorRef.current?.focus();
    if (kind === "split" && placed) splitPickerRef.current?.focus();
  }, [kind, placed]);
  useEffect(() => {
    if (kind !== "changeSpeaker") return;
    let current = true;
    void getGlobalSpeakers().then((names) => { if (current) setPeople(names); }).catch(() => {});
    return () => { current = false; };
  }, [kind]);
  // Back to the ⋯ button it opened from; a press outside leaves focus where it landed.
  const close = () => { onClose(); anchor.focus(); };
  // The speaker picker handles its own presses outside. The others let go
  // while nothing typed or chosen would be lost, and never mid-save.
  const unchanged = kind === "split" ? wordId === (words[0]?.id ?? "") : text === segment.text;
  const handlePanelKeyDown = usePanelDismissal({
    panelRef, anchor, unchanged: kind !== "changeSpeaker" && unchanged && !saving,
    dismiss: onClose, close: () => { if (!saving) close(); },
  });
  // A refusal keeps the editor open. Save and the text were disabled while it
  // ran, which dropped focus to the page, so the text is enabled again first
  // and focus goes back to it or the picker.
  const save = async (command: () => Promise<boolean>) => {
    if (saving) return;
    setSaving(true);
    try {
      if (await command()) close();
      else {
        flushSync(() => setSaving(false));
        (kind === "split" ? splitPickerRef : textEditorRef).current?.focus();
      }
    } catch (error) {
      console.error("Failed to save the transcript line", error);
    } finally { setSaving(false); }
  };
  // Text as it opened closes without a request, as in the finished view: the
  // server would replace the line's timed words with one untimed atom.
  const saveText = () => {
    if (text === segment.text) close();
    else void save(() => handlers.editText(segment, text));
  };
  if (kind === "changeSpeaker") return (
    <SpeakerAssignmentPopover
      availableSpeakers={speakers} globalSpeakers={people} currentSpeakerLabel={segment.speaker}
      liveSpeakerLabels={liveSpeakerLabels}
      targetElement={anchor} speakerColors={{}}
      onSelect={(assignment) => void save(() => handlers.changeSpeaker(segment, assignment))} onClose={close}
    />
  );
  return createPortal(
    <div ref={panelRef} style={panelStyle} role="dialog" aria-label={kind === "split" ? "Split line" : "Edit line text"}
      onKeyDown={handlePanelKeyDown}
      className="z-[var(--z-dropdown)] w-80 max-w-[calc(100vw-2rem)] rounded border border-surface-border bg-surface-card p-3 text-foreground shadow-float">
      {kind === "editText" ? (
        <TextareaBase ref={textEditorRef} aria-label="Line text" value={text} disabled={saving}
          onChange={(event) => setText(event.target.value)}
          className="w-full rounded border border-control-border bg-surface-input p-2 text-foreground" />
      ) : (
        <label className="block text-sm">Split before word
          <SelectBase ref={splitPickerRef} aria-label="Split before word" value={wordId}
            onChange={(event) => setWordId(event.target.value)}
            className="ml-2 rounded border border-control-border bg-surface-input p-2 text-foreground">
            {words.map((word) => <option key={word.id} value={word.id}>{word.word}</option>)}
          </SelectBase>
        </label>
      )}
      <div className="mt-2 flex gap-2">
        <Button size="sm" disabled={saving || (kind === "split" && !wordId)} onClick={() =>
          kind === "split" ? void save(() => handlers.split(segment, wordId)) : saveText()}>
          {kind === "split" ? "Split" : "Save"}
        </Button>
        <Button size="sm" variant="ghost" disabled={saving} onClick={close}>Cancel</Button>
      </div>
    </div>, document.body,
  );
}
