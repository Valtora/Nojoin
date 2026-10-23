import { describe, expect, it } from "vitest";

import { describeCalendarConnectFailure } from "./calendarConnectResult";

describe("describeCalendarConnectFailure", () => {
  it("has nothing to say about a success or a cancelled sign-in", () => {
    expect(describeCalendarConnectFailure("success", "google", null)).toBeNull();
    expect(describeCalendarConnectFailure("cancelled", "microsoft", null)).toBeNull();
    expect(describeCalendarConnectFailure(null, null, null)).toBeNull();
  });

  it("explains a Workspace account with Calendar turned off", () => {
    expect(
      describeCalendarConnectFailure("error", "google", "notACalendarUser"),
    ).toEqual({
      title: "Could not connect Google Calendar",
      message: "Google says this account is not set up for Google Calendar.",
      hint: "If this is a Google Workspace account, an administrator can turn Calendar on for it in the Google Admin console.",
      reasonText: "Reason given by Google: notACalendarUser",
    });
  });

  it("explains a Calendar API the Cloud project has not enabled", () => {
    const failure = describeCalendarConnectFailure(
      "error",
      "google",
      "accessNotConfigured",
    );

    expect(failure?.message).toBe(
      "The Google Calendar API is turned off in the Google Cloud project this installation signs in with.",
    );
  });

  it("explains a Microsoft account without a mailbox", () => {
    const failure = describeCalendarConnectFailure(
      "error",
      "microsoft",
      "MailboxNotEnabledForRESTAPI",
    );

    expect(failure?.message).toBe(
      "Microsoft says this account has no active Exchange Online mailbox, so it has no calendar Nojoin can read.",
    );
  });

  it("shows a code it does not know as the provider gave it", () => {
    expect(
      describeCalendarConnectFailure("error", "google", "somethingNew"),
    ).toEqual({
      title: "Could not connect Google Calendar",
      message: "Google refused the connection.",
      reasonText: "Reason given by Google: somethingNew",
    });
  });

  it("never shows text from the link that is not shaped like a code", () => {
    const failure = describeCalendarConnectFailure(
      "error",
      "google",
      "Call 0000 to restore access",
    );

    expect(failure?.reasonText).toBeUndefined();
    expect(failure?.message).toBe(
      "Something went wrong while connecting. Try again, and if it keeps failing, an administrator can find the cause in Nojoin's logs.",
    );
  });

  it("keeps the Entra fix for a single-tenant app registration", () => {
    const failure = describeCalendarConnectFailure(
      "tenant-config-error",
      "microsoft",
      null,
    );

    expect(failure?.message).toBe(
      "Outlook Calendar sign-in uses the tenant common, but the Microsoft app registration is still single-tenant.",
    );
    expect(failure?.hint).toContain("Supported account types");
  });
});
