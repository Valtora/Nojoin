import { AxiosError } from "axios";
import { beforeEach, describe, expect, it } from "vitest";

import {
  holdSignInInPlace,
  isSessionEndedError,
  noteSessionRefusal,
  noteSignedIn,
  resetSessionForTests,
  retryAfterSignIn,
  stampSessionRequest,
  useSessionStore,
  waitForSignInAfter,
} from "./session";

const refused = (status: number, detail = "Not authenticated", config?: object) => {
  const error = new AxiosError(`Request failed with status code ${status}`);
  Object.assign(error, { config, response: { status, data: { detail } } });
  return error;
};

const alice = { username: "alice", force_password_change: false };

/** Resolves on the next macrotask, after every pending promise has settled. */
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

describe("browser session", () => {
  beforeEach(() => {
    resetSessionForTests();
    noteSignedIn(alice);
  });

  it("treats a 401 and a pending password change as an ended session", () => {
    expect(isSessionEndedError(refused(401))).toBe(true);
    expect(isSessionEndedError(refused(403, "Password change required"))).toBe(true);
    expect(isSessionEndedError(refused(403, "Not enough permissions"))).toBe(false);
    expect(isSessionEndedError(refused(500))).toBe(false);
    expect(isSessionEndedError(new Error("offline"))).toBe(false);
  });

  it("lets a refusal leave the page while nothing holds sign-in in place", () => {
    expect(noteSessionRefusal(refused(401))).toBe(false);
    expect(useSessionStore.getState().signInRequired).toBe(true);
  });

  it("keeps the page through a refusal while a capture holds sign-in in place", () => {
    holdSignInInPlace(true);

    expect(noteSessionRefusal(refused(401))).toBe(true);
    expect(useSessionStore.getState().signInRequired).toBe(true);
  });

  it("ignores a refusal for a request sent before the latest sign-in", () => {
    holdSignInInPlace(true);
    const config = stampSessionRequest({});
    noteSessionRefusal(refused(401));
    noteSignedIn(alice);

    // The request went out under the old session; the new one is fine.
    expect(noteSessionRefusal(refused(401, "Not authenticated", config))).toBe(true);
    expect(useSessionStore.getState().signInRequired).toBe(false);
  });

  it("does not count a sign-in that must still change its password", () => {
    holdSignInInPlace(true);
    noteSessionRefusal(refused(401));

    noteSignedIn({ username: "alice", force_password_change: true });

    expect(useSessionStore.getState().signInRequired).toBe(true);
  });

  it("does not count another account's sign-in while a capture waits", () => {
    holdSignInInPlace(true);
    noteSessionRefusal(refused(401));

    noteSignedIn({ username: "bob", force_password_change: false });

    expect(useSessionStore.getState()).toMatchObject({
      signInRequired: true,
      username: "alice",
    });
  });

  it("keeps the capture's account when the browser signs in as another", () => {
    holdSignInInPlace(true);

    // Another tab signed in as somebody else while the session still worked.
    // The cookie is shared, so this tab's requests now go out as that account.
    noteSignedIn({ username: "bob", force_password_change: false });

    expect(useSessionStore.getState()).toMatchObject({
      signInRequired: true,
      username: "alice",
      otherUsername: "bob",
    });

    noteSignedIn(alice);

    expect(useSessionStore.getState()).toMatchObject({
      signInRequired: false,
      username: "alice",
      otherUsername: null,
    });
  });

  it("does not let another account's sign-in release a waiting request", async () => {
    holdSignInInPlace(true);
    const error = refused(401);
    noteSessionRefusal(error);

    let done = false;
    void waitForSignInAfter(error).then(() => {
      done = true;
    });
    noteSignedIn({ username: "bob", force_password_change: false });
    await settle();
    expect(done).toBe(false);

    noteSignedIn(alice);
    await settle();
    expect(done).toBe(true);
  });

  it("holds any failure while the session is known to be unusable", async () => {
    holdSignInInPlace(true);
    noteSignedIn({ username: "bob", force_password_change: false });

    // Sent as the wrong account, an upload is refused with 404, not 401.
    let done = false;
    void waitForSignInAfter(refused(404, "Recording not found")).then(() => {
      done = true;
    });
    await settle();
    expect(done).toBe(false);

    noteSignedIn(alice);
    await settle();
    expect(done).toBe(true);
  });

  it("asks for sign-in before waiting on a refusal the client did not record", () => {
    holdSignInInPlace(true);

    void waitForSignInAfter(refused(401));

    expect(useSessionStore.getState().signInRequired).toBe(true);
  });

  it("takes another account's sign-in when nothing is waiting", () => {
    noteSessionRefusal(refused(401));

    noteSignedIn({ username: "bob", force_password_change: false });

    expect(useSessionStore.getState()).toMatchObject({
      signInRequired: false,
      username: "bob",
    });
  });

  it("treats a refusal as ordinary when nothing holds sign-in in place", async () => {
    await expect(waitForSignInAfter(refused(401))).resolves.toBe(false);
  });

  it("treats other failures as ordinary while a capture waits", async () => {
    holdSignInInPlace(true);

    await expect(waitForSignInAfter(refused(500))).resolves.toBe(false);
  });

  it("waits for the user to sign back in after a refusal", async () => {
    holdSignInInPlace(true);
    const error = refused(401);
    noteSessionRefusal(error);

    let done = false;
    void waitForSignInAfter(error).then(() => {
      done = true;
    });
    await settle();
    expect(done).toBe(false);

    noteSignedIn(alice);
    await settle();
    expect(done).toBe(true);
  });

  it("runs a refused request again once the user has signed back in", async () => {
    holdSignInInPlace(true);
    let calls = 0;
    const request = async () => {
      calls += 1;
      if (calls === 1) {
        const error = refused(401);
        noteSessionRefusal(error);
        throw error;
      }
      return "paused";
    };

    const result = retryAfterSignIn(request);
    await settle();
    expect(calls).toBe(1);

    noteSignedIn(alice);

    await expect(result).resolves.toBe("paused");
    expect(calls).toBe(2);
  });

  it("throws a refused request while nothing holds sign-in in place", async () => {
    const error = refused(401);

    await expect(retryAfterSignIn(async () => Promise.reject(error))).rejects.toBe(error);
  });
});
