"use client";

import { useEffect } from "react";

import { getRecording, getUserMe } from "@/lib/api";
import { getErrorDetail, getErrorStatus } from "@/lib/errors";
import { useSessionStore } from "@/lib/session";
import type { RecordingId } from "@/types";

import { forgetRecordingNotes, noteOutboxesHere } from "./noteOutboxes";
import { sharedNoteStore } from "./noteStore";

/**
 * Notes kept in this browser for a recording deleted for good on another
 * device, while no page of it was open here (issue #636). Deleting here, or a
 * page of the recording finding it gone, forgets them at once (#610); this
 * catches the rest the next time the recordings list loads.
 *
 * Only a request can tell, so the sweep is bounded: once per page load and
 * account, only for the account's records whose recording is neither in the
 * list nor held by an outbox on this page, at most `MAX_RECORDINGS_CHECKED` of
 * them, `CHECKS_AT_ONCE` at a time. A record goes only on the API's own 404
 * for its recording, given between two `/users/me` answers that name the
 * record's account: tabs share the session cookie, and under another account
 * every recording of the owner's answers 404 (#583). Any other answer keeps
 * it, a binned recording's 200 included, since the bin can be restored.
 */

/** The API's 404 for a recording that its account doesn't have. */
export const RECORDING_NOT_FOUND_DETAIL = "Recording not found";

/** Records checked per sweep at most; any others wait for the next page load. */
export const MAX_RECORDINGS_CHECKED = 20;

/** Checks in flight at once. */
export const CHECKS_AT_ONCE = 2;

const isGone = (recordingId: RecordingId): Promise<boolean> =>
  getRecording(recordingId).then(
    () => false,
    (error: unknown) =>
      getErrorStatus(error) === 404 && getErrorDetail(error) === RECORDING_NOT_FOUND_DETAIL,
  );

/** Whether the browser is still signed in as `account`, by the server's answer and this tab's. */
const stillSignedInAs = async (account: number): Promise<boolean> => {
  try {
    const user = await getUserMe();
    const { userId, signInRequired } = useSessionStore.getState();
    return user.id === account && userId === account && !signInRequired;
  } catch {
    return false;
  }
};

const goneOf = async (recordingIds: readonly RecordingId[]): Promise<RecordingId[]> => {
  const gone = new Set<RecordingId>();
  let next = 0;
  const checker = async () => {
    while (next < recordingIds.length) {
      const recordingId = recordingIds[next];
      next += 1;
      if (await isGone(recordingId)) gone.add(recordingId);
    }
  };
  await Promise.all(
    Array.from({ length: Math.min(CHECKS_AT_ONCE, recordingIds.length) }, checker),
  );
  return recordingIds.filter((recordingId) => gone.has(recordingId));
};

/**
 * Forgets the notes kept for each of the signed-in account's recordings that
 * is not in `listed` and turns out deleted for good. Returns the recordings it
 * forgot.
 */
export const forgetNotesOfDeletedRecordings = async (
  listed: readonly RecordingId[],
): Promise<RecordingId[]> => {
  const { userId: account, signInRequired } = useSessionStore.getState();
  const store = sharedNoteStore();
  if (account === null || signInRequired || !store) return [];
  let kept: RecordingId[];
  try {
    kept = await store.recordingsOf(account);
  } catch {
    return [];
  }
  const { held } = noteOutboxesHere();
  const unlisted = kept
    .filter((recordingId) => !listed.includes(recordingId) && !held.includes(recordingId))
    .slice(0, MAX_RECORDINGS_CHECKED);
  if (unlisted.length === 0 || !(await stillSignedInAs(account))) return [];
  const gone = await goneOf(unlisted);
  if (gone.length === 0 || !(await stillSignedInAs(account))) return [];
  for (const recordingId of gone) await forgetRecordingNotes(recordingId);
  return gone;
};

const sweptFor = new Set<number>();

/**
 * Runs `forgetNotesOfDeletedRecordings` once per page load and account, once
 * the recordings list has loaded (`listed` is null until then).
 */
export function useForgetNotesOfDeletedRecordings(
  listed: readonly { id: RecordingId }[] | null,
): void {
  const account = useSessionStore((state) => state.userId);

  useEffect(() => {
    if (listed === null || account === null || sweptFor.has(account)) return;
    sweptFor.add(account);
    void forgetNotesOfDeletedRecordings(listed.map((recording) => recording.id));
  }, [listed, account]);
}

/** Tests: let the next list load sweep again. */
export const resetDeletedRecordingSweepForTests = (): void => {
  sweptFor.clear();
};
