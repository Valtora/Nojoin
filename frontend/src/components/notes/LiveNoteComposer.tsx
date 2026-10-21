"use client";

import {
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type ChangeEvent,
  type FormEvent,
  type KeyboardEvent,
} from "react";
import { SendHorizontal } from "lucide-react";

import { stripNote } from "@/lib/notes/headNoteSync";
import type { OutboxDraft, OutboxEntry } from "@/lib/notes/noteOutbox";
import { formatTimelineMs } from "@/lib/notes/placement";
import { useKeyboardInset } from "@/lib/useKeyboardInset";

import UnsavedNotesList from "./UnsavedNotesList";

/** Where a note goes: the four insert modes of the live note box. */
export type NoteInsertMode = "typing" | "send" | "head" | "tail";

const MODE_LABELS: Record<NoteInsertMode, string> = {
  typing: "At typing start",
  send: "At send",
  head: "Before the transcript",
  tail: "After the transcript",
};

/** The tallest the box grows before it scrolls, in px. */
const MAX_BOX_HEIGHT_PX = 160;

interface LiveNoteComposerProps {
  /** Where capture is on the audio timeline now, in ms. */
  readAudioNow: () => number;
  /** Queues a note; returns why it was refused, or null once it is on its way. */
  onSubmit: (draft: OutboxDraft) => string | null;
  /** Head and tail notes still on their way, which have no line to sit after. */
  pending?: readonly OutboxEntry[];
  onRetry?: (key: string) => void;
  onDiscard?: (key: string) => void;
}

const isBlank = (text: string) => stripNote(text) === "";

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
 * the whole meeting, listed with the Notes box rather than among the lines.
 */
export default function LiveNoteComposer({
  readAudioNow,
  onSubmit,
  pending = [],
  onRetry,
  onDiscard,
}: LiveNoteComposerProps) {
  const [draft, setDraft] = useState("");
  const [mode, setMode] = useState<NoteInsertMode>("typing");
  const [typingStartMs, setTypingStartMs] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const boxRef = useRef<HTMLTextAreaElement>(null);
  const keyboardInset = useKeyboardInset();
  const modeId = useId();
  const hintId = useId();

  // Grows with its text, chat-style, up to a cap.
  useLayoutEffect(() => {
    const box = boxRef.current;
    if (!box) return;
    box.style.height = "auto";
    box.style.height = `${Math.min(box.scrollHeight, MAX_BOX_HEIGHT_PX)}px`;
  }, [draft]);

  const handleChange = (event: ChangeEvent<HTMLTextAreaElement>) => {
    const next = event.target.value;
    if (isBlank(next)) {
      setTypingStartMs(null);
    } else if (isBlank(draft) || typingStartMs === null) {
      setTypingStartMs(readAudioNow());
    }
    setDraft(next);
    if (error) setError(null);
  };

  const submit = () => {
    if (isBlank(draft)) return;
    const kind = mode === "head" || mode === "tail" ? mode : "point";
    const anchorMs =
      mode === "typing"
        ? (typingStartMs ?? readAudioNow())
        : mode === "send"
          ? readAudioNow()
          : null;
    const refusal = onSubmit({ kind, body: draft, anchorMs });
    if (refusal) {
      setError(refusal);
      return;
    }
    setDraft("");
    setTypingStartMs(null);
    setError(null);
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

  const hint =
    error ??
    (mode === "typing"
      ? typingStartMs !== null
        ? `Placed at ${formatTimelineMs(typingStartMs)}, where you started typing.`
        : "Placed where you start typing. Shift+Enter starts a new line."
      : mode === "send"
        ? "Placed where the recording is when you press Enter."
        : mode === "head"
          ? "For the whole meeting, before the transcript."
          : "For the whole meeting, after the transcript.");

  return (
    <form
      onSubmit={handleSubmit}
      aria-label="Add a note"
      data-testid="live-note-composer"
      // Sticky against the page while the layout is stacked and the page
      // scrolls, offset above an on-screen keyboard that overlays the page.
      // In the two-column layout the card has a definite height and this
      // sits at its bottom without moving.
      className="sticky z-[var(--z-sticky)] mt-3 shrink-0 bg-surface-card pt-1"
      style={{ bottom: `calc(${keyboardInset}px + env(safe-area-inset-bottom, 0px))` }}
    >
      {pending.length > 0 && onRetry && onDiscard ? (
        <div className="mb-2">
          <UnsavedNotesList entries={pending} onRetry={onRetry} onDiscard={onDiscard} />
        </div>
      ) : null}

      <div className="flex items-end gap-1.5">
        <textarea
          ref={boxRef}
          rows={1}
          value={draft}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          aria-label="New note"
          aria-describedby={hintId}
          aria-invalid={error ? true : undefined}
          placeholder="Add a note…"
          className="density-surface-panel min-h-[44px] w-full flex-1 resize-none border border-control-border bg-control-bg px-3 py-2.5 text-sm leading-5 text-contrast-muted outline-none transition focus:border-action focus:ring-2 focus:ring-action"
        />
        <button
          type="submit"
          aria-label="Add note"
          title="Add note"
          disabled={isBlank(draft)}
          className="inline-flex h-[44px] w-[44px] shrink-0 items-center justify-center rounded-lg bg-action text-action-on transition-colors hover:bg-action-hover disabled:cursor-not-allowed disabled:opacity-60"
        >
          <SendHorizontal className="h-5 w-5" aria-hidden="true" />
        </button>
      </div>
      <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1">
        <label htmlFor={modeId} className="sr-only">
          Place note
        </label>
        <select
          id={modeId}
          value={mode}
          onChange={(event) => setMode(event.target.value as NoteInsertMode)}
          className="h-[44px] shrink-0 rounded-lg border border-control-border bg-control-bg px-1.5 text-sm text-contrast-muted outline-none focus:border-action focus:ring-2 focus:ring-action"
        >
          {(Object.keys(MODE_LABELS) as NoteInsertMode[]).map((value) => (
            <option key={value} value={value}>
              {MODE_LABELS[value]}
            </option>
          ))}
        </select>
        <p
          id={hintId}
          role={error ? "alert" : undefined}
          className={`min-w-0 flex-1 basis-40 text-xs ${error ? "text-status-danger-fg" : "text-contrast-helper"}`}
        >
          {hint}
        </p>
      </div>
    </form>
  );
}
