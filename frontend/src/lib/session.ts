import { create } from "zustand";

import { getErrorDetail, getErrorStatus, isRecord } from "@/lib/errors";

/**
 * Whether this tab's browser session still works, and what a refusal does.
 *
 * Normally a request refused with 401 sends the page to /login with a full
 * reload. A running capture cannot survive that: the reload tears the capture
 * down, and the pause sent on the way out is refused for the same reason, so
 * the recording is left open and the audio between the refusal and a fresh
 * start is lost (issue #502). While a capture runs, the capture controller
 * therefore holds sign-in in place: a refusal marks the session ended instead
 * of reloading, a dialog asks for the password, and each request waiting on it
 * runs again once the user has signed back in. A page holding notes not saved
 * yet holds it the same way (issue #537), so a device that only takes notes
 * keeps them through an ended session too.
 *
 * Signing back in is the ordinary session login. Nothing here extends a
 * session or changes how long one lasts.
 */

/** What can hold sign-in in place: a capture, or notes not saved yet. */
export type SignInHolder = "capture" | "notes";

export interface SessionState {
  /** The session cannot serve this tab until the user signs in again. */
  signInRequired: boolean;
  /** Whether a refusal is held in place rather than sent to the sign-in page. */
  heldInPlace: boolean;
  /** What holds it, so the sign-in dialog can say what is waiting. */
  heldBy: readonly SignInHolder[];
  /**
   * Who this tab is signed in as. Fixed while sign-in is held in place: the
   * capture or the notes belong to this account, and only its sign-in lets
   * them carry on.
   */
  username: string | null;
  /**
   * The id of the account `username` names, once the server has said
   * (`/users/me`), and null until then. Unlike the username it survives a
   * rename, so what this browser keeps for an account is keyed by it.
   */
  userId: number | null;
  /** Another account the browser was found signed in as while sign-in was held. */
  otherUsername: string | null;
}

export const useSessionStore = create<SessionState>(() => ({
  signInRequired: false,
  heldInPlace: false,
  heldBy: [],
  username: null,
  userId: null,
  otherUsername: null,
}));

const PASSWORD_CHANGE_REQUIRED_DETAIL = "Password change required";

/**
 * Counts the times this tab has seen the session come back. Every request is
 * stamped with the count it was sent under, so a refusal for a request sent
 * before the latest sign-in is recognised as stale and changes nothing.
 */
let signInCount = 0;

const sentUnder = new WeakMap<object, number>();

let waiters: Array<() => void> = [];

/** Stamps an outgoing request with the sign-in it is sent under. */
export const stampSessionRequest = <T extends object>(config: T): T => {
  sentUnder.set(config, signInCount);
  return config;
};

const signInCountWhenSent = (error: unknown): number => {
  const config = isRecord(error) && isRecord(error.config) ? error.config : null;
  return (config ? sentUnder.get(config) : undefined) ?? signInCount;
};

/**
 * True when a request was refused because the session can no longer be used:
 * it ended (401), or its password was reset and must be changed first.
 */
export const isSessionEndedError = (error: unknown): boolean => {
  const status = getErrorStatus(error);
  return (
    status === 401 ||
    (status === 403 && getErrorDetail(error) === PASSWORD_CHANGE_REQUIRED_DETAIL)
  );
};

const requireSignIn = () => {
  if (!useSessionStore.getState().signInRequired) {
    useSessionStore.setState({ signInRequired: true });
  }
};

/**
 * Records a refused request. Returns true when the page should stay where it
 * is: either sign-in is held in place, or the request was sent
 * before the latest sign-in and says nothing about the session now.
 */
export const noteSessionRefusal = (error: unknown): boolean => {
  if (signInCountWhenSent(error) < signInCount) {
    return true;
  }
  requireSignIn();
  return useSessionStore.getState().heldInPlace;
};

/**
 * Records that the server accepted this tab's session as `user`.
 *
 * While sign-in is held in place, only the account that held it counts. Tabs
 * share the session cookie, so a sign-in as somebody else in another tab
 * replaces this tab's session too, and the server refuses that account's
 * uploads to this recording. Finding the browser signed in as another account
 * therefore ends the session for this tab, as an expiry would. A session that
 * must change its password first does not count either.
 *
 * `user.id` comes with `/users/me` only; a sign-in that does not say keeps the
 * id it had for the same username, and forgets it for another. The id is how
 * a held page knows its own account after a rename.
 */
export const noteSignedIn = (user: {
  username: string;
  force_password_change: boolean;
  id?: number;
}) => {
  if (user.force_password_change) {
    return;
  }
  const state = useSessionStore.getState();
  // The same account under a new name (renamed meanwhile) is still the owner.
  const renamed = user.id !== undefined && user.id === state.userId;
  if (
    state.heldInPlace &&
    state.username !== null &&
    state.username !== user.username &&
    !renamed
  ) {
    if (!state.signInRequired || state.otherUsername !== user.username) {
      useSessionStore.setState({ signInRequired: true, otherUsername: user.username });
    }
    return;
  }
  const userId = user.id ?? (state.username === user.username ? state.userId : null);
  if (!state.signInRequired) {
    if (state.username !== user.username || state.userId !== userId) {
      useSessionStore.setState({ username: user.username, userId });
    }
    return;
  }

  signInCount += 1;
  useSessionStore.setState({
    signInRequired: false,
    username: user.username,
    userId,
    otherUsername: null,
  });
  const resolved = waiters;
  waiters = [];
  resolved.forEach((resolve) => resolve());
};

/**
 * Marks whether `holder` needs this tab to stay put through a refusal. Sign-in
 * is held in place while anything holds it.
 *
 * Releasing the hold while signed out does not redirect on its own: the next
 * refused request does, as it would have without the hold. Redirecting here
 * could run during a page unload and replace the navigation in progress.
 */
export const holdSignInInPlace = (held: boolean, holder: SignInHolder = "capture") => {
  const { heldBy } = useSessionStore.getState();
  if (heldBy.includes(holder) === held) {
    return;
  }
  const next = held ? [...heldBy, holder] : heldBy.filter((other) => other !== holder);
  useSessionStore.setState({ heldBy: next, heldInPlace: next.length > 0 });
};

/**
 * Decides what a failed request does while sign-in is held in place.
 * Resolves true once the request can be sent again, or false straight away
 * when the caller should treat the failure as an ordinary one.
 *
 * A request refused for an ended session waits for the user to sign back in.
 * So does any other failure while the session is known to be unusable: a
 * request sent as the wrong account fails with 404 rather than 401, and
 * counting it as an ordinary failure would stall the capture and pause the
 * recorder while the dialog is open.
 */
export const waitForSignInAfter = async (error: unknown): Promise<boolean> => {
  if (!useSessionStore.getState().heldInPlace) {
    return false;
  }
  if (signInCountWhenSent(error) < signInCount) {
    return true;
  }
  if (isSessionEndedError(error)) {
    // Normally the API client has already marked it. Marking it here as well
    // means a wait can never begin with no dialog open to end it.
    requireSignIn();
  } else if (!useSessionStore.getState().signInRequired) {
    return false;
  }
  await new Promise<void>((resolve) => {
    waiters.push(resolve);
  });
  return true;
};

/**
 * Runs a request, and runs it again each time it fails while the session is
 * unusable, once the user has signed back in. Any other failure, or any
 * failure while nothing holds sign-in in place, is thrown as usual.
 */
export const retryAfterSignIn = async <T>(request: () => Promise<T>): Promise<T> => {
  for (;;) {
    try {
      return await request();
    } catch (error: unknown) {
      if (!(await waitForSignInAfter(error))) {
        throw error;
      }
    }
  }
};

/** Restores the initial state. For tests. */
export const resetSessionForTests = () => {
  signInCount = 0;
  waiters = [];
  useSessionStore.setState({
    signInRequired: false,
    heldInPlace: false,
    heldBy: [],
    username: null,
    userId: null,
    otherUsername: null,
  });
};
