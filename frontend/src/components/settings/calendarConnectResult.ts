/**
 * What the Calendars card says when a calendar sign-in comes back.
 *
 * The OAuth callback redirects to Settings > Integrations with `calendar` (the
 * outcome), `provider` and, for a failure the provider explained, `reason`: the
 * provider's own error code, such as Google's `notACalendarUser` or Graph's
 * `ErrorAccessDenied`. Known codes get a plain-words explanation; any code is
 * also shown as given, so an administrator can look it up.
 */

import type { CalendarProvider } from "@/types";

export interface CalendarConnectFailure {
  title: string;
  message: string;
  hint?: string;
  /** "Reason given by Google: forbidden", when the provider gave a code. */
  reasonText?: string;
}

type ReasonKind =
  | "calendar-off"
  | "api-disabled"
  | "scope-missing"
  | "refused"
  | "admin-blocked"
  | "no-mailbox"
  | "credentials"
  | "expired";

const PROVIDER_NAMES: Record<CalendarProvider, string> = {
  google: "Google",
  microsoft: "Microsoft",
};

export const CALENDAR_CONNECT_LABELS: Record<CalendarProvider, string> = {
  google: "Google Calendar",
  microsoft: "Outlook Calendar",
};

function isCalendarProvider(value: string): value is CalendarProvider {
  return value === "google" || value === "microsoft";
}

// The shape the API lets through (PROVIDER_REASON_PATTERN in
// backend/services/calendar_service/provider_errors.py). The page is reachable
// by any link, so a value of another shape is not shown.
const PROVIDER_REASON = /^[A-Za-z][A-Za-z0-9_.-]{0,63}$/;

// Keyed in lower case: Google's reasons are camelCase, Graph's codes PascalCase
// and OAuth's snake_case, and a provider is not always consistent about it.
const REASON_KINDS: Partial<Record<string, ReasonKind>> = {
  notacalendaruser: "calendar-off",
  accessnotconfigured: "api-disabled",
  service_disabled: "api-disabled",
  insufficientpermissions: "scope-missing",
  access_token_scope_insufficient: "scope-missing",
  forbidden: "refused",
  permission_denied: "refused",
  accessdenied: "refused",
  erroraccessdenied: "refused",
  authorization_requestdenied: "refused",
  admin_policy_enforced: "admin-blocked",
  mailboxnotenabledforrestapi: "no-mailbox",
  invalid_client: "credentials",
  unauthorized_client: "credentials",
  invalid_grant: "expired",
};

const GOOGLE_WORKSPACE_HINT =
  "If this is a Google Workspace account, an administrator may have turned Calendar off for you, or restricted third-party apps' access to the Calendar API, in the Google Admin console.";

const MICROSOFT_ORGANISATION_HINT =
  "In a Microsoft 365 organisation, an administrator may need to approve the app, or a policy may be blocking it.";

function explainReason(
  kind: ReasonKind | undefined,
  provider: CalendarProvider,
): Pick<CalendarConnectFailure, "message" | "hint"> {
  const google = provider === "google";
  const providerName = PROVIDER_NAMES[provider];
  switch (kind) {
    case "calendar-off":
      return {
        message: "Google says this account is not set up for Google Calendar.",
        hint: "If this is a Google Workspace account, an administrator can turn Calendar on for it in the Google Admin console.",
      };
    case "api-disabled":
      return {
        message:
          "The Google Calendar API is turned off in the Google Cloud project this installation signs in with.",
        hint: "An administrator can enable it in Google Cloud Console, under APIs and services.",
      };
    case "scope-missing":
      return {
        message: `${providerName} did not give Nojoin permission to read your calendars. Connect again and allow calendar access when ${providerName} asks.`,
        hint: google
          ? "If it keeps happening on a Google Workspace account, an administrator may be restricting third-party apps' access to the Calendar API in the Google Admin console."
          : undefined,
      };
    case "refused":
      return {
        message: `${providerName} refused Nojoin access to this account's calendars.`,
        hint: google ? GOOGLE_WORKSPACE_HINT : MICROSOFT_ORGANISATION_HINT,
      };
    case "admin-blocked":
      return {
        message:
          "Your Google Workspace administrator does not allow this app to access your account.",
        hint: "An administrator can allow it in the Google Admin console, under API controls.",
      };
    case "no-mailbox":
      return {
        message:
          "Microsoft says this account has no active Exchange Online mailbox, so it has no calendar Nojoin can read.",
        hint: "A Microsoft 365 administrator can check the account's Exchange Online licence.",
      };
    case "credentials":
      return {
        message: `${providerName} did not accept this installation's sign-in app credentials.`,
        hint: "An administrator can check the client ID and secret in Settings > Integrations, under Advanced.",
      };
    case "expired":
      return {
        message: "The sign-in expired before Nojoin could finish it. Connect again.",
      };
    default:
      return { message: `${providerName} refused the connection.` };
  }
}

/**
 * The failure to show for a callback outcome, or null for success, a
 * cancelled sign-in, or no outcome at all.
 */
export function describeCalendarConnectFailure(
  status: string | null,
  provider: string | null,
  reason: string | null,
): CalendarConnectFailure | null {
  if (!status || !provider || status === "success" || status === "cancelled") {
    return null;
  }

  const knownProvider = isCalendarProvider(provider) ? provider : null;
  const label = knownProvider ? CALENDAR_CONNECT_LABELS[knownProvider] : "your calendar";
  const title = `Could not connect ${label}`;

  if (status === "config-error") {
    return {
      title,
      message: `${label} sign-in is not set up yet. An administrator must add this installation's app credentials first.`,
    };
  }
  if (status === "tenant-config-error") {
    return {
      title,
      message: `${label} sign-in uses the tenant common, but the Microsoft app registration is still single-tenant.`,
      hint: "In Entra, change Supported account types to include organisational directories and personal Microsoft accounts, or enter a specific tenant ID instead of common.",
    };
  }
  if (!reason || !PROVIDER_REASON.test(reason) || !knownProvider) {
    return {
      title,
      message:
        "Something went wrong while connecting. Try again, and if it keeps failing, an administrator can find the cause in Nojoin's logs.",
    };
  }

  return {
    title,
    ...explainReason(REASON_KINDS[reason.toLowerCase()], knownProvider),
    reasonText: `Reason given by ${PROVIDER_NAMES[knownProvider]}: ${reason}`,
  };
}
