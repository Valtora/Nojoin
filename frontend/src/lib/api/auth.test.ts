import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { logout } from "./auth";
import api from "./client";

// The real axios instance runs on the fetch adapter against a stubbed fetch.
// jsdom cannot navigate, so location is a stand-in that records the target.
const originalAdapter = api.defaults.adapter;

const replace = vi.fn<(url: string) => void>();

describe("logout", () => {
  beforeEach(() => {
    api.defaults.adapter = "fetch";
    replace.mockClear();
    vi.stubGlobal("location", { pathname: "/recordings", replace });
    vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    api.defaults.adapter = originalAdapter;
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("still leaves for the sign-in page when the server cannot be reached", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("Failed to fetch");
      }),
    );

    await logout();

    expect(replace).toHaveBeenCalledWith("/login");
  });
});
