import { useCallback, useEffect, useMemo, useRef } from "react";

import { getCurrentUser, getRecording } from "@/lib/api";
import { getErrorStatus } from "@/lib/errors";
import { subscribeStoreEvents } from "@/lib/notes/noteStore";
import type { NewNotification } from "@/lib/notificationStore";
import { useSessionStore } from "@/lib/session";
import type { RecordingId } from "@/types";

import { notFoundMeans } from "./recordingDetailUtils";

/**
 * How long a page that stayed after a 404 waits before asking `/users/me`
 * again while its recording still answers 404: once a minute per tab at most.
 */
export const NOT_FOUND_RECHECK_MS = 60_000;

export const RECORDING_REMOVED_MESSAGE = "Recording was discarded or deleted.";

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
 * under another account, it stays and says so once. That answer moves the
 * tab's session to the other account, so once the owner is back (a request
 * for the recording succeeds, or another tab's ping names the owner's
 * account), the page asks `/users/me` once more to move it back. Until then
 * it asks at most once a minute, so a recording really removed meanwhile is
 * still noticed.
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

  // The owner signed back in from another tab, whose `/users/me` pinged this
  // one. This page polls on under the cookie, and nothing else would tell
  // the tab's session the owner is back.
  useEffect(() => {
    const stop = subscribeStoreEvents((event) => {
      const account = loadedAsRef.current?.account ?? null;
      if (event.kind !== "kept" || account === null || !event.accounts.includes(account)) return;
      if (useSessionStore.getState().userId === account) return;
      getCurrentUser().catch(() => undefined);
    });
    return () => {
      stop();
      generationRef.current += 1;
    };
  }, []);

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
    if (!signedInElsewhereRef.current) return;
    signedInElsewhereRef.current = false;
    const account = loadedAsRef.current?.account ?? null;
    if (account !== null && useSessionStore.getState().userId !== account) {
      getCurrentUser().catch(() => undefined);
    }
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
          getCurrentUser,
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
