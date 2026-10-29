"use client";

import { SelectBase, TextareaBase } from "@/components/ui/nativeBases";
import {
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type ChangeEvent,
  type ClipboardEvent,
  type DragEvent,
  type FormEvent,
  type KeyboardEvent,
  type RefObject,
} from "react";
import {
  ArrowDownToLine,
  ArrowUpToLine,
  Camera,
  ChevronDown,
  CornerDownLeft,
  Paperclip,
  SendHorizontal,
  TextCursorInput,
  TextQuote,
  X,
  type LucideIcon,
} from "lucide-react";

import { stripNote } from "@/lib/notes/headNoteSync";
import {
  EMPTY_DRAFT,
  notePlace,
  noteFromDraft,
  noteSnippet,
  type ComposerDraft,
  type NoteInsertMode,
  type OutboxDraft,
  type OutboxNotice,
} from "@/lib/notes/noteOutbox";
import { fileForNote, transferredFile } from "@/lib/captures/frameImage";
import { formatTimelineMs } from "@/lib/notes/placement";
import { useKeyboardInset } from "@/lib/useKeyboardInset";
import { useOnDepsChange } from "@/lib/useOnDepsChange";

import IconButton from "../ui/IconButton";

export type { NoteInsertMode };

export const MODE_LABELS: Record<NoteInsertMode, string> = {
  typing: "At typing start",
  send: "At send",
  head: "Before the transcript",
  tail: "After the transcript",
};

const MODE_ICONS: Record<NoteInsertMode, LucideIcon> = {
  typing: TextCursorInput,
  send: CornerDownLeft,
  head: ArrowUpToLine,
  tail: ArrowDownToLine,
};

/** The menu's entry while the next note is on chosen text (`draft.selection`). */
const ON_SELECTION = "selection";
const ON_SELECTION_LABEL = "On the chosen text";

interface LiveNoteComposerProps {
  /** Active and paused captures retain the timeline placement choices. */
  captureActive?: boolean;
  /** The recording has finished processing, so a new note waits for the next notes run. */
  finished?: boolean;
  /** Where capture is on the audio timeline now, in ms. */
  readAudioNow: () => number;
  /** Queues a note; returns why it was refused, or null once it is on its way. */
  onSubmit: (note: OutboxDraft) => string | null;
  /** The box's text, mode and anchor, kept by the caller so they outlive the box. */
  draft?: ComposerDraft;
  onDraftChange?: (draft: ComposerDraft) => void;
  /** The latest news from the outbox, said in the box's status line. */
  notice?: OutboxNotice | null;
  /** The text box, for a caller that moves focus to it. */
  boxRef?: RefObject<HTMLTextAreaElement | null>;
  /** Takes a still of the shared screen; without it, no Capture button. */
  onCapture?: () => void;
  /** A capture is being taken, so the button waits. */
  capturing?: boolean;
  /** The keys that capture too, for the button's tooltip. */
  captureShortcut?: string;
  /** The file pasted or dropped into the box, which the next note takes. */
  draftFile?: File | null;
  /** Holds or clears that file; without it, a file pasted or dropped does nothing. */
  onDraftFileChange?: (file: File | null) => void;
  /** The latest word on a capture, said in the status line; a new `seq` says it again. */
  captureMessage?: { text: string; tone: "info" | "error"; seq: number } | null;
}

interface Message {
  text: string;
  tone: "error" | "info";
  /** The outbox notice current when this was said: a later one replaces it. */
  noticeSeq: number;
}

const isBlank = (text: string) => stripNote(text) === "";

const modeHint = (draft: ComposerDraft, hasFile: boolean) => {
  if (draft.selection) {
    return `On the text at ${formatTimelineMs(draft.selection.startMs)}, which the note quotes.`;
  }
  switch (draft.mode) {
    case "typing":
      if (draft.typingStartMs === null) return "Placed where you start typing. Shift+Enter starts a new line.";
      return (isBlank(draft.text) && !hasFile) || draft.typingStartFromLine
        ? `Placed at ${formatTimelineMs(draft.typingStartMs)}.`
        : `Placed at ${formatTimelineMs(draft.typingStartMs)}, where you started typing.`;
    case "send":
      return "Placed where the recording is when you press Enter.";
    case "head":
      return "For the whole meeting, before the transcript. Also listed in My notes.";
    case "tail":
      return "For the whole meeting, after the transcript. Also listed in My notes.";
  }
};

/**
 * The chat-style note box under the live transcript.
 *
 * Enter adds the note and Shift+Enter starts a new line. An Enter that
 * finishes an input method's composition (Japanese, Chinese, Korean...)
 * belongs to the composition and never sends.
 *
 * "At typing start", the default, anchors the note where the recording was
 * when the first character went into the empty box, so a note takes as long
 * to write as it likes and still lands beside what prompted it. Emptying the
 * box drops that anchor; the next character takes a new one. "At send"
 * anchors it on Enter. "Before" and "After the transcript" make a note on
 * the whole meeting, shown before or after its lines and in My notes.
 * "Note on this line", from a line's ⋯ menu, puts the next note on that
 * line's text: the quote shows above the box and the mode reads "On the
 * chosen text" until the note is sent or the quote removed, when the mode
 * before it comes back. On a line still settling it instead sets an
 * "At typing start" anchor at the line's start, which the first character keeps.
 *
 * A file pasted or dropped into the box goes with the note being typed, whose
 * text captions it; the note saves first, then the file uploads. The camera
 * button, where this tab shares a screen, makes a note of its own.
 *
 * One row, so it costs the transcript window as little height as it can: the
 * mode is an icon-sized select (its options say the modes in full), and the
 * status line under it says where the note will go. That line is a polite
 * live region, so a refusal, a note added and the outbox's news are read out.
 */
export default function LiveNoteComposer({
  captureActive = true,
  readAudioNow,
  onSubmit,
  draft: controlledDraft,
  onDraftChange,
  notice = null,
  boxRef,
  onCapture,
  capturing = false,
  finished = false,
  captureShortcut,
  draftFile = null,
  onDraftFileChange,
  captureMessage = null,
}: LiveNoteComposerProps) {
  const [ownDraft, setOwnDraft] = useState<ComposerDraft>(EMPTY_DRAFT);
  const sourceDraft = controlledDraft ?? ownDraft;
  // A carried draft keeps its placement. Only a new, blank finished draft
  // defaults to Before; an explicit After choice persists between notes.
  const draft = !captureActive && isBlank(sourceDraft.text) && !draftFile &&
    !sourceDraft.selection &&
    (sourceDraft.mode === "typing" || sourceDraft.mode === "send")
    ? { ...sourceDraft, mode: "head" as const }
    : sourceDraft;
  const setDraft = onDraftChange ?? setOwnDraft;
  const [message, setMessage] = useState<Message | null>(null);
  const [dismissedNoticeSeq, setDismissedNoticeSeq] = useState(notice?.seq ?? 0);
  const ownBoxRef = useRef<HTMLTextAreaElement>(null);
  const box = boxRef ?? ownBoxRef;
  const keyboardInset = useKeyboardInset();
  const modeId = useId();
  const hintId = useId();
  const { selection } = draft;
  const ModeIcon = selection ? TextQuote : MODE_ICONS[draft.mode];

  // Grows with its text, chat-style, up to the cap its classes set.
  useLayoutEffect(() => {
    const element = box.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${element.scrollHeight}px`;
  }, [box, draft.text]);

  const latestNoticeSeq = notice?.seq ?? 0;
  const showNotice =
    notice !== null &&
    notice.seq > Math.max(dismissedNoticeSeq, message?.noticeSeq ?? 0);

  const say = (text: string, tone: Message["tone"]) =>
    setMessage({ text, tone, noticeSeq: latestNoticeSeq });

  // A capture's news replaces the line, as a note's does.
  useOnDepsChange([captureMessage?.seq], () => {
    if (captureMessage) {
      setMessage({ text: captureMessage.text, tone: captureMessage.tone, noticeSeq: latestNoticeSeq });
    }
  });

  // A file pasted or dropped into the box goes with the note being typed, and
  // starts it as a first character would; text, even text that came with a
  // picture, pastes as text.
  const attach = async (file: File | null) => {
    if (!file || !onDraftFileChange) return;
    // Anchored now, before an image is redrawn, while the draft is current.
    if (draft.typingStartMs === null) setDraft({ ...draft, typingStartMs: readAudioNow() });
    const usable = await fileForNote(file);
    onDraftFileChange(usable);
    say(`${usable.name} goes with this note. Add text to caption it, then Enter.`, "info");
  };

  const handlePaste = (event: ClipboardEvent<HTMLTextAreaElement>) => {
    const file = onDraftFileChange ? transferredFile(event.clipboardData) : null;
    if (!file) return;
    event.preventDefault();
    void attach(file);
  };

  const handleDrop = (event: DragEvent<HTMLFormElement>) => {
    const file = onDraftFileChange ? transferredFile(event.dataTransfer) : null;
    if (!file) return;
    event.preventDefault();
    void attach(file);
  };

  const handleChange = (event: ChangeEvent<HTMLTextAreaElement>) => {
    const text = event.target.value;
    // With a file in the box the note has begun, so its anchor holds, as does
    // one a line's menu set; clearing the box otherwise starts again.
    const typingStartMs = isBlank(text)
      ? draftFile
        ? draft.typingStartMs
        : null
      : (draft.typingStartMs ?? readAudioNow());
    const typingStartFromLine = (typingStartMs !== null && draft.typingStartFromLine) || undefined;
    setDraft({ ...draft, text, typingStartMs, typingStartFromLine });
    setMessage(null);
    setDismissedNoticeSeq(latestNoticeSeq);
  };

  const empty = isBlank(draft.text) && !draftFile;

  const submit = () => {
    if (empty) return;
    const note = noteFromDraft(draft, readAudioNow);
    const refusal = onSubmit(note);
    if (refusal) {
      say(refusal, "error");
      return;
    }
    // A blank finished draft reads as Before again (above).
    setDraft({ text: "", mode: draft.mode, typingStartMs: null });
    const place = notePlace(note);
    say(
      note.kind === "head" || note.kind === "tail"
        ? `Note added ${place}, and in My notes.`
        : `Note added ${place}.`,
      "info",
    );
    // A click on Send leaves focus on a button that is now disabled.
    box.current?.focus();
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== "Enter" || event.shiftKey) return;
    // keyCode 229 is the Enter that Safari reports after the composition it
    // ended, with isComposing already false.
    if (event.nativeEvent.isComposing || event.keyCode === 229) return;
    event.preventDefault();
    submit();
  };

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    submit();
  };

  const status = showNotice
    ? { text: notice.text, tone: notice.tone }
    : (message ?? { text: !captureActive && draft.mode === "send" && !selection
      ? "At send places this note where capture stopped, at the end of the recording."
      : modeHint(draft, draftFile !== null), tone: "info" as const });

  return (
    <form
      data-ui-fill="card" data-ui="surface" data-ui-surface="pane"
      onSubmit={handleSubmit}
      onDragOver={(event) => {
        if (onDraftFileChange && event.dataTransfer.types.includes("Files")) event.preventDefault();
      }}
      onDrop={handleDrop}
      aria-label="Add a note"
      data-testid="live-note-composer"
      // Sticky against the page while the layout is stacked and the page
      // scrolls, offset above an on-screen keyboard that overlays the page.
      // In the two-column layout the card clips and has a definite height,
      // and this sits at its bottom without moving.
      className="sticky z-[var(--z-sticky)] mt-2 shrink-0 bg-surface-card pt-1"
      style={{ bottom: `calc(${keyboardInset}px + env(safe-area-inset-bottom, 0px))` }}
    >
      {selection ? (
        <div
          data-testid="note-selection-chip"
          className="mb-1.5 flex items-center gap-2 rounded-lg bg-action-tint py-0.5 pl-2 text-xs text-action-tint-fg"
        >
          <TextQuote aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />
          <span className="min-w-0 flex-1 truncate">
            {formatTimelineMs(selection.startMs)} “{noteSnippet(selection.quotedText, 80)}”
          </span>
          <IconButton
            size="sm"
            aria-label="Don't put the note on this text"
            title="Don't put the note on this text"
            onClick={() => {
              setDraft({ ...draft, selection: null });
              setMessage(null);
              box.current?.focus();
            }}
            className="h-7 w-7 text-action-tint-fg hover:bg-action-tint-hover hover:text-action-tint-fg pointer-coarse:h-[44px] pointer-coarse:w-[44px] pointer-coarse:-my-2"
            icon={<X aria-hidden="true" />}
          />
        </div>
      ) : null}
      {draftFile && onDraftFileChange ? (
        <div
          data-testid="note-draft-file"
          className="mb-1.5 flex items-center gap-2 rounded-lg bg-action-tint py-0.5 pl-2 text-xs text-action-tint-fg"
        >
          <Paperclip aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />
          <span className="min-w-0 flex-1 truncate">{draftFile.name}</span>
          <IconButton
            size="sm"
            aria-label={`Don't attach ${draftFile.name}`}
            title="Don't attach this file"
            onClick={() => {
              onDraftFileChange(null);
              if (isBlank(draft.text) && !draft.typingStartFromLine) setDraft({ ...draft, typingStartMs: null });
              setMessage(null);
              box.current?.focus();
            }}
            className="h-7 w-7 text-action-tint-fg hover:bg-action-tint-hover hover:text-action-tint-fg pointer-coarse:h-[44px] pointer-coarse:w-[44px] pointer-coarse:-my-2"
            icon={<X aria-hidden="true" />}
          />
        </div>
      ) : null}
      <div className="flex items-end gap-1.5">
        <label htmlFor={modeId} className="sr-only">
          Place note
        </label>
        <div className="relative h-[44px] w-[52px] shrink-0">
          <SelectBase
            data-ui-fill="control"
            data-ui-border="full"
            id={modeId}
            value={selection ? ON_SELECTION : draft.mode}
            title={selection ? ON_SELECTION_LABEL : MODE_LABELS[draft.mode]}
            onChange={(event) => {
              const { value } = event.target;
              if (value === ON_SELECTION) return;
              setDraft({ ...draft, mode: value as NoteInsertMode, selection: null });
              setMessage(null);
            }}
            className="absolute inset-0 h-full w-full cursor-pointer appearance-none rounded-lg border border-control-border bg-control-bg text-transparent outline-none focus:border-action focus:ring-2 focus:ring-action [&>option]:text-foreground"
          >
            {selection ? <option value={ON_SELECTION}>{ON_SELECTION_LABEL}</option> : null}
            {(Object.keys(MODE_LABELS) as NoteInsertMode[])
              .filter((value) => captureActive || value === "head" || value === "tail" || value === draft.mode)
              .map((value) => (
              <option key={value} value={value}>
                {MODE_LABELS[value]}
              </option>
            ))}
          </SelectBase>
          <span
            aria-hidden="true"
            className="pointer-events-none absolute inset-0 flex items-center justify-center gap-0.5 text-contrast-muted"
          >
            <ModeIcon className="h-4 w-4" />
            <ChevronDown className="h-3 w-3" />
          </span>
        </div>
        <TextareaBase
          data-ui-fill="control"
          data-ui-border="full"
          ref={box}
          rows={1}
          value={draft.text}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          onPaste={handlePaste}
          aria-label="New note"
          aria-describedby={hintId}
          aria-invalid={message?.tone === "error" && !showNotice ? true : undefined}
          placeholder="Add a note…"
          className="density-surface-panel max-h-[160px] min-h-[44px] w-full min-w-0 flex-1 resize-none overflow-y-auto border border-control-border bg-control-bg px-3 py-2.5 text-sm leading-5 text-contrast-muted outline-none transition focus:border-action focus:ring-2 focus:ring-action @min-[54rem]:max-h-[80px]"
        />
        {onCapture ? (
          <IconButton
            variant="secondary"
            aria-label="Capture the shared screen"
            title={
              captureShortcut
                ? `Capture the shared screen (${captureShortcut})`
                : "Capture the shared screen"
            }
            aria-keyshortcuts={captureShortcut ? "Alt+Shift+C" : undefined}
            loading={capturing}
            onClick={onCapture}
            icon={<Camera aria-hidden="true" />}
            data-testid="capture-button"
            className="h-[44px] w-[44px] shrink-0"
          />
        ) : null}
        <IconButton
          type="submit"
          variant="primary"
          aria-label="Add note"
          title="Add note (Enter)"
          disabled={empty}
          icon={<SendHorizontal aria-hidden="true" />}
          // 44 px in every density, level with the box beside it.
          className="h-[44px] w-[44px]"
        />
      </div>
      <p
        id={hintId}
        aria-live="polite"
        data-testid="live-note-status"
        className={`mt-1 text-xs ${status.tone === "error" ? "text-status-danger-fg" : "text-contrast-helper"}`}
      >
        {status.text}
      </p>
      <p className="mt-1 text-xs text-contrast-helper">
        {finished
          ? "New notes are included, labelled as yours, the next time meeting notes are generated."
          : "Your notes go into the meeting notes when they are generated, labelled as yours."}
      </p>
    </form>
  );
}
