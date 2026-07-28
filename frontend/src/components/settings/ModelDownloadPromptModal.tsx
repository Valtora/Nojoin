"use client";

import { Download, Loader2, X } from "lucide-react";
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

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
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
    return () => setMounted(false);
  }, []);

  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) onLater();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, busy, onLater]);

  if (!isOpen || !mounted) return null;

  return createPortal(
    <div
      className="fixed inset-0 z-9999 flex items-center justify-center bg-black/50 p-4 backdrop-blur-sm"
      onClick={() => {
        if (!busy) onLater();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="model-download-prompt-title"
        className="bg-white dark:bg-gray-900 rounded-xl shadow-xl w-full max-w-md max-h-[calc(100dvh-2rem)] overflow-y-auto border border-gray-300 dark:border-gray-800 p-6 relative animate-in fade-in zoom-in-95 duration-200"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex justify-between items-start mb-4">
          <h3
            id="model-download-prompt-title"
            className="text-lg font-bold text-gray-900 dark:text-white"
          >
            Download {modelLabel} now?
          </h3>
          <button
            onClick={onLater}
            disabled={busy}
            aria-label="Close"
            className="text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 disabled:opacity-50"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        <p className="text-gray-600 dark:text-gray-300 mb-4">
          {modelLabel} is not on the server yet. Downloading it now means it is
          ready before your next recording starts.
        </p>
        <p className="text-sm contrast-helper mb-6">
          If you skip this, the model is fetched the first time it is needed.
          That works, but Meeting Edge and live transcription stay unavailable
          until the download finishes and the model has loaded.
        </p>

        <div className="flex justify-end gap-3">
          <button
            onClick={onLater}
            disabled={busy}
            className="px-4 py-2 text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg text-sm font-medium disabled:opacity-50"
          >
            Download later
          </button>
          <button
            onClick={onDownloadNow}
            disabled={busy}
            className="px-4 py-2 flex items-center gap-2 bg-orange-600 hover:bg-orange-700 text-white rounded-lg text-sm font-medium disabled:opacity-50"
          >
            {busy ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Download className="w-4 h-4" />
            )}
            Download now
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
