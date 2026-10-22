/** What a 404 for a recording turned out to mean. */
export type NotFoundMeaning =
  | { kind: "removed" }
  | { kind: "signed-in-elsewhere"; username: string };

/**
 * What a 404 for a recording says, once the server has named the account the
 * browser is signed in as (`whoIsSignedIn`, which tells the session too).
 * Tabs share the session cookie, so after a sign-in as another account in
 * another tab, this tab's requests go out as that account, and a recording
 * that still exists answers 404 (issue #583). Only for `account`, the account
 * the recording was loaded or its notes kept as, does a 404 mean it is gone.
 * The recording page and the notes kept in the browser (issue #558) share it.
 *
 * - "removed": the recording is gone.
 * - "signed-in-elsewhere": the browser is `username`'s now.
 * - null: nothing to act on. `/users/me` failed; or sign-in is held in place
 *   (`signInHeld`, read once the answer is in), and its dialog has opened
 *   instead; or `account` is unknown, so it can't tell, and claims no
 *   removal.
 */
export const notFoundMeans = async (
  account: number | null,
  whoIsSignedIn: () => Promise<{ id: number; username: string }>,
  signInHeld: () => boolean,
): Promise<NotFoundMeaning | null> => {
  let signedIn: { id: number; username: string };
  try {
    signedIn = await whoIsSignedIn();
  } catch {
    return null;
  }
  if (account === null) return null;
  if (signedIn.id === account) return { kind: "removed" };
  return signInHeld() ? null : { kind: "signed-in-elsewhere", username: signedIn.username };
};
