import axios from "axios";

import { recordRequestOutcome } from "@/lib/connectivity/monitor";
import { describeErrorDetail } from "@/lib/errors";
import { isLegalAcceptanceRefusal, requireLegalAcceptance } from "@/lib/legalAcceptance";
import {
  isSessionEndedError,
  noteSessionRefusal,
  stampSessionRequest,
  useSessionStore,
} from "@/lib/session";

export const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL
  ? `${process.env.NEXT_PUBLIC_API_URL}/v1`
  : "https://localhost:14443/api/v1";
const FIRST_RUN_PASSWORD_AUTH_SCHEME = "Bootstrap";
const FORCE_PASSWORD_CHANGE_REDIRECT = "/settings/profile";

/**
 * Leaves for the sign-in page with a full reload, which clears the signed-in
 * session's in-memory client state.
 */
export const reloadToSignIn = () => {
  // eslint-disable-next-line @next/next/no-location-assign-relative-destination -- a full reload is required: it clears the signed-in session's in-memory client state
  window.location.href = "/login";
};

/**
 * Leaves a page whose sign-in is held in place for the sign-in page, unless a
 * sign-in has come back meanwhile, in this tab or another. The page's leave
 * warning still applies. Returns whether it left.
 */
export const leaveForSignIn = (): boolean => {
  if (!useSessionStore.getState().signInRequired) return false;
  reloadToSignIn();
  return true;
};

// A header value is a string of bytes, so the browser refuses any character
// above U+00FF. Each UTF-8 byte of the value goes in as one character, which
// lets any FIRST_RUN_PASSWORD through; the server compares the bytes.
const toUtf8ByteString = (value: string): string =>
  Array.from(new TextEncoder().encode(value), (byte) =>
    String.fromCharCode(byte),
  ).join("");

// Shared first-run bootstrap header. Onboarding endpoints accept the one-time
// FIRST_RUN_PASSWORD via the Bootstrap auth scheme before a session exists.
export const buildFirstRunRequestConfig = (bootstrapPassword?: string) => {
  if (!bootstrapPassword) {
    return {};
  }

  return {
    headers: {
      Authorization: `${FIRST_RUN_PASSWORD_AUTH_SCHEME} ${toUtf8ByteString(bootstrapPassword)}`,
    },
  };
};

const api = axios.create({
  baseURL: API_BASE_URL,
  maxRedirects: 0,
  withCredentials: true,
  headers: {
    "Content-Type": "application/json",
  },
});

api.interceptors.request.use((config) => stampSessionRequest(config));

api.interceptors.response.use(
  (response) => {
    // Passive health: every real response is authoritative proof the backend
    // is reachable, so the connectivity monitor never needs to guess.
    recordRequestOutcome({ reachedServer: true });
    return response;
  },
  (error) => {
    // Classify the failure mode rather than collapsing everything into "down".
    // A 4xx/5xx still means the server answered; only a missing response or a
    // 502/503/504 gateway error indicates the backend itself is unreachable.
    const status = error.response?.status;
    const gateway = status === 502 || status === 503 || status === 504;
    recordRequestOutcome({
      reachedServer: Boolean(error.response) && !gateway,
      gateway,
    });

    if (
      error.response?.data &&
      typeof error.response.data === "object" &&
      !Array.isArray(error.response.data)
    ) {
      // A request validation error's list becomes its text here, for code
      // that reads the detail itself; getErrorDetail reads any shape.
      const detail = error.response.data.detail;
      if (Array.isArray(detail)) {
        error.response.data.detail = describeErrorDetail(detail) ?? detail;
      }
    }

    // While a capture runs, or notes wait to be saved, a refused session stays
    // on the page and a sign-in dialog asks for the password: a reload would
    // tear the capture down.
    const stayOnPage = isSessionEndedError(error) && noteSessionRefusal(error);

    if (error.response && error.response.status === 401 && !stayOnPage) {
      if (
        typeof window !== "undefined" &&
        !window.location.pathname.includes("/login") &&
        !window.location.pathname.includes("/setup") &&
        !window.location.pathname.includes("/register") &&
        // The OAuth consent page handles its own inline sign-in; a redirect
        // here would discard the client's authorization parameters.
        !window.location.pathname.startsWith("/oauth/authorize")
      ) {
        reloadToSignIn();
      }
    }

    if (
      error.response &&
      error.response.status === 403 &&
      error.response.data?.detail === "Password change required" &&
      !stayOnPage
    ) {
      if (
        typeof window !== "undefined" &&
        !window.location.pathname.startsWith("/settings")
      ) {
        // eslint-disable-next-line @next/next/no-location-assign-relative-destination -- a full reload is required: this runs outside React (no router), and the reload clears the signed-in session's in-memory client state
        window.location.href = FORCE_PASSWORD_CHANGE_REDIRECT;
      }
    }

    // A new capture or import refused until the terms of use are accepted:
    // the app answers with the notice, not an error (#1004).
    if (isLegalAcceptanceRefusal(error)) {
      requireLegalAcceptance();
    }

    return Promise.reject(error);
  },
);

export default api;
