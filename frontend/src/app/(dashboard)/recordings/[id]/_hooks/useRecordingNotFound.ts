import { useCallback, useEffect, useMemo, useRef } from "react";

import { askWhoIsSignedIn, getRecording } from "@/lib/api";
import { getErrorStatus } from "@/lib/errors";
import type { NewNotification } from "@/lib/notificationStore";
import { noteSignedIn, useSessionStore } from "@/lib/session";
import type { RecordingId } from "@/types";

import { notFoundMeans } from "./recordingDetailUtils";

/**
 * How long a page that stayed after a 404 waits before asking `/users/me`
 * again while its recording still answers 404: once a minute per tab at most.
 */
export const NOT_FOUND_RECHECK_MS = 60_000;

export const RECORDING_REMOVED_MESSAGE = "Recording was discarded or deleted.";

/**
 * `/users/me`, told to the session only where sign-in is held: there the
 * other account's answer opens the sign-in dialog, pinned to the owner.
 */
const whoIsSignedIn = async () => {
  const user = await askWhoIsSignedIn();
  if (useSessionStore.getState().heldInPlace) noteSignedIn(user);
  return user;
};

interface LoadedAs {
  recordingId: RecordingId;
  /** The account the browser was signed in as when the recording first loaded. */
  account: number | null;
}

/**
 * What the recording page makes of a 404 for its recording (issue #583).
 *
 * Tabs share the session cookie, so a sign-in as another account in another
 * tab makes this page's requests answer 404 although nothing was deleted.
 * Before acting on a 404, the page asks `/users/me` (`notFoundMeans`): the
 * account that first loaded the recording sees it gone, and the page leaves;
 * under another account, it stays and says so once. While the 404s go on, it
 * asks at most once a minute, so a recording really removed meanwhile is
 * still noticed.
 *
 * The answer only decides what the 404 means. It leaves the tab's session as
 * it was, the owner's, so her notes are still stored and a notes hold begun
 * meanwhile is hers; only a page that holds sign-in passes it on, which opens
 * the sign-in dialog for the owner.
 */
export function useRecordingNotFound(
  addNotification: (notification: NewNotification) => unknown,
  leave: () => void,
) {
  const loadedAsRef = useRef<LoadedAs | null>(null);
  const signedInElsewhereRef = useRef(false);
  const askedAtRef = useRef<number | null>(null);
  const checkingRef = useRef(false);
  // Bumped when the page unmounts or loads another recording: a check begun
  // before then does nothing once it answers.
  const generationRef = useRef(0);

  useEffect(
    () => () => {
      generationRef.current += 1;
    },
    [],
  );

  /** The recording loaded: the first load of each recording decides the page's account. */
  const loaded = useCallback((recordingId: RecordingId) => {
    if (loadedAsRef.current?.recordingId === recordingId) return;
    generationRef.current += 1;
    loadedAsRef.current = { recordingId, account: useSessionStore.getState().userId };
    signedInElsewhereRef.current = false;
    askedAtRef.current = null;
  }, []);

  const isLoaded = useCallback(
    (recordingId: RecordingId) => loadedAsRef.current?.recordingId === recordingId,
    [],
  );

  /** A request for the recording succeeded: if the browser was another account's, the owner is back. */
  const found = useCallback(() => {
    askedAtRef.current = null;
    signedInElsewhereRef.current = false;
  }, []);

  /**
   * The recording answered 404 while the session is usable. One check at a
   * time, and after one that kept the page here, the next only a minute on.
   */
  const notFound = useCallback(
    async (recordingId: RecordingId) => {
      if (checkingRef.current) return;
      const askedAt = askedAtRef.current;
      if (askedAt !== null && Date.now() - askedAt < NOT_FOUND_RECHECK_MS) return;
      checkingRef.current = true;
      const generation = generationRef.current;
      const current = () => generation === generationRef.current;
      try {
        const loadedAs = loadedAsRef.current;
        const answer = await notFoundMeans(
          loadedAs?.recordingId === recordingId ? loadedAs.account : null,
          whoIsSignedIn,
          () => useSessionStore.getState().signInRequired,
        );
        if (!current()) return;
        if (answer?.kind === "removed") {
          // The 404 and `/users/me` went out apart, and the owner may have
          // signed back in between: leave only if it is still not found.
          const status = await getRecording(recordingId).then(
            () => null,
            (error: unknown) => getErrorStatus(error),
          );
          if (!current()) return;
          if (status === 404) {
            addNotification({ type: "info", message: RECORDING_REMOVED_MESSAGE });
            leave();
            return;
          }
          if (status === null) {
            found();
            return;
          }
        }
        askedAtRef.current = Date.now();
        if (answer?.kind === "signed-in-elsewhere" && !signedInElsewhereRef.current) {
          signedInElsewhereRef.current = true;
          addNotification({
            type: "info",
            message: `This browser is now signed in as ${answer.username}, so this recording can't be updated. Sign in again as its owner to carry on.`,
          });
        }
      } finally {
        checkingRef.current = false;
      }
    },
    [addNotification, found, leave],
  );

  return useMemo(
    () => ({ loaded, isLoaded, found, notFound }),
    [loaded, isLoaded, found, notFound],
  );
}
