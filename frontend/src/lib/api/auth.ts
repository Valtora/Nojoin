import { noteLegalAcceptance } from "@/lib/legalAcceptance";
import { clearStoredNotes, storedNotesSignedIn } from "@/lib/notes/noteStore";
import { noteSignedIn } from "@/lib/session";
import type { User } from "@/types";
import api, { API_BASE_URL } from "./client";

/** What the sign-in endpoint answers: UI metadata, never the session token. */
export interface LoginResponse {
  force_password_change: boolean;
  is_superuser: boolean;
  username: string;
  /** Whether the account must accept the terms of use first (#1004). */
  legal_acceptance_required?: boolean;
}

/**
 * Records who the browser is signed in as. Once the account's id is known,
 * notes kept in this browser for any other account are removed, so they are
 * never shown or sent as this one (`storedNotesSignedIn`). Whether it must
 * accept the terms of use comes with it.
 */
const signedInAs = (user: {
  username: string;
  force_password_change: boolean;
  id?: number;
  legal_acceptance_required?: boolean;
}) => {
  noteSignedIn(user);
  noteLegalAcceptance(user);
  void storedNotesSignedIn(user.id);
};

export const login = async (
  username: string,
  password: string,
): Promise<LoginResponse> => {
  const formData = new FormData();
  formData.append("username", username);
  formData.append("password", password);

  const response = await api.post<LoginResponse>("/login/session", formData, {
    headers: {
      "Content-Type": "multipart/form-data",
    },
  });
  signedInAs(response.data);
  return response.data;
};

export const logout = async (): Promise<void> => {
  try {
    await api.post("/login/logout");

    } catch (error: unknown) {
    console.error("Logout failed:", error);
  } finally {
    // Notes not saved yet leave the browser with the session.
    await clearStoredNotes();
    if (typeof window !== "undefined") {
      // eslint-disable-next-line @next/next/no-location-assign-relative-destination -- a full reload is required: this runs outside React (no router), and the reload clears the signed-in session's in-memory client state
      window.location.href = "/login";
    }
  }
};

export const getCurrentUser = async (): Promise<User> => {
  const response = await api.get<User>("/users/me");
  signedInAs(response.data);
  return response.data;
};

/** Which sign-in methods the server offers (`GET /login/methods`). */
export interface LoginMethods {
  password: boolean;
  password_policy: "enabled" | "owner_only" | "disabled";
  oidc: { enabled: boolean; display_name: string | null };
}

export const getLoginMethods = async (): Promise<LoginMethods> => {
  const response = await api.get<LoginMethods>("/login/methods");
  return response.data;
};

/**
 * Where a browser goes to sign in through the identity provider. A plain
 * navigation, not a request: the server answers with a redirect to the
 * provider, and the provider sends the browser back to `next`.
 */
export const ssoSignInUrl = (next = "/"): string =>
  `${API_BASE_URL}/login/oidc/start?${new URLSearchParams({ next }).toString()}`;

/**
 * Starts linking the signed-in account to an identity at the provider. A
 * POST, so the trusted-origin check applies; the browser then navigates to
 * the URL it returns.
 */
export const startSsoLink = async (): Promise<string> => {
  const response = await api.post<{ authorization_url: string }>("/login/oidc/link");
  return response.data.authorization_url;
};

const SSO_ERROR_MESSAGES: Record<string, string> = {
  sso_state: "The sign-in took too long or was started in another browser. Please try again.",
  sso_cancelled: "Sign-in was cancelled at the identity provider.",
  sso_not_allowed: "Your account at the identity provider is not allowed to use Nojoin. Ask an administrator for access.",
  sso_no_account: "No Nojoin account is linked to this identity. Ask an administrator to create or link one.",
  sso_setup_required: "Nojoin is not set up yet. Single sign-on works once its owner has finished first-run setup.",
  sso_inactive: "This Nojoin account is deactivated.",
  sso_identity_in_use: "That identity is already linked to another Nojoin account.",
  sso_already_linked: "This account is already linked to another identity.",
  invalid_token: "The identity provider's answer could not be verified. Please try again, or ask an administrator to check the SSO settings.",
  token_exchange_failed: "The identity provider refused the sign-in. Please try again.",
  provider_unreachable: "The identity provider could not be reached. Please try again later.",
  provider_error: "The identity provider is misconfigured. Ask an administrator to check the SSO settings.",
  password_login_disabled: "Password sign-in is turned off for this account. Use single sign-on instead.",
};

/** A readable message for an SSO error code from a redirect's query string. */
export const ssoErrorMessage = (code: string): string =>
  SSO_ERROR_MESSAGES[code] ?? "Single sign-on failed. Please try again.";
