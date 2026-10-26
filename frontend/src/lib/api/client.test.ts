import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  holdSignInInPlace,
  noteSignedIn,
  resetSessionForTests,
  useSessionStore,
} from "@/lib/session";

import api, { buildFirstRunRequestConfig } from "./client";

// The real axios instance and its response interceptor run, on the fetch
// adapter, against a stubbed fetch. jsdom cannot navigate, so location is a
// stand-in that logs every navigation, whether by href, assign or replace.
const originalAdapter = api.defaults.adapter;

let navigations: string[] = [];

const openPage = (pathname: string) => {
  const navigate = (url: string) => {
    navigations.push(url);
  };
  vi.stubGlobal("location", {
    pathname,
    get href() {
      return `https://nojoin.test${pathname}`;
    },
    set href(url: string) {
      navigate(url);
    },
    assign: navigate,
    replace: navigate,
  });
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
    navigations = [];
    resetSessionForTests();
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

    expect(navigations).toEqual(["/login"]);
  });

  it.each(["/login", "/setup", "/register", "/oauth/authorize"])(
    "leaves a 401 on %s to the page itself",
    async (pathname) => {
      openPage(pathname);
      serverAnswers(401, "Not authenticated");

      await expect(api.get("/users/me")).rejects.toMatchObject({
        response: { status: 401 },
      });

      expect(navigations).toEqual([]);
    },
  );

  it("sends a pending password change to the profile settings", async () => {
    openPage("/recordings");
    serverAnswers(403, "Password change required");

    await expect(api.get("/recordings")).rejects.toMatchObject({
      response: { status: 403 },
    });

    expect(navigations).toEqual(["/settings/profile"]);
  });

  it("stays on the settings pages for a pending password change", async () => {
    openPage("/settings/profile");
    serverAnswers(403, "Password change required");

    await expect(api.get("/settings")).rejects.toMatchObject({
      response: { status: 403 },
    });

    expect(navigations).toEqual([]);
  });

  it("does not redirect on any other 403", async () => {
    openPage("/recordings");
    serverAnswers(403, "Not enough permissions");

    await expect(api.get("/system/health")).rejects.toMatchObject({
      response: { status: 403 },
    });

    expect(navigations).toEqual([]);
  });
});

describe("api client while a capture holds sign-in in place", () => {
  beforeEach(() => {
    api.defaults.adapter = "fetch";
    navigations = [];
    resetSessionForTests();
    noteSignedIn({ username: "alice", force_password_change: false });
  });

  afterEach(() => {
    api.defaults.adapter = originalAdapter;
    vi.unstubAllGlobals();
  });

  it("stays on the page through a 401 and asks for sign-in", async () => {
    openPage("/recordings/42");
    holdSignInInPlace(true);
    serverAnswers(401, "Not authenticated");

    await expect(api.get("/recordings/42")).rejects.toMatchObject({
      response: { status: 401 },
    });

    expect(navigations).toEqual([]);
    expect(useSessionStore.getState().signInRequired).toBe(true);
  });

  it("asks for sign-in once when several requests are refused together", async () => {
    openPage("/recordings/42");
    holdSignInInPlace(true);
    serverAnswers(401, "Not authenticated");
    const changes: boolean[] = [];
    const unsubscribe = useSessionStore.subscribe((state) => {
      changes.push(state.signInRequired);
    });

    const results = await Promise.allSettled([
      api.post("/recordings/42/segment?sequence=7"),
      api.get("/recordings/42"),
      api.get("/users/me"),
    ]);
    unsubscribe();

    expect(results.map((result) => result.status)).toEqual([
      "rejected",
      "rejected",
      "rejected",
    ]);
    expect(navigations).toEqual([]);
    expect(changes).toEqual([true]);
  });

  it("stays on the page through a pending password change", async () => {
    openPage("/recordings/42");
    holdSignInInPlace(true);
    serverAnswers(403, "Password change required");

    await expect(api.get("/recordings/42")).rejects.toMatchObject({
      response: { status: 403 },
    });

    expect(navigations).toEqual([]);
    expect(useSessionStore.getState().signInRequired).toBe(true);
  });

  it("leaves for the sign-in page again once the capture lets go", async () => {
    openPage("/recordings/42");
    holdSignInInPlace(true);
    holdSignInInPlace(false);
    serverAnswers(401, "Not authenticated");

    await expect(api.get("/recordings/42")).rejects.toMatchObject({
      response: { status: 401 },
    });

    expect(navigations).toEqual(["/login"]);
  });

  it("ignores a 401 for a request sent before the user signed back in", async () => {
    openPage("/recordings/42");
    useSessionStore.setState({ signInRequired: true });
    let answer = () => {};
    vi.stubGlobal(
      "fetch",
      vi.fn(
        () =>
          new Promise<Response>((resolve) => {
            answer = () =>
              resolve(
                new Response(JSON.stringify({ detail: "Not authenticated" }), {
                  status: 401,
                  headers: { "content-type": "application/json" },
                }),
              );
          }),
      ),
    );

    const request = api.get("/recordings/42");
    await vi.waitFor(() => expect(fetch).toHaveBeenCalled());
    noteSignedIn({ username: "alice", force_password_change: false });
    answer();

    await expect(request).rejects.toMatchObject({ response: { status: 401 } });
    expect(navigations).toEqual([]);
    expect(useSessionStore.getState().signInRequired).toBe(false);
  });
});

describe("first-run bootstrap header", () => {
  it("sends a password beyond Latin-1 as its UTF-8 bytes", () => {
    const config = buildFirstRunRequestConfig("pass-€-à");

    // A header value with any character above U+00FF is refused outright.
    expect(() => new Headers(config.headers)).not.toThrow();
    expect(config.headers?.Authorization).toBe(
      "Bootstrap pass-\u00e2\u0082\u00ac-\u00c3\u00a0",
    );
  });
});
