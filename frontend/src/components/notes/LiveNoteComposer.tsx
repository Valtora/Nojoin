"use client";

import { TextareaBase } from "@/components/ui/nativeBases";
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
  Clock,
  Paperclip,
  SendHorizontal,
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
  type OutboxDraft,
  type OutboxNotice,
} from "@/lib/notes/noteOutbox";
import { fileForNote, transferredFile } from "@/lib/captures/frameImage";
import { formatTimelineMs } from "@/lib/notes/placement";
import { useKeyboardInset } from "@/lib/useKeyboardInset";
import { useOnDepsChange } from "@/lib/useOnDepsChange";

import TranscriptActionsMenu, { type TranscriptMenuItem } from "../transcript/TranscriptActionsMenu";
import IconButton from "../ui/IconButton";

const WHOLE_MEETING = {
  head: { label: "Before the transcript", icon: ArrowUpToLine },
  tail: { label: "After the transcript", icon: ArrowDownToLine },
} as const satisfies Record<"head" | "tail", { label: string; icon: LucideIcon }>;

const NEW_LINE_HINT = "Shift+Enter starts a new line.";

/** What the chip over the box says, and how its button undoes it. */
interface PlacementChip {
  testId: string;
  icon: LucideIcon;
  text: string;
  removeLabel: string;
}

interface LiveNoteComposerProps {
  /** Active and paused captures retain the timeline placement choices. */
  captureActive?: boolean;
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

/**
 * The chip over the box, when the next note goes somewhere other than the
 * usual place: where typing starts during capture, before the transcript
 * after it. Null when it goes there, which needs no saying (issue #959).
 */
const placementChip = (draft: ComposerDraft, captureActive: boolean): PlacementChip | null => {
  const { selection } = draft;
  if (selection) {
    return {
      testId: "note-selection-chip",
      icon: TextQuote,
      text: `${formatTimelineMs(selection.startMs)} “${noteSnippet(selection.quotedText, 80)}”`,
      removeLabel: "Don't put the note on this text",
    };
  }
  if (draft.mode === "tail" || (draft.mode === "head" && captureActive)) {
    const { label, icon } = WHOLE_MEETING[draft.mode];
    return { testId: "note-placement-chip", icon, text: label, removeLabel: `Don't put the note ${label.toLowerCase()}` };
  }
  // A time a line's menu set during capture, or one a draft carried through
  // Stop: the note goes there rather than where it would otherwise.
  if (draft.mode === "typing" && draft.typingStartMs !== null && (draft.typingStartFromLine || !captureActive)) {
    const at = formatTimelineMs(draft.typingStartMs);
    return { testId: "note-placement-chip", icon: Clock, text: `At ${at}`, removeLabel: `Don't put the note at ${at}` };
  }
  return null;
};

/**
 * Where the next note goes, as a phrase for a screen reader: said when a
 * choice or a chip's button moves it, since the chip itself is not a live
 * region.
 */
const placePhrase = (draft: ComposerDraft, captureActive: boolean, empty: boolean): string => {
  if (draft.selection) return `on the text at ${formatTimelineMs(draft.selection.startMs)}`;
  if (draft.mode === "head") return "before the transcript";
  if (draft.mode === "tail") return "after the transcript";
  if (draft.typingStartMs !== null && (draft.typingStartFromLine || !captureActive)) {
    return `at ${formatTimelineMs(draft.typingStartMs)}`;
  }
  if (!captureActive) return "before the transcript";
  return empty ? "where you start typing" : "where you started typing";
};

/**
 * The chat-style note box under the live transcript.
 *
 * Enter adds the note and Shift+Enter starts a new line. An Enter that
 * finishes an input method's composition (Japanese, Chinese, Korean...)
 * belongs to the composition and never sends.
 *
 * During capture a note is anchored where the recording was when the first
 * character went into the empty box, so a note takes as long to write as it
 * likes and still lands beside what prompted it. Emptying the box drops that
 * anchor; the next character takes a new one. After capture there is no
 * moving clock, and a note is on the whole meeting, before the transcript.
 *
 * Anything else is chosen where it applies and shown in a chip over the box,
 * whose ✕ goes back to the usual place (issue #959). The ⋯ beside the box
 * puts a note on the whole meeting, before or after the transcript, shown
 * there and in Your notes: for the next note during capture, and until changed
 * after it. "Note on this line", from a line's ⋯ menu, puts the next note on
 * that line's text, quoted in the chip until the note is sent or the quote
 * removed, when the place before it comes back. On a line still settling it
 * instead anchors the next note at the line's start, which typing keeps.
 *
 * A file pasted or dropped into the box goes with the note being typed, whose
 * text captions it; the note saves first, then the file uploads. The camera
 * button, where this tab shares a screen, makes a note of its own.
 *
 * One row, so it costs the transcript window as little height as it can. The
 * status line under it is empty until there is news: a polite live region,
 * so a refusal, a note added and the outbox's news are read out.
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
    !sourceDraft.selection && sourceDraft.mode === "typing"
    ? { ...sourceDraft, mode: "head" as const }
    : sourceDraft;
  const setDraft = onDraftChange ?? setOwnDraft;
  const [message, setMessage] = useState<Message | null>(null);
  // Said in the live region but not shown: where a moved placement now puts
  // the next note. The chip shows it on screen.
  const [announcement, setAnnouncement] = useState("");
  const [dismissedNoticeSeq, setDismissedNoticeSeq] = useState(notice?.seq ?? 0);
  const ownBoxRef = useRef<HTMLTextAreaElement>(null);
  const box = boxRef ?? ownBoxRef;
  const keyboardInset = useKeyboardInset();
  const chipId = useId();
  const statusId = useId();
  const hintId = useId();
  const chip = placementChip(draft, captureActive);

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

  const say = (text: string, tone: Message["tone"]) => {
    setMessage({ text, tone, noticeSeq: latestNoticeSeq });
    setAnnouncement("");
  };

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
    setAnnouncement("");
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
    // During capture the next note goes where typing starts again; after it,
    // a blank draft reads as Before (above) and After holds until changed.
    setDraft({ text: "", mode: captureActive ? "typing" : draft.mode, typingStartMs: null });
    const place = notePlace(note);
    say(
      note.kind === "head" || note.kind === "tail"
        ? `Note added ${place}, and in Your notes.`
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

  const status = showNotice ? { text: notice.text, tone: notice.tone } : message;

  // Applies a moved placement and says where the next note now goes.
  const movePlacement = (next: ComposerDraft) => {
    setDraft(next);
    setMessage(null);
    setDismissedNoticeSeq(latestNoticeSeq);
    setAnnouncement(`The next note goes ${placePhrase(next, captureActive, empty)}.`);
    box.current?.focus();
  };

  // Clears the chip's placement: the note goes to the usual place again. A
  // time a line set gives way to where typing started, now if it has.
  const removePlacement = () => {
    if (draft.selection) {
      movePlacement({ ...draft, selection: null });
    } else if (!captureActive) {
      movePlacement({ ...draft, mode: "head", typingStartMs: null, typingStartFromLine: undefined });
    } else if (draft.mode === "typing") {
      movePlacement({ ...draft, typingStartMs: empty ? null : readAudioNow(), typingStartFromLine: undefined });
    } else {
      movePlacement({ ...draft, mode: "typing" });
    }
  };

  // A note on the whole meeting, from the ⋯ beside the box. It replaces a
  // line or text chosen for the note, as any other place does.
  const wholeMeetingItems: TranscriptMenuItem[] = (["head", "tail"] as const).map((mode) => ({
    id: mode,
    label: WHOLE_MEETING[mode].label,
    icon: WHOLE_MEETING[mode].icon,
    onSelect: () => movePlacement({
      ...draft,
      mode,
      selection: null,
      ...(draft.typingStartFromLine
        ? { typingStartMs: empty ? null : readAudioNow(), typingStartFromLine: undefined }
        : {}),
    }),
  }));
  const ChipIcon = chip?.icon;

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
      {chip && ChipIcon ? (
        <div
          id={chipId}
          data-testid={chip.testId}
          className="mb-1.5 flex items-center gap-2 rounded-lg bg-action-tint py-0.5 pl-2 text-xs text-action-tint-fg"
        >
          <ChipIcon aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />
          <span className="min-w-0 flex-1 truncate">{chip.text}</span>
          <IconButton
            size="sm"
            aria-label={chip.removeLabel}
            title={chip.removeLabel}
            onClick={removePlacement}
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
              setAnnouncement("");
              box.current?.focus();
            }}
            className="h-7 w-7 text-action-tint-fg hover:bg-action-tint-hover hover:text-action-tint-fg pointer-coarse:h-[44px] pointer-coarse:w-[44px] pointer-coarse:-my-2"
            icon={<X aria-hidden="true" />}
          />
        </div>
      ) : null}
      <div className="flex items-end gap-1.5">
        <TranscriptActionsMenu
          label="Note on the whole meeting"
          title="Note on the whole meeting"
          reveal="always"
          items={wholeMeetingItems}
          triggerClassName="h-[44px] w-[44px] shrink-0"
        />
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
          aria-describedby={[chip ? chipId : null, statusId, hintId].filter(Boolean).join(" ")}
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
          title={`Add note (Enter). ${NEW_LINE_HINT}`}
          disabled={empty}
          icon={<SendHorizontal aria-hidden="true" />}
          // 44 px in every density, level with the box beside it.
          className="h-[44px] w-[44px]"
        />
      </div>
      <p
        id={statusId}
        aria-live="polite"
        data-testid="live-note-status"
        className={status
          ? `mt-1 text-xs ${status.tone === "error" ? "text-status-danger-fg" : "text-contrast-helper"}`
          : "sr-only"}
      >
        {status?.text ?? announcement}
      </p>
      {/* Enter and Shift+Enter work as in any chat box, so the hint is the
          box's description and the Send button's tooltip, not a line. */}
      <p id={hintId} className="sr-only">{NEW_LINE_HINT}</p>
    </form>
  );
}
