"use client";

import { Languages } from "lucide-react";
import { useState } from "react";

import ConfirmationModal from "@/components/ConfirmationModal";
import Button from "@/components/ui/Button";
import { useRecordingActions } from "@/components/recordings/_hooks/useRecordingActions";
import { Recording } from "@/types";

interface LanguageNoticeBannerProps {
  recording: Recording;
}

const announceUpdate = (id: Recording["id"]) =>
  window.dispatchEvent(new CustomEvent("recording-updated", { detail: { id } }));

/**
 * Says when language detection heard another language than the transcript
 * used (#671). The server decides when to show it (`language_notice`). Either
 * answer sets the recording's own language, so it doesn't come back: there is
 * no dismiss.
 */
export default function LanguageNoticeBanner({ recording }: LanguageNoticeBannerProps) {
  const actions = useRecordingActions();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const notice = recording.language_notice;
  if (!notice) return null;

  const done = {
    onSuccess: () => announceUpdate(recording.id),
    onError: () => setBusy(false),
  };
  const keep = () => {
    setBusy(true);
    void actions.setTranscriptionLanguage(recording.id, notice.used, done);
  };
  const reprocess = () => {
    setConfirming(false);
    setBusy(true);
    void actions.reprocessInLanguage(
      recording.id,
      notice.reprocess_language,
      notice.engine,
      done,
    );
  };

  return (
    <div
      role="status"
      className="flex shrink-0 flex-col gap-3 border-b border-status-info-border bg-status-info-bg px-4 py-3 md:flex-row md:items-center"
    >
      <div className="flex min-w-0 flex-1 items-start gap-3">
        <Languages
          aria-hidden="true"
          className="mt-0.5 h-4 w-4 shrink-0 text-status-info-fg"
        />
        <p className="min-w-0 text-sm text-status-info-fg">
          Language detection heard {notice.detected_label}, but this transcript
          is in {notice.used_label}.
        </p>
      </div>
      <div className="flex gap-2">
        <Button
          size="sm"
          variant="primary"
          className="flex-1 md:flex-none"
          disabled={busy}
          onClick={() => setConfirming(true)}
        >
          Reprocess in {notice.detected_label}
        </Button>
        <Button
          size="sm"
          variant="secondary"
          className="flex-1 md:flex-none"
          disabled={busy}
          onClick={keep}
        >
          Keep {notice.used_label}
        </Button>
      </div>
      <ConfirmationModal
        isOpen={confirming}
        onClose={() => setConfirming(false)}
        onConfirm={reprocess}
        title={`Reprocess in ${notice.detected_label}?`}
        message="Reprocessing replaces the transcript, speaker labels and meeting notes, so manual edits to those are lost. Manual processing notes, tags and documents are kept."
        confirmText="Reprocess"
      />
    </div>
  );
}
