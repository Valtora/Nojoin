"use client";

import { useEffect, useRef } from "react";

import { discardRecordingCapture, getRecording, getStalledImports } from "@/lib/api";
import { getErrorStatus } from "@/lib/errors";
import { useImportAudioStore } from "@/lib/importAudioStore";
import { useNotificationStore } from "@/lib/notificationStore";
import { dispatchRecordingRemoved } from "@/lib/recordingEvents";
import type { Recording } from "@/types";

/**
 * Discards an import that stopped before its upload finished, once the server
 * confirms it still has, and then opens Import Audio if asked.
 *
 * Looked up again first: the offer may have sat on screen while a slow part
 * arrived after all, and discard would delete a queued import as readily as
 * one still waiting.
 */
const discardStalledImport = async (recording: Recording, importAgain: boolean) => {
  const { addNotification } = useNotificationStore.getState();
  try {
    const current = await getRecording(recording.id);
    if (!current.import_stalled) {
      addNotification({
        type: "info",
        message: `The import of "${recording.name}" is uploading again or has finished, so it was left as it is.`,
      });
      return;
    }
    await discardRecordingCapture(recording.id, "import_abandoned");
    dispatchRecordingRemoved(recording.id);
  } catch (error: unknown) {
    // A 404 means it is gone already, which is what was asked for.
    if (getErrorStatus(error) !== 404) {
      console.error("Failed to discard an unfinished import", error);
      addNotification({
        type: "error",
        message: `Could not discard the unfinished import of "${recording.name}".`,
      });
      return;
    }
  }
  if (importAgain) {
    useImportAudioStore.getState().openImportAudio();
  }
};

const offer = (recording: Recording) => {
  useNotificationStore.getState().addNotification({
    id: `unfinished-import-${recording.id}`,
    type: "warning",
    persistent: true,
    message:
      `The import of "${recording.name}" did not finish: the page sending it closed ` +
      "or lost its connection before the whole file arrived. Import the file again, " +
      "or discard what arrived.",
    actions: [
      {
        label: "Import again",
        variant: "primary",
        onClick: () => {
          void discardStalledImport(recording, true);
        },
      },
      {
        label: "Discard",
        onClick: () => {
          void discardStalledImport(recording, false);
        },
      },
    ],
  });
};

/**
 * Tells the user, once per page load, about each import that stopped before
 * its upload finished (issue #569).
 *
 * An import cannot be resumed: the file is on the user's device, and nothing
 * here can tell that a file picked again is the same one. So the choice is to
 * import it again or discard the parts that arrived, and closing the notice
 * leaves the import as it is until the next page load. The server judges an
 * import stopped (`import_stalled`) once no part has arrived for 15 minutes,
 * so one still uploading in another tab is never offered. Nothing else waits on
 * the answer: an import does not hold the capture lock.
 */
export default function UnfinishedImportNotice() {
  const checkedRef = useRef(false);

  useEffect(() => {
    if (checkedRef.current) {
      return;
    }
    checkedRef.current = true;
    void getStalledImports()
      .then((imports) => imports.forEach(offer))
      .catch(() => {
        // Informational: without the list there is simply no notice.
      });
  }, []);

  return null;
}
