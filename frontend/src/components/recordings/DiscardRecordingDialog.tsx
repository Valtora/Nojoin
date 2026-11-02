"use client";

import ConfirmationModal from "../ConfirmationModal";

/** What every Discard asks before it deletes an unfinished recording. */
export const DISCARD_RECORDING_TITLE = "Discard Recording";
export const DISCARD_RECORDING_MESSAGE =
  "Discard this recording? This permanently deletes the in-progress meeting and its audio, and cannot be undone.";

interface DiscardRecordingDialogProps {
  isOpen: boolean;
  onClose: () => void;
  onConfirm: () => void;
}

/**
 * The in-app confirmation for a Discard, the same one the recordings list
 * shows, in place of the browser's own confirm, which halts the page's
 * scripts until it is answered.
 */
export default function DiscardRecordingDialog({ isOpen, onClose, onConfirm }: DiscardRecordingDialogProps) {
  return (
    <ConfirmationModal
      isOpen={isOpen}
      onClose={onClose}
      onConfirm={onConfirm}
      title={DISCARD_RECORDING_TITLE}
      message={DISCARD_RECORDING_MESSAGE}
      confirmText="Discard"
      isDangerous
    />
  );
}
