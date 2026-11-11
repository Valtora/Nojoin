// @vitest-environment node
import { afterEach, describe, expect, it } from "vitest";

import {
  isLegalAcceptanceRefusal,
  legalRefusalMessage,
  noteLegalAcceptance,
  requireLegalAcceptance,
  resetLegalAcceptanceForTests,
  useLegalAcceptanceStore,
} from "./legalAcceptance";
import { holdSignInInPlace, resetSessionForTests } from "./session";

const refusal = (status: number, detail: unknown) => ({
  response: { status, data: { detail } },
});

afterEach(() => {
  resetLegalAcceptanceForTests();
  resetSessionForTests();
});

describe("legal acceptance (#1004)", () => {
  it("recognises the API's refusal by its code alone", () => {
    expect(
      isLegalAcceptanceRefusal(
        refusal(403, { code: "legal_acceptance_required", message: "Accept first." }),
      ),
    ).toBe(true);
    expect(isLegalAcceptanceRefusal(refusal(403, "Password change required"))).toBe(false);
    expect(
      isLegalAcceptanceRefusal(refusal(409, { code: "legal_acceptance_required" })),
    ).toBe(false);
    expect(
      isLegalAcceptanceRefusal(refusal(403, { code: "active_recording_exists" })),
    ).toBe(false);
    expect(isLegalAcceptanceRefusal(new Error("Network Error"))).toBe(false);
  });

  it("follows what the server says about the account", () => {
    noteLegalAcceptance({ force_password_change: false, legal_acceptance_required: true });
    expect(useLegalAcceptanceStore.getState().required).toBe(true);

    noteLegalAcceptance({ force_password_change: false, legal_acceptance_required: false });
    expect(useLegalAcceptanceStore.getState().required).toBe(false);
  });

  it("waits for a pending password change", () => {
    noteLegalAcceptance({ force_password_change: true, legal_acceptance_required: true });

    expect(useLegalAcceptanceStore.getState().required).toBe(false);
  });

  it("asks nothing of a server too old to say", () => {
    requireLegalAcceptance();
    noteLegalAcceptance({ force_password_change: false });

    expect(useLegalAcceptanceStore.getState().required).toBe(false);
  });

  it("explains a refusal only while something holds the page", () => {
    expect(legalRefusalMessage("record")).toBeNull();

    holdSignInInPlace(true, "notes");

    expect(legalRefusalMessage("record")).toMatch(/start a recording/);
    expect(legalRefusalMessage("import")).toMatch(/import audio/);
  });
});
