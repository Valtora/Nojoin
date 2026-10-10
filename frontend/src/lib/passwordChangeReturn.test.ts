import { describe, expect, it } from "vitest";

import {
  consentReturnPathFromSearch,
  passwordChangePath,
  safeConsentReturnPath,
} from "./passwordChangeReturn";

const CONSENT =
  "/oauth/authorize?client_id=abc&redirect_uri=https%3A%2F%2Fclaude.ai%2Fapi%2Fmcp%2Fauth_callback&state=a%2Bb&code_challenge=xyz";

describe("safeConsentReturnPath", () => {
  it("accepts the consent page with its query", () => {
    expect(safeConsentReturnPath(CONSENT)).toBe(CONSENT);
  });

  it.each([
    null,
    undefined,
    "",
    "/",
    "/recordings",
    "/settings/profile",
    "/oauth/authorizex?client_id=abc",
    "/oauth/authorize/../settings",
    "https://evil.example/oauth/authorize?client_id=abc",
    "//evil.example/oauth/authorize?client_id=abc",
    "/\\evil.example/oauth/authorize?client_id=abc",
    "javascript:alert(1)",
  ])("rejects %s", (value) => {
    expect(safeConsentReturnPath(value)).toBeNull();
  });
});

describe("passwordChangePath", () => {
  it("carries a consent URL through the password change", () => {
    const path = passwordChangePath(CONSENT);

    expect(path.startsWith("/settings/profile?next=")).toBe(true);
    expect(consentReturnPathFromSearch(path.slice(path.indexOf("?")))).toBe(
      CONSENT,
    );
  });

  it.each([undefined, null, "/recordings", "https://evil.example/"])(
    "carries nothing else (%s)",
    (value) => {
      expect(passwordChangePath(value)).toBe("/settings/profile");
    },
  );
});

describe("consentReturnPathFromSearch", () => {
  it("returns nothing without a next parameter", () => {
    expect(consentReturnPathFromSearch("")).toBeNull();
    expect(consentReturnPathFromSearch("?tab=security")).toBeNull();
  });

  it("refuses a crafted next parameter", () => {
    const search = `?next=${encodeURIComponent("//evil.example/oauth/authorize")}`;

    expect(consentReturnPathFromSearch(search)).toBeNull();
  });
});
