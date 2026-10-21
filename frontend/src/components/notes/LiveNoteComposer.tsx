"use client";

import {
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type ChangeEvent,
  type FormEvent,
  type KeyboardEvent,
  type RefObject,
} from "react";
import {
  ArrowDownToLine,
  ArrowUpToLine,
  ChevronDown,
  CornerDownLeft,
  SendHorizontal,
  TextCursorInput,
  type LucideIcon,
} from "lucide-react";

import { stripNote } from "@/lib/notes/headNoteSync";
import {
  EMPTY_DRAFT,
  notePlace,
  noteFromDraft,
  type ComposerDraft,
  type NoteInsertMode,
  type OutboxDraft,
  type OutboxNotice,
} from "@/lib/notes/noteOutbox";
import { formatTimelineMs } from "@/lib/notes/placement";
import { useKeyboardInset } from "@/lib/useKeyboardInset";

export type { NoteInsertMode };

export const MODE_LABELS: Record<NoteInsertMode, string> = {
  typing: "At typing start",
  send: "At send",
  head: "Before the transcript (the Notes box's text if it has none)",
  tail: "After the transcript",
};

const MODE_ICONS: Record<NoteInsertMode, LucideIcon> = {
  typing: TextCursorInput,
  send: CornerDownLeft,
  head: ArrowUpToLine,
  tail: ArrowDownToLine,
};

interface LiveNoteComposerProps {
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
}

interface Message {
  text: string;
  tone: "error" | "info";
  /** The outbox notice current when this was said: a later one replaces it. */
  noticeSeq: number;
}

const isBlank = (text: string) => stripNote(text) === "";

const modeHint = (draft: ComposerDraft) => {
  switch (draft.mode) {
    case "typing":
      return draft.typingStartMs !== null && !isBlank(draft.text)
        ? `Placed at ${formatTimelineMs(draft.typingStartMs)}, where you started typing.`
        : "Placed where you start typing. Shift+Enter starts a new line.";
    case "send":
      return "Placed where the recording is when you press Enter.";
    case "head":
      return "For the whole meeting. Becomes the Notes box's text if it has none; otherwise listed under Other notes.";
    case "tail":
      return "For the whole meeting, after the transcript, listed under Other notes.";
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
 * the whole meeting, kept with the Notes box rather than among the lines.
 *
 * One row, so it costs the transcript window as little height as it can: the
 * mode is an icon-sized select (its options say the modes in full), and the
 * status line under it says where the note will go. That line is a polite
 * live region, so a refusal, a note added and the outbox's news are read out.
 */
export default function LiveNoteComposer({
  readAudioNow,
  onSubmit,
  draft: controlledDraft,
  onDraftChange,
  notice = null,
  boxRef,
}: LiveNoteComposerProps) {
  const [ownDraft, setOwnDraft] = useState<ComposerDraft>(EMPTY_DRAFT);
  const draft = controlledDraft ?? ownDraft;
  const setDraft = onDraftChange ?? setOwnDraft;
  const [message, setMessage] = useState<Message | null>(null);
  const [dismissedNoticeSeq, setDismissedNoticeSeq] = useState(notice?.seq ?? 0);
  const ownBoxRef = useRef<HTMLTextAreaElement>(null);
  const box = boxRef ?? ownBoxRef;
  const keyboardInset = useKeyboardInset();
  const modeId = useId();
  const hintId = useId();
  const ModeIcon = MODE_ICONS[draft.mode];

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

  const handleChange = (event: ChangeEvent<HTMLTextAreaElement>) => {
    const text = event.target.value;
    const typingStartMs = isBlank(text)
      ? null
      : isBlank(draft.text) || draft.typingStartMs === null
        ? readAudioNow()
        : draft.typingStartMs;
    setDraft({ ...draft, text, typingStartMs });
    setMessage(null);
    setDismissedNoticeSeq(latestNoticeSeq);
  };

  const submit = () => {
    if (isBlank(draft.text)) return;
    const note = noteFromDraft(draft, readAudioNow);
    const refusal = onSubmit(note);
    if (refusal) {
      say(refusal, "error");
      return;
    }
    setDraft({ text: "", mode: draft.mode, typingStartMs: null });
    const place = notePlace({ kind: note.kind, anchorMs: note.anchorMs });
    say(
      note.kind === "point"
        ? `Note added ${place}.`
        : `Note added ${place}, with the Notes box.`,
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
    : (message ?? { text: modeHint(draft), tone: "info" as const });

  return (
    <form
      onSubmit={handleSubmit}
      aria-label="Add a note"
      data-testid="live-note-composer"
      // Sticky against the page while the layout is stacked and the page
      // scrolls, offset above an on-screen keyboard that overlays the page.
      // In the two-column layout the card clips and has a definite height,
      // and this sits at its bottom without moving.
      className="sticky z-[var(--z-sticky)] mt-2 shrink-0 bg-surface-card pt-1"
      style={{ bottom: `calc(${keyboardInset}px + env(safe-area-inset-bottom, 0px))` }}
    >
      <div className="flex items-end gap-1.5">
        <label htmlFor={modeId} className="sr-only">
          Place note
        </label>
        <div className="relative h-[44px] w-[52px] shrink-0">
          <select
            id={modeId}
            value={draft.mode}
            title={MODE_LABELS[draft.mode]}
            onChange={(event) => {
              setDraft({ ...draft, mode: event.target.value as NoteInsertMode });
              setMessage(null);
            }}
            className="absolute inset-0 h-full w-full cursor-pointer appearance-none rounded-lg border border-control-border bg-control-bg text-transparent outline-none focus:border-action focus:ring-2 focus:ring-action [&>option]:text-foreground"
          >
            {(Object.keys(MODE_LABELS) as NoteInsertMode[]).map((value) => (
              <option key={value} value={value}>
                {MODE_LABELS[value]}
              </option>
            ))}
          </select>
          <span
            aria-hidden="true"
            className="pointer-events-none absolute inset-0 flex items-center justify-center gap-0.5 text-contrast-muted"
          >
            <ModeIcon className="h-4 w-4" />
            <ChevronDown className="h-3 w-3" />
          </span>
        </div>
        <textarea
          ref={box}
          rows={1}
          value={draft.text}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          aria-label="New note"
          aria-describedby={hintId}
          aria-invalid={message?.tone === "error" && !showNotice ? true : undefined}
          placeholder="Add a note…"
          className="density-surface-panel max-h-[160px] min-h-[44px] w-full min-w-0 flex-1 resize-none overflow-y-auto border border-control-border bg-control-bg px-3 py-2.5 text-sm leading-5 text-contrast-muted outline-none transition focus:border-action focus:ring-2 focus:ring-action @min-[54rem]:max-h-[80px]"
        />
        <button
          type="submit"
          aria-label="Add note"
          title="Add note (Enter)"
          disabled={isBlank(draft.text)}
          className="inline-flex h-[44px] w-[44px] shrink-0 items-center justify-center rounded-lg bg-action text-action-on transition-colors hover:bg-action-hover disabled:cursor-not-allowed disabled:opacity-60"
        >
          <SendHorizontal className="h-5 w-5" aria-hidden="true" />
        </button>
      </div>
      <p
        id={hintId}
        aria-live="polite"
        data-testid="live-note-status"
        className={`mt-1 text-xs ${status.tone === "error" ? "text-status-danger-fg" : "text-contrast-helper"}`}
      >
        {status.text}
      </p>
    </form>
  );
}
