"use client";

import { Download } from "lucide-react";

import Button from "../ui/Button";
import Modal from "../ui/Modal";

interface ModelDownloadPromptModalProps {
  isOpen: boolean;
  /** Human-readable name of the newly selected model, e.g. "Whisper Turbo". */
  modelLabel: string;
  /** True while the preparation request is in flight. */
  busy?: boolean;
  /** Queue preparation now. */
  onDownloadNow: () => void;
  /** Dismiss without downloading. Backdrop, Escape and the close button all
   * land here: declining is the safe answer, so it must be the easy one. */
  onLater: () => void;
}

/**
 * Asks whether a newly selected transcription model should be fetched now.
 *
 * Preparation runs on the GPU worker lane, the same lane that serves live
 * transcription, so it is a decision worth surfacing rather than a side effect
 * of saving a setting.
 */
export default function ModelDownloadPromptModal({
  isOpen,
  modelLabel,
  busy = false,
  onDownloadNow,
  onLater,
}: ModelDownloadPromptModalProps) {
  return (
    <Modal
      open={isOpen}
      onClose={onLater}
      // Dismissal is blocked only while the request is in flight, so a stray
      // Escape cannot leave the caller waiting on a modal that has gone.
      dismissible={!busy}
      size="sm"
      title={`Download ${modelLabel} now?`}
      footer={
        <>
          <Button variant="ghost" onClick={onLater} disabled={busy}>
            Download later
          </Button>
          <Button
            variant="primary"
            onClick={onDownloadNow}
            loading={busy}
            iconLeft={<Download aria-hidden="true" className="w-4 h-4" />}
          >
            Download now
          </Button>
        </>
      }
    >
      <p className="mb-4 text-sm text-contrast-muted">
        {modelLabel} is not on the server yet. Downloading it now means it is
        ready before your next recording starts.
      </p>
      <p className="text-sm text-contrast-helper">
        If you skip this, the model is fetched the first time it is needed.
        That works, but Meeting Edge and live transcription stay unavailable
        until the download finishes and the model has loaded.
      </p>
    </Modal>
  );
}
