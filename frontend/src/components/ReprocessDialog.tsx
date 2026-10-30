"use client";

import { useEffect, useState } from "react";
import { useOnDepsChange } from "@/lib/useOnDepsChange";
import { RefreshCw, AlertTriangle } from "lucide-react";
import Button from "@/components/ui/Button";
import Modal from "@/components/ui/Modal";
import Select from "@/components/ui/Select";
import SpeakerCapField from "@/components/SpeakerCapField";
import RecordingLanguageField from "@/components/RecordingLanguageField";
import { getSettings, reprocessRecording } from "@/lib/api";
import { useNotificationStore } from "@/lib/notificationStore";
import { Recording, RecordingId, ReprocessRequest } from "@/types";

type TranscriptionBackend = "whisper" | "parakeet" | "canary";

const WHISPER_MODEL_SIZES = [
  "turbo",
  "large",
  "medium",
  "small",
  "base",
  "tiny",
];

const PARAKEET_MODEL = "parakeet-tdt-0.6b-v3";
const CANARY_MODEL = "nemo-canary-1b-v2";
/** Shown until the install's own size has loaded, or when it can't be read. */
const DEFAULT_WHISPER_MODEL_SIZE = "turbo";

interface ReprocessDialogProps {
  recordingId: RecordingId;
  isOpen: boolean;
  onClose: () => void;
  onReprocessed: (updatedRecording: Recording) => void;
  defaultBackend?: TranscriptionBackend;
  /** The recording's current speaker cap, so the field opens showing it. */
  currentMaxSpeakers?: number | null;
  /** The recording's language (null: the owner's setting), so the field opens showing it. */
  currentTranscriptionLanguage?: string | null;
}

export default function ReprocessDialog({
  recordingId,
  isOpen,
  onClose,
  onReprocessed,
  defaultBackend = "whisper",
  currentMaxSpeakers = null,
  currentTranscriptionLanguage = null,
}: ReprocessDialogProps) {
  const [backend, setBackend] = useState<TranscriptionBackend>(defaultBackend);
  // The size picked in this dialog; null follows the install's (issue #839).
  const [whisperModelSize, setWhisperModelSize] = useState<string | null>(null);
  const [installModelSize, setInstallModelSize] = useState<string | null>(null);
  const [maxSpeakers, setMaxSpeakers] = useState<number | null>(
    currentMaxSpeakers,
  );
  const [language, setLanguage] = useState<string | null>(
    currentTranscriptionLanguage,
  );
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const { addNotification } = useNotificationStore();

  useOnDepsChange(
    [isOpen, defaultBackend, currentMaxSpeakers, currentTranscriptionLanguage],
    () => {
      if (isOpen) {
        setBackend(defaultBackend);
        setWhisperModelSize(null);
        setMaxSpeakers(currentMaxSpeakers);
        setLanguage(currentTranscriptionLanguage);
        setIsSubmitting(false);
      }
    },
  );

  // Read on every open, so a size an administrator changed since shows.
  useEffect(() => {
    if (!isOpen) return;
    let active = true;
    getSettings()
      .then((settings) => {
        const size = settings.whisper_model_size;
        if (active && size && WHISPER_MODEL_SIZES.includes(size)) {
          setInstallModelSize(size);
        }
      })
      .catch(console.error);
    return () => {
      active = false;
    };
  }, [isOpen]);

  const shownModelSize =
    whisperModelSize ?? installModelSize ?? DEFAULT_WHISPER_MODEL_SIZE;

  if (!isOpen) return null;

  const handleConfirm = async () => {
    setIsSubmitting(true);

    let body: ReprocessRequest;
    if (backend === "whisper") {
      body = {
        transcription_backend: "whisper",
        whisper_model_size: shownModelSize,
      };
    } else if (backend === "canary") {
      body = {
        transcription_backend: "canary",
        canary_model: CANARY_MODEL,
      };
    } else {
      body = {
        transcription_backend: "parakeet",
        parakeet_model: PARAKEET_MODEL,
      };
    }

    // Always sent, so clearing the field genuinely clears the cap rather than
    // leaving the previous one in place.
    body.max_speakers = maxSpeakers;
    // Sent the same way, so "Your default" returns the recording to the
    // owner's setting. Parakeet takes no language, so the recording keeps its.
    if (backend !== "parakeet") body.transcription_language = language;

    try {
      const updatedRecording = await reprocessRecording(recordingId, body);
      onReprocessed(updatedRecording);
      onClose();

        } catch (err: unknown) {
      const message =
        err && typeof err === "object" && "response" in err
          ? ((err as { response?: { data?: { detail?: unknown } } }).response
              ?.data?.detail as string | undefined)
          : undefined;
      addNotification({
        type: "error",
        message: message || "Failed to start reprocessing. Please try again.",
      });
      setIsSubmitting(false);
    }
  };

  return (
    <Modal
      open
      onClose={onClose}
      // Reprocessing is destructive and cannot be interrupted once dispatched,
      // so the dialog stops accepting a dismissal while the request is away.
      dismissible={!isSubmitting}
      size="sm"
      title="Reprocess at higher quality"
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={isSubmitting}>
            Cancel
          </Button>
          <Button
            variant="primary"
            onClick={handleConfirm}
            disabled={isSubmitting}
            loading={isSubmitting}
            iconLeft={<RefreshCw aria-hidden="true" className="w-4 h-4" />}
          >
            {isSubmitting ? "Reprocessing..." : "Reprocess"}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Select
          label="Transcription engine"
          value={backend}
          onChange={(e) => setBackend(e.target.value as TranscriptionBackend)}
          disabled={isSubmitting}
        >
          <option value="whisper">Whisper</option>
          <option value="parakeet">Parakeet (NVIDIA)</option>
          <option value="canary">Canary 1B (NVIDIA)</option>
        </Select>

        {/* Model selection */}
        {backend === "whisper" ? (
          <Select
            label="Whisper Model Size"
            value={shownModelSize}
            onChange={(e) => setWhisperModelSize(e.target.value)}
            disabled={isSubmitting}
          >
            {WHISPER_MODEL_SIZES.map((size) => (
              <option key={size} value={size}>
                {size.charAt(0).toUpperCase() + size.slice(1)}
              </option>
            ))}
          </Select>
        ) : (
          <div>
            <label className="mb-2 block text-sm font-medium text-contrast-muted">
              {backend === "canary" ? "Canary Model" : "Parakeet Model"}
            </label>
            <div className="flex items-center gap-4 rounded-lg border border-surface-border bg-surface-inset p-4">
              <div className="flex-1">
                <div className="font-semibold text-foreground">
                  {backend === "canary" ? CANARY_MODEL : PARAKEET_MODEL}
                </div>
                <p className="mt-1 text-xs text-contrast-helper">
                  Model used for transcription.
                </p>
              </div>
            </div>
          </div>
        )}

        <RecordingLanguageField
          value={language}
          onChange={setLanguage}
          backend={backend}
          disabled={isSubmitting}
          idPrefix="reprocess-language"
        />

        <SpeakerCapField
          value={maxSpeakers}
          onCommit={setMaxSpeakers}
          disabled={isSubmitting}
          idPrefix="reprocess-speaker-cap"
        />

        {/* Destructive warning */}
        <div className="flex gap-3 rounded-lg border border-status-warning-border bg-status-warning-bg p-4">
          <AlertTriangle aria-hidden="true" className="mt-0.5 h-5 w-5 shrink-0 text-status-warning-fg" />
          <p className="text-sm text-status-warning-fg">
            Reprocessing replaces the current transcript, speaker labels and
            meeting notes for this recording. Any manual edits to those will
            be lost. Manual processing notes, tags and documents are kept.
          </p>
        </div>
      </div>
    </Modal>
  );
}
