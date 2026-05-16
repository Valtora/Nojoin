"use client";

import { useState, useEffect } from "react";
import { X, RefreshCw, AlertTriangle } from "lucide-react";
import { reprocessRecording } from "@/lib/api";
import { Recording, RecordingId, ReprocessRequest } from "@/types";

type TranscriptionBackend = "whisper" | "parakeet";

const WHISPER_MODEL_SIZES = [
  "turbo",
  "large",
  "medium",
  "small",
  "base",
  "tiny",
];

const PARAKEET_MODEL = "parakeet-tdt-0.6b-v3";
const DEFAULT_WHISPER_MODEL_SIZE = "turbo";

interface ReprocessDialogProps {
  recordingId: RecordingId;
  isOpen: boolean;
  onClose: () => void;
  onReprocessed: (updatedRecording: Recording) => void;
}

export default function ReprocessDialog({
  recordingId,
  isOpen,
  onClose,
  onReprocessed,
}: ReprocessDialogProps) {
  const [backend, setBackend] = useState<TranscriptionBackend>("whisper");
  const [whisperModelSize, setWhisperModelSize] = useState<string>(
    DEFAULT_WHISPER_MODEL_SIZE,
  );
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (isOpen) {
      setBackend("whisper");
      setWhisperModelSize(DEFAULT_WHISPER_MODEL_SIZE);
      setIsSubmitting(false);
      setError(null);
    }
  }, [isOpen]);

  if (!isOpen) return null;

  const handleConfirm = async () => {
    setIsSubmitting(true);
    setError(null);

    const body: ReprocessRequest =
      backend === "whisper"
        ? {
            transcription_backend: "whisper",
            whisper_model_size: whisperModelSize,
          }
        : {
            transcription_backend: "parakeet",
            parakeet_model: PARAKEET_MODEL,
          };

    try {
      const updatedRecording = await reprocessRecording(recordingId, body);
      onReprocessed(updatedRecording);
      onClose();
    } catch (err) {
      const message =
        err && typeof err === "object" && "response" in err
          ? ((err as { response?: { data?: { detail?: unknown } } }).response
              ?.data?.detail as string | undefined)
          : undefined;
      setError(message || "Failed to start reprocessing. Please try again.");
      setIsSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/50 backdrop-blur-sm"
        onClick={isSubmitting ? undefined : onClose}
      />

      {/* Modal */}
      <div className="relative bg-white dark:bg-gray-800 rounded-xl shadow-2xl w-full max-w-md mx-4 overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200 dark:border-gray-700">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white">
            Reprocess at higher quality
          </h2>
          <button
            onClick={onClose}
            disabled={isSubmitting}
            className="p-1 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 rounded-md hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors disabled:opacity-50"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Content */}
        <div className="p-6 space-y-4">
          {/* Engine select */}
          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
              Transcription engine
            </label>
            <select
              value={backend}
              onChange={(e) =>
                setBackend(e.target.value as TranscriptionBackend)
              }
              disabled={isSubmitting}
              className="w-full p-2.5 rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-900 text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500 outline-none transition-all disabled:opacity-50"
            >
              <option value="whisper">Whisper</option>
              <option value="parakeet">Parakeet (NVIDIA)</option>
            </select>
          </div>

          {/* Model selection */}
          {backend === "whisper" ? (
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                Whisper Model Size
              </label>
              <select
                value={whisperModelSize}
                onChange={(e) => setWhisperModelSize(e.target.value)}
                disabled={isSubmitting}
                className="w-full p-2.5 rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-900 text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500 outline-none transition-all disabled:opacity-50"
              >
                {WHISPER_MODEL_SIZES.map((size) => (
                  <option key={size} value={size}>
                    {size.charAt(0).toUpperCase() + size.slice(1)}
                  </option>
                ))}
              </select>
            </div>
          ) : (
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                Parakeet Model
              </label>
              <div className="flex items-center gap-4 p-4 rounded-lg bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700">
                <div className="flex-1">
                  <div className="font-semibold text-gray-900 dark:text-white">
                    {PARAKEET_MODEL}
                  </div>
                  <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                    Model used for transcription.
                  </p>
                </div>
              </div>
            </div>
          )}

          {/* Destructive warning */}
          <div className="flex gap-3 p-4 rounded-lg bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800">
            <AlertTriangle className="w-5 h-5 text-amber-600 dark:text-amber-400 shrink-0 mt-0.5" />
            <p className="text-sm text-amber-800 dark:text-amber-200">
              Reprocessing replaces the current transcript, speaker labels and
              meeting notes for this recording. Any manual edits to those will
              be lost. Manual processing notes, tags and documents are kept.
            </p>
          </div>

          {/* Error message */}
          {error && (
            <p className="text-sm text-red-600 dark:text-red-400">{error}</p>
          )}
        </div>

        {/* Footer */}
        <div className="flex justify-end gap-3 px-6 py-4 border-t border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900/50">
          <button
            onClick={onClose}
            disabled={isSubmitting}
            className="px-4 py-2 text-gray-600 dark:text-gray-400 hover:text-gray-800 dark:hover:text-gray-200 transition-colors disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            onClick={handleConfirm}
            disabled={isSubmitting}
            className="flex items-center gap-2 px-4 py-2 bg-orange-600 text-white rounded-md hover:bg-orange-700 transition-colors disabled:opacity-50"
          >
            <RefreshCw
              className={`w-4 h-4 ${isSubmitting ? "animate-spin" : ""}`}
            />
            {isSubmitting ? "Reprocessing..." : "Reprocess"}
          </button>
        </div>
      </div>
    </div>
  );
}
