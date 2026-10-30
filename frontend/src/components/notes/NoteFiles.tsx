"use client";

import { ButtonBase } from "@/components/ui/nativeBases";
import { SurfaceBase } from "@/components/ui/SurfaceBase";
import {
  createContext,
  useCallback,
  useContext,
  useId,
  useMemo,
  useRef,
  useState,
  type ReactNode,
  type RefObject,
} from "react";
import { flushSync } from "react-dom";
import { Download, FileText, Paperclip, Play, Trash2 } from "lucide-react";

import {
  documentFileUrl,
  documentPreviewUrl,
  downloadDocument,
  removeNoteFile,
  setNoteFileUseInNotes,
} from "@/lib/api";
import { CAPTURE_NOTE_BODY } from "@/lib/captures/useCaptureActions";
import { getErrorStatus } from "@/lib/errors";
import { shownNotes, type NoteOutbox, type NoteUpload, type OutboxSnapshot } from "@/lib/notes/noteOutbox";
import { formatTimelineMs } from "@/lib/notes/placement";
import { selectSavedNotes, useNoteOutbox } from "@/lib/notes/useNoteOutbox";
import { useNotificationStore } from "@/lib/notificationStore";
import { notifyRequestError } from "@/lib/notifyRequestError";
import type { NoteEdits } from "@/lib/notes/useNoteEdits";
import type { NoteAttachment, RecordingId, TimelineNote } from "@/types";

import Button from "../ui/Button";
import Modal from "../ui/Modal";
import { Switch } from "../ui/Switch";

import { NOTE_BUTTON_TOUCH } from "./noteButtons";

interface NoteFilesValue {
  recordingId: RecordingId;
  /**
   * Opens the note's file. Given the button pressed, a Remove that takes the
   * button away puts focus on its nearest `tabindex="-1"` ancestor, the
   * section it sat in, rather than back on the button.
   */
  open: (noteId: string, opener?: HTMLElement | null) => void;
}

const NoteFilesContext = createContext<NoteFilesValue | null>(null);

/** Opens a note's file in the viewer; does nothing outside a recording page. */
export const useOpenNoteFile = () => useContext(NoteFilesContext)?.open ?? null;

/** A note's moment, which its file shares; null for a note on the whole meeting. */
export const noteFileTimeMs = (note: Pick<TimelineNote, "kind" | "anchor_ms" | "start_ms">) =>
  note.kind === "point"
    ? (note.anchor_ms ?? 0)
    : note.kind === "selection"
      ? (note.start_ms ?? 0)
      : null;

type NoteText = Pick<TimelineNote, "kind" | "body">;

/** A capture's point note whose text is still only "Screen capture". */
const isUncaptionedCapture = (note: NoteText): boolean =>
  note.kind === "point" && note.body === CAPTURE_NOTE_BODY;

/**
 * Whether removing a note's file takes the note too: its text is still only
 * what came with the file, "Screen capture" on a capture or, on any note, the
 * file's name, which a file pasted or dropped with nothing typed gives it.
 * Exact, case and extension included. Nojoin decides by the same rule
 * (`note_goes_with_its_file`), on the note as it is there.
 */
export const noteGoesWithItsFile = (note: NoteText, file: Pick<NoteAttachment, "title">): boolean =>
  note.body === file.title || isUncaptionedCapture(note);

/** What the viewer asks before it removes a note's file, saying what else happens. */
export const removalQuestion = (
  note: NoteText,
  file: Pick<NoteAttachment, "title" | "use_in_notes">,
  atMs: number | null,
): string => {
  const place = atMs === null ? "on the meeting" : `at ${formatTimelineMs(atMs)}`;
  return [
    isUncaptionedCapture(note)
      ? `Remove this file and its note? The note ${place} has only the automatic text “${CAPTURE_NOTE_BODY}”, so it goes too.`
      : noteGoesWithItsFile(note, file)
        ? `Remove this file and its note? The note ${place} has only the file's name as its text, so it goes too.`
        : `Remove this file? Its note ${place} stays, with its text.`,
    file.use_in_notes
      ? "The file is used in notes, so notes already written are marked to regenerate."
      : null,
    "This cannot be undone.",
  ]
    .filter(Boolean)
    .join(" ");
};

const readState = (file: NoteAttachment): string => {
  if (!file.readable) return "Download only: Nojoin can't read this kind of file.";
  if (!file.use_in_notes) return "Off: the AI notes, Meeting Chat and search leave it out.";
  if (file.status === "PENDING" || file.status === "PROCESSING") {
    return "Being read. The AI notes, Meeting Chat and search draw on it once that finishes.";
  }
  if (file.status === "ERROR") {
    return file.error_message ? `Couldn't be read: ${file.error_message}` : "Couldn't be read.";
  }
  return file.parse_warning ?? "Read: the AI notes, Meeting Chat and search draw on it.";
};

interface NoteFilesProviderProps {
  recordingId: RecordingId;
  /** The recording's notes as last listed; null while they load. */
  notes: readonly TimelineNote[] | null;
  /** The page's note edits, so the viewer shows a note as moved or edited here. */
  noteEdits: Pick<NoteEdits, "apply">;
  /** Plays the recording from a time in seconds; without it, no Play button. */
  onPlay?: (seconds: number) => void;
  /** Called after the switch turns, for the stale notes banner. */
  onChanged?: () => void;
  children: ReactNode;
}

/**
 * The recording page's note files: what a note's line and the Captures tab
 * open, and the viewer they open, with the file's Use in notes switch and
 * Remove.
 */
export function NoteFilesProvider({
  recordingId,
  notes,
  noteEdits,
  onPlay,
  onChanged,
  children,
}: NoteFilesProviderProps) {
  const [openId, setOpenId] = useState<string | null>(null);
  // The file last opened and its button's section, and where focus goes once
  // the viewer has gone: set only when Remove takes that button away.
  const opened = useRef<{ noteId: string; home: HTMLElement | null } | null>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  const { outbox, snapshot } = useNoteOutbox(recordingId, selectSavedNotes);
  const { apply } = noteEdits;
  const note = useMemo(
    () =>
      apply(shownNotes(notes, snapshot)).find((candidate) => candidate.id === openId) ?? null,
    [apply, notes, snapshot, openId],
  );
  const value = useMemo(
    () => ({
      recordingId,
      open: (noteId: string, opener?: HTMLElement | null) => {
        const home = opener?.parentElement?.closest<HTMLElement>("[tabindex='-1']") ?? null;
        opened.current = { noteId, home };
        returnFocus.current = null;
        setOpenId(noteId);
      },
    }),
    [recordingId],
  );
  return (
    <NoteFilesContext.Provider value={value}>
      {children}
      <NoteFileViewer
        recordingId={recordingId}
        note={note?.attachment ? note : null}
        onClose={() => setOpenId(null)}
        returnFocus={returnFocus}
        onPlay={onPlay}
        onChanged={() => {
          // The page then polls until a file switched on has been read.
          outbox.notesChanged();
          onChanged?.();
        }}
        onRemoved={(noteId) => {
          // Nojoin refuses any later upload for the note, so none is kept here.
          outbox.forgetUpload(noteId);
          // The file's button goes with it, so focus goes to its section.
          if (opened.current?.noteId === noteId) returnFocus.current = opened.current.home;
          // Closed with X while the removal was on its way, another file may be open.
          setOpenId((current) => (current === noteId ? null : current));
          // A page that polls brings the list from the recording's read; one
          // that doesn't fetches it now. Either way the line updates in place.
          outbox.notesChanged();
          onChanged?.();
        }}
      />
    </NoteFilesContext.Provider>
  );
}

/**
 * A note's file on the note's line: its picture, or its name, which opens
 * the viewer. Before it reaches the server it says where it is, with Retry
 * and Discard once its upload has failed.
 */
export function NoteFile({
  noteId,
  attachment,
}: {
  noteId: string;
  attachment?: NoteAttachment | null;
}) {
  const context = useContext(NoteFilesContext);
  const { outbox, upload } = useNoteUpload(noteId);
  if (!context) return null;
  if (attachment) {
    return (
      <ButtonBase
        type="button"
        onClick={(event) => context.open(noteId, event.currentTarget)}
        data-testid="note-file"
        className="mt-1.5 flex max-w-full flex-col items-start gap-1 rounded-md text-left outline-none focus-visible:ring-2 focus-visible:ring-action"
        aria-label={`Open ${attachment.title}`}
      >
        {attachment.is_image ? (
          // From the API, which next/image would proxy.
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={documentPreviewUrl(attachment.id)}
            alt=""
            loading="lazy"
            className="max-h-32 w-auto max-w-full rounded-md border border-surface-border bg-surface-inset"
          />
        ) : null}
        <span className="inline-flex max-w-full items-center gap-1.5 text-xs text-contrast-helper">
          <Paperclip aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />
          <span className="truncate">{attachment.title}</span>
          {attachment.use_in_notes ? (
            <span className="shrink-0 font-semibold text-action-text">· In notes</span>
          ) : null}
        </span>
      </ButtonBase>
    );
  }
  if (!upload || !outbox) return null;
  return <NoteFileUpload upload={upload} outbox={outbox} />;
}

/** Only this note's upload can redraw its file or make an empty file slot appear. */
export function useNoteUpload(noteId: string) {
  const context = useContext(NoteFilesContext);
  const select = useCallback((snapshot: OutboxSnapshot) =>
    snapshot.uploads.get(noteId), [noteId]);
  const { outbox, snapshot: upload } = useNoteOutbox(context?.recordingId ?? null, select);
  return { outbox, upload };
}

/** A file on its way to its note, with Retry and Discard once its upload failed. */
export function NoteFileUpload({
  upload,
  outbox,
  onButton,
}: {
  upload: NoteUpload;
  outbox: NoteOutbox;
  /** Called as Retry or Discard is pressed, before the line may go. */
  onButton?: () => void;
}) {
  const failed = upload.status === "failed";
  return (
    <div data-testid="note-file-upload" data-upload-status={upload.status} className="mt-1.5">
      <p
        className={`flex items-center gap-1.5 text-xs ${
          failed ? "text-status-danger-fg" : "text-contrast-helper"
        }`}
      >
        <Paperclip aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />
        <span className="min-w-0 truncate">
          {upload.fileName}:{" "}
          {failed
            ? upload.error
            : upload.status === "uploading"
              ? "uploading…"
              : "uploads once the note is saved."}
        </span>
      </p>
      {failed ? (
        <div className="mt-1.5 flex flex-wrap gap-2">
          {(["Retry", "Discard"] as const).map((action) => (
            <Button
              key={action}
              size="sm"
              aria-label={`${action === "Retry" ? "Retry uploading" : "Discard"} ${upload.fileName}`}
              onClick={() => {
                onButton?.();
                if (action === "Retry") outbox.retryUpload(upload.key);
                else outbox.discardUpload(upload.key);
              }}
              className={NOTE_BUTTON_TOUCH}
            >
              {action}
            </Button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

interface NoteFileViewerProps {
  recordingId: RecordingId;
  /** The note whose file to show; null keeps the viewer closed. */
  note: TimelineNote | null;
  onClose: () => void;
  /** Where focus goes once the viewer has gone, instead of the button that opened it. */
  returnFocus: RefObject<HTMLElement | null>;
  onPlay?: (seconds: number) => void;
  onChanged: () => void;
  /** Called once the file is gone from its note, which may have gone too. */
  onRemoved: (noteId: string) => void;
}

const notify = (message: string) =>
  useNotificationStore.getState().addNotification({ type: "success", message });

function NoteFileViewer({
  recordingId,
  note,
  onClose,
  returnFocus,
  onPlay,
  onChanged,
  onRemoved,
}: NoteFileViewerProps) {
  const switchLabelId = useId();
  const switchHelpId = useId();
  const confirmId = useId();
  const [busy, setBusy] = useState(false);
  // The file whose removal is being confirmed, so another file opens without the question.
  const [confirmingId, setConfirmingId] = useState<number | null>(null);
  const [removing, setRemoving] = useState(false);
  const removeRef = useRef<HTMLButtonElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const file = note?.attachment ?? null;

  // Closing keeps `returnFocus`: it is read as the dialog unmounts, after its fade.
  if (!note || !file) {
    return (
      <Modal open={false} onClose={onClose} returnFocus={returnFocus}>
        {null}
      </Modal>
    );
  }
  const atMs = noteFileTimeMs(note);
  const confirming = confirmingId === file.id;
  const noteGoes = noteGoesWithItsFile(note, file);

  const close = () => {
    setConfirmingId(null);
    onClose();
  };

  // The question replaces the buttons, so focus moves with it, and back.
  const askToRemove = () => {
    flushSync(() => setConfirmingId(file.id));
    cancelRef.current?.focus();
  };
  const keepFile = () => {
    flushSync(() => setConfirmingId(null));
    removeRef.current?.focus();
  };

  const remove = async () => {
    setRemoving(true);
    try {
      const { note: kept } = await removeNoteFile(recordingId, note.id);
      notify(kept ? "File removed. Its note stays." : "File and note removed.");
    } catch (error: unknown) {
      const status = getErrorStatus(error);
      if (status !== 404 && status !== 410) {
        notifyRequestError(error, "The file could not be removed.");
        setRemoving(false);
        return;
      }
      // Removed already, on another device or with its note.
      notify("This file was already removed.");
    }
    setRemoving(false);
    setConfirmingId(null);
    onRemoved(note.id);
  };

  const toggle = async (useInNotes: boolean) => {
    setBusy(true);
    try {
      await setNoteFileUseInNotes(recordingId, note.id, useInNotes);
      onChanged();
    } catch (error: unknown) {
      notifyRequestError(error, "The file could not be switched.");
    } finally {
      setBusy(false);
    }
  };

  const download = async () => {
    try {
      const url = URL.createObjectURL(await downloadDocument(file.id));
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = file.title;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    } catch (error: unknown) {
      notifyRequestError(error, "The file could not be downloaded.");
    }
  };

  return (
    <Modal
      open
      onClose={close}
      // A removal on its way finishes before the viewer can be dismissed.
      dismissible={!removing}
      returnFocus={returnFocus}
      size="xl"
      title={file.title}
      description={readState(file)}
      footer={
        confirming ? (
          <div
            role="group"
            aria-labelledby={confirmId}
            data-testid="note-file-remove-confirm"
            className="flex w-full flex-col gap-3"
          >
            <p id={confirmId} className="text-sm text-foreground">
              {removalQuestion(note, file, atMs)}
            </p>
            <div className="flex flex-wrap justify-end gap-2">
              <Button ref={cancelRef} disabled={removing} onClick={keepFile}>
                Cancel
              </Button>
              <Button
                variant="danger"
                loading={removing}
                onClick={() => void remove()}
                iconLeft={<Trash2 aria-hidden="true" className="h-4 w-4" />}
              >
                {noteGoes ? "Remove file and note" : "Remove file"}
              </Button>
            </div>
          </div>
        ) : (
          <>
            <Button
              ref={removeRef}
              onClick={askToRemove}
              iconLeft={<Trash2 aria-hidden="true" className="h-4 w-4" />}
              className="mr-auto"
            >
              Remove
            </Button>
            <Button
              onClick={() => void download()}
              iconLeft={<Download aria-hidden="true" className="h-4 w-4" />}
            >
              Download
            </Button>
            {onPlay && atMs !== null ? (
              <Button
                onClick={() => {
                  onPlay(atMs / 1000);
                  close();
                }}
                iconLeft={<Play aria-hidden="true" className="h-4 w-4" />}
              >
                Play from {formatTimelineMs(atMs)}
              </Button>
            ) : null}
          </>
        )
      }
    >
      <div className="space-y-4">
        {file.is_image ? (
          <SurfaceBase data-ui-fill="inset" data-ui-border="full" kind="inset" className="flex items-center justify-center overflow-hidden rounded-md border border-surface-border bg-surface-inset">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={documentFileUrl(file.id)}
              alt={`${file.title}, on the note ${atMs === null ? "on the meeting" : `at ${formatTimelineMs(atMs)}`}`}
              className="max-h-[60dvh] w-auto max-w-full object-contain"
            />
          </SurfaceBase>
        ) : (
          <p data-ui-fill="inset" data-ui-border="full" data-ui="surface" data-ui-surface="inset" className="flex items-center gap-2 rounded-md border border-surface-border bg-surface-inset p-4 text-sm text-foreground">
            <FileText aria-hidden="true" className="h-5 w-5 shrink-0 text-contrast-icon-muted" />
            <span className="min-w-0 break-words">{file.title}</span>
          </p>
        )}
        <p className="whitespace-pre-wrap break-words text-sm text-foreground">
          <span className="font-semibold">
            {atMs === null ? "Note on the meeting" : `Note at ${formatTimelineMs(atMs)}`}:
          </span>{" "}
          {note.body}
        </p>
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <p id={switchLabelId} className="text-sm font-semibold text-foreground">
              Use in notes
            </p>
            <p id={switchHelpId} className="mt-0.5 text-sm text-contrast-helper">
              {file.readable
                ? "Off by default. On, Nojoin reads the file, and the AI notes, Meeting Chat and search use it. Turning it either way marks notes already written to regenerate."
                : "Unavailable: Nojoin can't read this kind of file, so it stays here to download."}
            </p>
          </div>
          <Switch
            checked={file.use_in_notes}
            disabled={busy || removing || !file.readable}
            onCheckedChange={(checked) => void toggle(checked)}
            aria-labelledby={switchLabelId}
            aria-describedby={switchHelpId}
          />
        </div>
      </div>
    </Modal>
  );
}
