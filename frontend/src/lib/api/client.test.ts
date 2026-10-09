import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import api from "./client";

// The real axios instance and its response interceptor run, on the fetch
// adapter, against a stubbed fetch. jsdom cannot navigate, so location is a
// stand-in that records where the page was sent.
const originalAdapter = api.defaults.adapter;

const replace = vi.fn<(url: string) => void>();

const openPage = (pathname: string) => {
  vi.stubGlobal("location", { pathname, replace });
};

const serverAnswers = (status: number, detail: string) => {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(JSON.stringify({ detail }), {
          status,
          headers: { "content-type": "application/json" },
        }),
    ),
  );
};

describe("api client auth redirects", () => {
  beforeEach(() => {
    api.defaults.adapter = "fetch";
    replace.mockClear();
  });

  afterEach(() => {
    api.defaults.adapter = originalAdapter;
    vi.unstubAllGlobals();
  });

  it("sends a 401 on an app page to the sign-in page", async () => {
    openPage("/recordings/42");
    serverAnswers(401, "Not authenticated");

    await expect(api.get("/users/me")).rejects.toMatchObject({
      response: { status: 401 },
    });

    expect(replace).toHaveBeenCalledWith("/login");
  });

  it.each(["/login", "/setup", "/register", "/oauth/authorize"])(
    "leaves a 401 on %s to the page itself",
    async (pathname) => {
      openPage(pathname);
      serverAnswers(401, "Not authenticated");

      await expect(api.get("/users/me")).rejects.toMatchObject({
        response: { status: 401 },
      });

      expect(replace).not.toHaveBeenCalled();
    },
  );

  it("sends a pending password change to the profile settings", async () => {
    openPage("/recordings");
    serverAnswers(403, "Password change required");

    await expect(api.get("/recordings")).rejects.toMatchObject({
      response: { status: 403 },
    });

    expect(replace).toHaveBeenCalledWith("/settings/profile");
  });

  it("stays on the settings pages for a pending password change", async () => {
    openPage("/settings/profile");
    serverAnswers(403, "Password change required");

    await expect(api.get("/settings")).rejects.toMatchObject({
      response: { status: 403 },
    });

    expect(replace).not.toHaveBeenCalled();
  });

  it("does not redirect on any other 403", async () => {
    openPage("/recordings");
    serverAnswers(403, "Not enough permissions");

    await expect(api.get("/system/health")).rejects.toMatchObject({
      response: { status: 403 },
    });

    expect(replace).not.toHaveBeenCalled();
  });
});
