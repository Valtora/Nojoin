import { create } from "zustand";

import { DEMO_MODE } from "@/lib/demo";
import { getErrorStatus, isRecord } from "@/lib/errors";
import { useSessionStore } from "@/lib/session";

/**
 * Whether this browser's account must accept the terms of use before it can
 * use the app (#1004). The server decides: `/users/me` and the sign-in answer
 * carry `legal_acceptance_required`, and a new capture or import refused for
 * it answers 403 with the code below. `AuthGuard` shows the notice while this
 * is set.
 *
 * A pending password change comes first. The API refuses the acceptance until
 * a temporary password is replaced, so the terms are accepted by the person
 * who chose the account's password, not under one an administrator knows.
 *
 * The public demo never asks: its fixture API refuses every write, the
 * acceptance included, so a visitor could not get past the notice.
 */

/** The `code` in the detail of the API's refusal of a new capture or import. */
export const LEGAL_ACCEPTANCE_REQUIRED_CODE = "legal_acceptance_required";

interface LegalAcceptanceState {
  required: boolean;
}

export const useLegalAcceptanceStore = create<LegalAcceptanceState>(() => ({
  required: false,
}));

const setRequired = (required: boolean) => {
  const next = required && !DEMO_MODE;
  if (useLegalAcceptanceStore.getState().required !== next) {
    useLegalAcceptanceStore.setState({ required: next });
  }
};

/** True for the API's refusal of a new capture or import before acceptance. */
export const isLegalAcceptanceRefusal = (error: unknown): boolean => {
  if (getErrorStatus(error) !== 403 || !isRecord(error) || !isRecord(error.response)) {
    return false;
  }
  const data = error.response.data;
  return (
    isRecord(data) &&
    isRecord(data.detail) &&
    data.detail.code === LEGAL_ACCEPTANCE_REQUIRED_CODE
  );
};

/**
 * Records what the server said about the signed-in account: its `/users/me`,
 * its sign-in answer or the user the acceptance returned. An answer without
 * the field, from a server older than the notice, asks nothing.
 */
export const noteLegalAcceptance = (user: {
  force_password_change: boolean;
  legal_acceptance_required?: boolean;
}) => {
  setRequired(Boolean(user.legal_acceptance_required) && !user.force_password_change);
};

/** The server refused a new capture or import until the terms are accepted. */
export const requireLegalAcceptance = () => setRequired(true);

/**
 * What to tell a user whose capture or import was refused for the terms, or
 * null when the notice says it. The notice waits while notes not saved yet
 * hold the page, and the user would otherwise see nothing happen.
 */
export const legalRefusalMessage = (action: "record" | "import"): string | null =>
  useSessionStore.getState().heldInPlace
    ? `Accept the terms of use to ${action === "record" ? "start a recording" : "import audio"}. Nojoin shows them once your notes are saved.`
    : null;

/** Restores the initial state. For tests. */
export const resetLegalAcceptanceForTests = () => {
  useLegalAcceptanceStore.setState({ required: false });
};
