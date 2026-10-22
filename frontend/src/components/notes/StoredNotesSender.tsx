"use client";

import { useEffect, useState } from "react";

import Button from "@/components/ui/Button";
import Modal from "@/components/ui/Modal";
import { forgetGoneNotes, noteRecordingRemoved, sweepStoredNotes } from "@/lib/notes/noteOutboxes";
import { useGoneNotes } from "@/lib/notes/useNoteOutbox";
import { useNotificationStore } from "@/lib/notificationStore";
import { subscribeRecordingRemoved } from "@/lib/recordingEvents";
import { useSessionStore, type SessionState } from "@/lib/session";

const sweepWhenSignedIn = ({ userId, signInRequired }: SessionState) => {
  if (userId !== null && !signInRequired) void sweepStoredNotes(userId);
};

/**
 * Sends the notes this browser kept for recordings the page has not opened
 * (issue #558), and shows the notes of a recording found gone, so they can be
 * copied before they are removed from the browser.
 */
export default function StoredNotesSender() {
  const gone = useGoneNotes();
  const addNotification = useNotificationStore((state) => state.addNotification);
  const [copyFailed, setCopyFailed] = useState(false);

  useEffect(() => {
    sweepWhenSignedIn(useSessionStore.getState());
    return useSessionStore.subscribe(sweepWhenSignedIn);
  }, []);

  useEffect(() => subscribeRecordingRemoved(noteRecordingRemoved), []);

  const texts = gone.flatMap((recording) => recording.texts);
  const forgetAll = async () => {
    await Promise.all(gone.map(({ recordingId }) => forgetGoneNotes(recordingId)));
    setCopyFailed(false);
  };
  const copyAll = async () => {
    try {
      await navigator.clipboard.writeText(texts.join("\n\n"));
    } catch {
      setCopyFailed(true);
      return;
    }
    await forgetAll();
    addNotification({ type: "info", message: "Copied the notes. They are no longer kept in this browser." });
  };

  return (
    <Modal
      open={texts.length > 0}
      onClose={() => undefined}
      dismissible={false}
      hideCloseButton
      size="lg"
      title="Notes for a recording that no longer exists"
      description="These were kept in this browser, but their recording was deleted, so they can't be saved. Copy them before you dismiss them: dismissing removes them from this browser."
      footer={
        <>
          <Button variant="ghost" onClick={() => void forgetAll()}>
            Dismiss
          </Button>
          <Button variant="primary" onClick={() => void copyAll()}>
            Copy all
          </Button>
        </>
      }
    >
      {copyFailed && (
        <p role="alert" className="mb-3 text-sm text-status-danger-fg">
          They couldn&apos;t be copied. Select the text below and copy it instead.
        </p>
      )}
      <ul className="space-y-3">
        {texts.map((text, index) => (
          <li
            key={index}
            className="whitespace-pre-wrap break-words rounded-lg border border-surface-border bg-surface-card p-3 text-sm text-foreground"
          >
            {text}
          </li>
        ))}
      </ul>
    </Modal>
  );
}
