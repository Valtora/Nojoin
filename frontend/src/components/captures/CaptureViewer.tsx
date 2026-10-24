"use client";

import { useId, useState } from "react";
import { Download, Play, RefreshCw, Trash2 } from "lucide-react";

import {
  captureImageUrl,
  downloadDocument,
  reparseDocument,
  type Capture,
} from "@/lib/api";
import { notifyRequestError } from "@/lib/notifyRequestError";
import { formatTimelineMs } from "@/lib/notes/placement";
import { useOnDepsChange } from "@/lib/useOnDepsChange";

import Button from "../ui/Button";
import Modal from "../ui/Modal";
import { Switch } from "../ui/Switch";

import { captureFileName } from "./captureItems";

interface CaptureViewerProps {
  /** The capture to show; null keeps the viewer closed. */
  capture: Capture | null;
  onClose: () => void;
  onSetUseInNotes: (capture: Capture, useInNotes: boolean) => void | Promise<void>;
  /** Deletes the capture; without it, no Delete button. Resolves whether it went. */
  onDelete?: (capture: Capture) => Promise<boolean>;
  /** Plays the recording from the capture's time; without it, no Play button. */
  onPlay?: (seconds: number) => void;
  /** Asks for the list again, after a parse is started. */
  onChanged?: () => void;
}

const readState = (capture: Capture): string => {
  if (capture.status === "PENDING" || capture.status === "PROCESSING") {
    return "Being read. Its words become searchable once that finishes.";
  }
  if (capture.status === "ERROR") {
    return capture.error_message
      ? `Couldn't be read: ${capture.error_message}`
      : "Couldn't be read.";
  }
  return capture.parse_warning ?? "Read, so its words are searchable and Meeting Chat can draw on it.";
};

/**
 * One capture at full size, with its notes switch. The switch is off until
 * the user turns it on: a capture is always read and searchable, but notes
 * generation reads it only when chosen, and turning it either way after the
 * notes were written marks them to regenerate.
 */
export default function CaptureViewer({
  capture,
  onClose,
  onSetUseInNotes,
  onDelete,
  onPlay,
  onChanged,
}: CaptureViewerProps) {
  const switchLabelId = useId();
  const switchHelpId = useId();
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [busy, setBusy] = useState<"delete" | "download" | "reparse" | null>(null);
  useOnDepsChange([capture?.id], () => {
    setConfirmingDelete(false);
    setBusy(null);
  });

  if (!capture) {
    return <Modal open={false} onClose={onClose}>{null}</Modal>;
  }
  const time = formatTimelineMs(capture.capture_ms);

  const download = async () => {
    setBusy("download");
    try {
      const blob = await downloadDocument(capture.id);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = captureFileName(capture);
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    } catch (error: unknown) {
      notifyRequestError(error, "The capture could not be downloaded.");
    } finally {
      setBusy(null);
    }
  };

  const remove = async () => {
    if (!onDelete) return;
    if (!confirmingDelete) {
      setConfirmingDelete(true);
      return;
    }
    setBusy("delete");
    const deleted = await onDelete(capture);
    setBusy(null);
    if (deleted) onClose();
  };

  const readAgain = async () => {
    setBusy("reparse");
    try {
      await reparseDocument(capture.id, { deepParse: true });
      onChanged?.();
    } catch (error: unknown) {
      notifyRequestError(error, "The capture could not be read again.");
    } finally {
      setBusy(null);
    }
  };

  return (
    <Modal
      open
      onClose={onClose}
      size="xl"
      title={`Capture at ${time}`}
      description={readState(capture)}
      footer={
        <>
          {onDelete ? (
            <Button
              variant={confirmingDelete ? "danger" : "ghost"}
              onClick={() => void remove()}
              loading={busy === "delete"}
              iconLeft={<Trash2 aria-hidden="true" className="h-4 w-4" />}
              className="mr-auto"
            >
              {confirmingDelete ? "Confirm delete" : "Delete"}
            </Button>
          ) : null}
          {capture.status === "ERROR" ? (
            <Button
              onClick={() => void readAgain()}
              loading={busy === "reparse"}
              iconLeft={<RefreshCw aria-hidden="true" className="h-4 w-4" />}
            >
              Read again
            </Button>
          ) : null}
          <Button
            onClick={() => void download()}
            loading={busy === "download"}
            iconLeft={<Download aria-hidden="true" className="h-4 w-4" />}
          >
            Download
          </Button>
          {onPlay ? (
            <Button
              onClick={() => {
                onPlay(capture.capture_ms / 1000);
                onClose();
              }}
              iconLeft={<Play aria-hidden="true" className="h-4 w-4" />}
            >
              Play from {time}
            </Button>
          ) : null}
        </>
      }
    >
      <div className="space-y-4">
        <div className="flex items-center justify-center overflow-hidden rounded-md border border-surface-border bg-surface-inset">
          {/* The full image from the API: next/image would proxy it. */}
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={captureImageUrl(capture.id)}
            alt={`The capture at ${time}`}
            className="max-h-[60dvh] w-auto max-w-full object-contain"
          />
        </div>
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <p id={switchLabelId} className="text-sm font-semibold text-foreground">
              Use in notes
            </p>
            <p id={switchHelpId} className="mt-0.5 text-sm text-contrast-helper">
              Off, it is searchable but the AI notes leave it out. On, notes generated
              from now on read it.
            </p>
          </div>
          <Switch
            checked={capture.use_in_notes}
            onCheckedChange={(checked) => void onSetUseInNotes(capture, checked)}
            aria-labelledby={switchLabelId}
            aria-describedby={switchHelpId}
          />
        </div>
      </div>
    </Modal>
  );
}
