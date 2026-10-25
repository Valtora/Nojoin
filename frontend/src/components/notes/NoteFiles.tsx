"use client";

import { ButtonBase } from "@/components/ui/nativeBases";
import { SurfaceBase } from "@/components/ui/SurfaceBase";
import {
  createContext,
  useContext,
  useId,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { Download, FileText, Paperclip, Play } from "lucide-react";

import {
  documentFileUrl,
  documentPreviewUrl,
  downloadDocument,
  setNoteFileUseInNotes,
} from "@/lib/api";
import { shownNotes, type NoteOutbox, type NoteUpload } from "@/lib/notes/noteOutbox";
import { formatTimelineMs } from "@/lib/notes/placement";
import { useNoteOutbox } from "@/lib/notes/useNoteOutbox";
import { notifyRequestError } from "@/lib/notifyRequestError";
import type { NoteEdits } from "@/lib/notes/useNoteEdits";
import type { NoteAttachment, RecordingId, TimelineNote } from "@/types";

import Button from "../ui/Button";
import Modal from "../ui/Modal";
import { Switch } from "../ui/Switch";

import { NOTE_BUTTON_TOUCH } from "./noteButtons";

interface NoteFilesValue {
  recordingId: RecordingId;
  open: (noteId: string) => void;
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
 * open, and the viewer they open, with the file's Use in notes switch.
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
  const { outbox, snapshot } = useNoteOutbox(recordingId);
  const { apply } = noteEdits;
  const note = useMemo(
    () =>
      apply(shownNotes(notes, snapshot)).find((candidate) => candidate.id === openId) ?? null,
    [apply, notes, snapshot, openId],
  );
  const value = useMemo(() => ({ recordingId, open: setOpenId }), [recordingId]);
  return (
    <NoteFilesContext.Provider value={value}>
      {children}
      <NoteFileViewer
        recordingId={recordingId}
        note={note?.attachment ? note : null}
        onClose={() => setOpenId(null)}
        onPlay={onPlay}
        onChanged={() => {
          // The page then polls until a file switched on has been read.
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
  const { outbox, snapshot } = useNoteOutbox(context?.recordingId ?? null);
  if (!context) return null;
  if (attachment) {
    return (
      <ButtonBase
        type="button"
        onClick={() => context.open(noteId)}
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
  const upload = snapshot.uploads.get(noteId);
  if (!upload || !outbox) return null;
  return <NoteFileUpload upload={upload} outbox={outbox} />;
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
  onPlay?: (seconds: number) => void;
  onChanged: () => void;
}

function NoteFileViewer({
  recordingId,
  note,
  onClose,
  onPlay,
  onChanged,
}: NoteFileViewerProps) {
  const switchLabelId = useId();
  const switchHelpId = useId();
  const [busy, setBusy] = useState(false);
  const file = note?.attachment ?? null;

  if (!note || !file) return <Modal open={false} onClose={onClose}>{null}</Modal>;
  const atMs = noteFileTimeMs(note);

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
      onClose={onClose}
      size="xl"
      title={file.title}
      description={readState(file)}
      footer={
        <>
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
                onClose();
              }}
              iconLeft={<Play aria-hidden="true" className="h-4 w-4" />}
            >
              Play from {formatTimelineMs(atMs)}
            </Button>
          ) : null}
        </>
      }
    >
      <div className="space-y-4">
        {file.is_image ? (
          <SurfaceBase kind="inset" className="flex items-center justify-center overflow-hidden rounded-md border border-surface-border bg-surface-inset">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={documentFileUrl(file.id)}
              alt={`${file.title}, on the note ${atMs === null ? "on the meeting" : `at ${formatTimelineMs(atMs)}`}`}
              className="max-h-[60dvh] w-auto max-w-full object-contain"
            />
          </SurfaceBase>
        ) : (
          <p data-ui="surface" data-ui-surface="inset" className="flex items-center gap-2 rounded-md border border-surface-border bg-surface-inset p-4 text-sm text-foreground">
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
            disabled={busy || !file.readable}
            onCheckedChange={(checked) => void toggle(checked)}
            aria-labelledby={switchLabelId}
            aria-describedby={switchHelpId}
          />
        </div>
      </div>
    </Modal>
  );
}
