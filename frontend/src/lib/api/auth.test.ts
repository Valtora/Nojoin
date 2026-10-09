import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { logout } from "./auth";
import api from "./client";

// The real axios instance runs on the fetch adapter against a stubbed fetch.
// jsdom cannot navigate, so location is a stand-in that logs every
// navigation, whether by href, assign or replace.
const originalAdapter = api.defaults.adapter;

let navigations: string[] = [];

const navigate = (url: string) => {
  navigations.push(url);
};

const serverAnswers = (answer: () => Promise<Response>) => {
  vi.stubGlobal("fetch", vi.fn(answer));
};

describe("logout", () => {
  beforeEach(() => {
    api.defaults.adapter = "fetch";
    navigations = [];
    vi.stubGlobal("location", {
      pathname: "/recordings",
      get href() {
        return "https://nojoin.test/recordings";
      },
      set href(url: string) {
        navigate(url);
      },
      assign: navigate,
      replace: navigate,
    });
    vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    api.defaults.adapter = originalAdapter;
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("leaves for the sign-in page once the server ends the session", async () => {
    serverAnswers(async () => new Response(null, { status: 204 }));

    await logout();

    expect(navigations).toEqual(["/login"]);
  });

  it("still leaves for the sign-in page when the server cannot be reached", async () => {
    serverAnswers(async () => {
      throw new TypeError("Failed to fetch");
    });

    await logout();

    expect(navigations).toEqual(["/login"]);
  });
});
