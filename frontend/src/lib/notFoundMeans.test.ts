import { describe, expect, it } from "vitest";

import { notFoundMeans } from "./notFoundMeans";

describe("notFoundMeans", () => {
  const ALICE = { id: 1, username: "alice" };
  const BOB = { id: 2, username: "bob" };
  const notHeld = () => false;

  it("reads a 404 as a removal when the account the recording was loaded as is still signed in", async () => {
    await expect(notFoundMeans(1, async () => ALICE, notHeld)).resolves.toEqual({ kind: "removed" });
  });

  it("names the account the server answered with when it is another", async () => {
    await expect(notFoundMeans(1, async () => BOB, notHeld)).resolves.toEqual({
      kind: "signed-in-elsewhere",
      username: "bob",
    });
  });

  it("says nothing more while sign-in is held, as its dialog has opened", async () => {
    await expect(notFoundMeans(1, async () => BOB, () => true)).resolves.toBeNull();
  });

  it("reads whether sign-in is held only once the server has answered", async () => {
    let held = false;
    const answer = notFoundMeans(
      1,
      async () => {
        // The answer is what holds it: a held page's `/users/me` opens the dialog.
        held = true;
        return BOB;
      },
      () => held,
    );
    await expect(answer).resolves.toBeNull();
  });

  it("does nothing when it can't tell who is signed in", async () => {
    await expect(
      notFoundMeans(1, async () => Promise.reject(new Error("Network Error")), notHeld),
    ).resolves.toBeNull();
  });

  it("claims no removal when it doesn't know which account the recording belongs to", async () => {
    await expect(notFoundMeans(null, async () => BOB, notHeld)).resolves.toBeNull();
  });
});
