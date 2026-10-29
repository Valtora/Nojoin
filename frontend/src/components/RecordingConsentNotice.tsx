"use client";

import { useEffect, useState } from "react";

import Button from "@/components/ui/Button";
import Modal from "@/components/ui/Modal";
import { InputBase } from "@/components/ui/nativeBases";
import { useRecordingActions } from "@/components/recordings/_hooks/useRecordingActions";
import { updateSettings } from "@/lib/api";
import { useCapture } from "@/lib/capture/CaptureProvider";
import { useNotificationStore } from "@/lib/notificationStore";
import type { RecordingId } from "@/types";

interface Props {
  recordingId: RecordingId;
  onClose: () => void;
}

/**
 * Shown only in the tab that started the recording, and only while its capture
 * runs. Nothing here stops or discards a recording unless Stop is pressed.
 */
export default function RecordingConsentNotice() {
  const { consentNoticeRecordingId, closeConsentNotice, recordingId, runtimeActive } = useCapture();
  if (!consentNoticeRecordingId || consentNoticeRecordingId !== recordingId || !runtimeActive) {
    return null;
  }
  return (
    <ConsentNoticeDialog
      key={consentNoticeRecordingId}
      recordingId={consentNoticeRecordingId}
      onClose={closeConsentNotice}
    />
  );
}

function ConsentNoticeDialog({ recordingId, onClose }: Props) {
  const [stage, setStage] = useState<"notice" | "decision">("notice");
  const [saving, setSaving] = useState(false);
  const [hidden, setHidden] = useState(false);
  const [savingPreference, setSavingPreference] = useState(false);
  const { answerConsentNotice } = useRecordingActions();
  const { controller, stop } = useCapture();
  const { addNotification } = useNotificationStore();

  useEffect(() => {
    const timer = window.setTimeout(onClose, 60_000);
    return () => window.clearTimeout(timer);
  }, [stage, onClose]);

  const answer = async (outcome: "agreed" | "not_agreed") => {
    setSaving(true);
    const saved = await answerConsentNotice(recordingId, outcome);
    setSaving(false);
    if (!saved) return;
    if (outcome === "agreed") onClose();
    else setStage("decision");
  };

  const savePreference = async (value: boolean) => {
    setSavingPreference(true);
    try {
      await updateSettings({ hide_recording_consent_notice: value });
      setHidden(value);
    } catch {
      addNotification({ type: "error", message: "Could not save your notice preference. Please try again." });
    } finally {
      setSavingPreference(false);
    }
  };

  // As the Stop button in the meeting controls: stop, then tell the lists and
  // the recording's page that the meeting has ended.
  const stopRecording = async () => {
    onClose();
    try {
      await stop();
    } catch {
      if (!controller.getState().error) {
        addNotification({ type: "error", message: "Could not stop the recording. Use the recording controls to try again." });
      }
      return;
    }
    setTimeout(() => {
      window.dispatchEvent(new CustomEvent("recording-updated", { detail: { id: recordingId } }));
    }, 300);
  };

  const footer = stage === "notice" ? (
    <>
      <Button disabled={saving} onClick={() => void answer("agreed")}>Everyone agreed</Button>
      <Button disabled={saving} onClick={() => void answer("not_agreed")}>Not everyone agreed</Button>
    </>
  ) : (
    <>
      <Button variant="danger" onClick={() => void stopRecording()}>Stop the recording</Button>
      <Button onClick={onClose}>Continue</Button>
    </>
  );

  return (
    <Modal
      open
      onClose={onClose}
      title={stage === "notice" ? "Recording consent" : "Keep recording?"}
      footer={footer}
    >
      {stage === "notice" ? (
        <div className="space-y-4 text-sm text-foreground">
          <p>Some places require everyone&apos;s consent to record a conversation. This reminder is not legal advice.</p>
          <p>You could ask the meeting:</p>
          <blockquote className="border-l-2 border-control-border pl-3">
            &ldquo;May I record and transcribe this meeting to prepare AI notes? Does everyone agree?&rdquo;
          </blockquote>
          <p>Recording has started. Closing this notice or leaving it unanswered keeps recording.</p>
          <label className="flex items-center gap-2">
            <InputBase
              type="checkbox"
              checked={hidden}
              disabled={savingPreference}
              onChange={(event) => void savePreference(event.target.checked)}
            />
            Don&apos;t show this again
          </label>
        </div>
      ) : (
        <p className="text-sm text-foreground">
          Your answer has been saved as &ldquo;Not everyone agreed&rdquo;. Choose whether to stop or continue.
          Closing this prompt or leaving it unanswered keeps recording.
        </p>
      )}
    </Modal>
  );
}
