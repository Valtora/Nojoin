// A user who must change their password is sent to /settings/profile from
// wherever they were. When that was the OAuth consent page, the consent URL
// rides along as ?next= so the MCP client still gets its redirect afterwards.
// Only the consent page is accepted as a return target, so a crafted ?next=
// cannot become an open redirect: the worst it can do is show a consent screen,
// which still asks the user to approve.

const PASSWORD_CHANGE_PATH = "/settings/profile";
const CONSENT_PATH = "/oauth/authorize";
// Resolving against a placeholder origin keeps the check free of `window`.
// Anything that parses to another origin ("//host", "/\\host", an absolute URL)
// fails the origin comparison.
const PLACEHOLDER_ORIGIN = "https://nojoin.invalid";

export function safeConsentReturnPath(
  value: string | null | undefined,
): string | null {
  if (!value || !value.startsWith("/")) {
    return null;
  }
  let url: URL;
  try {
    url = new URL(value, PLACEHOLDER_ORIGIN);
  } catch {
    return null;
  }
  if (url.origin !== PLACEHOLDER_ORIGIN || url.pathname !== CONSENT_PATH) {
    return null;
  }
  return `${url.pathname}${url.search}`;
}

export function passwordChangePath(returnTo?: string | null): string {
  const safe = safeConsentReturnPath(returnTo);
  return safe
    ? `${PASSWORD_CHANGE_PATH}?next=${encodeURIComponent(safe)}`
    : PASSWORD_CHANGE_PATH;
}

export function consentReturnPathFromSearch(search: string): string | null {
  return safeConsentReturnPath(new URLSearchParams(search).get("next"));
}
