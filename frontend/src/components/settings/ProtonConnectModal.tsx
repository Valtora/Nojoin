"use client";

import { useEffect, useState, type FormEvent } from "react";
import { CheckCircle2, ExternalLink, ShieldAlert } from "lucide-react";

import Button from "@/components/ui/Button";
import Input from "@/components/ui/Input";
import Modal from "@/components/ui/Modal";
import {
  connectProton,
  connectProtonTwoFactor,
  connectProtonUnlock,
  getCalendarOverview,
} from "@/lib/api";
import { getErrorMessage } from "@/lib/errors";
import type {
  CalendarConnection,
  ProtonConnectRefusal,
  ProtonConnectStep,
} from "@/types";

import CalendarSyncProgress from "./CalendarSyncProgress";
import SettingsCallout from "./SettingsCallout";

/** How often the dialog reads the first sync's progress. */
export const SYNC_POLL_MS = 2000;

type Stage =
  | "disclosure"
  | "sign_in"
  | "verification"
  | "two_factor"
  | "mailbox_password"
  | "connected";

interface ProtonConnectModalProps {
  open: boolean;
  onClose: () => void;
  onConnected: (connection: CalendarConnection) => void;
  /** Each fresh read of the connection while its first sync runs. */
  onUpdated?: (connection: CalendarConnection) => void;
  /** The account being reconnected, filled into the address field. */
  reconnectAddress?: string | null;
}

/** The refusal a sign-in step answered with, when the API sent one. */
function refusalOf(error: unknown): ProtonConnectRefusal | null {
  if (typeof error !== "object" || error === null || !("response" in error)) {
    return null;
  }
  const response = (error as { response?: { data?: { detail?: unknown } } }).response;
  const detail = response?.data?.detail;
  if (typeof detail === "object" && detail !== null && "code" in detail) {
    return detail as ProtonConnectRefusal;
  }
  return null;
}

/**
 * Connecting Proton Calendar: a disclosure first, then Proton's own sign-in
 * steps as Proton asks for them. The password stays in this component's
 * memory, because the key passphrase can only be derived once two-factor is
 * done; Nojoin never stores it.
 */
export default function ProtonConnectModal({
  open,
  onClose,
  onConnected,
  onUpdated,
  reconnectAddress,
}: ProtonConnectModalProps) {
  const [stage, setStage] = useState<Stage>("disclosure");
  const [understood, setUnderstood] = useState(false);
  const [username, setUsername] = useState(reconnectAddress ?? "");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [mailboxPassword, setMailboxPassword] = useState("");
  const [flowId, setFlowId] = useState<string | null>(null);
  const [verification, setVerification] = useState<ProtonConnectRefusal | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [connection, setConnection] = useState<CalendarConnection | null>(null);
  const syncing = stage === "connected" && connection?.sync_status === "syncing";

  // The first sync runs on the server; the dialog only watches it, so
  // closing it never stops the sync.
  useEffect(() => {
    if (!syncing || !connection) {
      return;
    }
    const id = connection.id;
    const timer = window.setInterval(() => {
      getCalendarOverview()
        .then((overview) => {
          const latest = overview.connections.find((item) => item.id === id);
          if (latest) {
            setConnection(latest);
            onUpdated?.(latest);
          }
        })
        .catch(() => undefined);
    }, SYNC_POLL_MS);
    return () => window.clearInterval(timer);
  }, [syncing, connection, onUpdated]);

  const close = () => {
    setStage("disclosure");
    setUnderstood(false);
    setPassword("");
    setCode("");
    setMailboxPassword("");
    setFlowId(null);
    setVerification(null);
    setError(null);
    setConnection(null);
    onClose();
  };

  const follow = (step: ProtonConnectStep) => {
    setError(null);
    if (step.step === "done" && step.connection) {
      setPassword("");
      setMailboxPassword("");
      setConnection(step.connection);
      setStage("connected");
      onConnected(step.connection);
      return;
    }
    setFlowId(step.flow_id ?? null);
    setStage(step.step === "two_factor" ? "two_factor" : "mailbox_password");
  };

  const run = async (action: () => Promise<ProtonConnectStep>) => {
    setBusy(true);
    setError(null);
    try {
      follow(await action());
    } catch (caught: unknown) {
      const refusal = refusalOf(caught);
      if (refusal?.code === "human_verification_required") {
        setVerification(refusal);
        setStage("verification");
      } else if (refusal?.code === "flow_expired") {
        setStage("sign_in");
        setError(refusal.message);
      } else {
        setError(refusal?.message ?? getErrorMessage(caught, "Could not connect to Proton."));
      }
    } finally {
      setBusy(false);
    }
  };

  const credentials = { username: username.trim(), password };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (stage === "sign_in") {
      void run(() => connectProton(credentials));
    } else if (stage === "verification" && verification?.token) {
      const token = verification.token;
      void run(() => connectProton(credentials, { token, type: "captcha" }));
    } else if (stage === "two_factor" && flowId) {
      void run(() => connectProtonTwoFactor(flowId, credentials, code.trim()));
    } else if (stage === "mailbox_password" && flowId) {
      void run(() => connectProtonUnlock(flowId, credentials, mailboxPassword));
    }
  };

  const formId = "proton-connect-form";
  const primaryLabel =
    stage === "disclosure"
      ? "Continue"
      : stage === "verification"
        ? "I've finished the check"
        : "Connect";

  return (
    <Modal
      open={open}
      onClose={close}
      size="md"
      title="Connect Proton Calendar"
      description="An unofficial connection, through Proton's private API."
      footer={
        <>
          {stage !== "connected" && (
            <Button variant="ghost" onClick={close}>
              Cancel
            </Button>
          )}
          {stage === "connected" ? (
            <Button variant="primary" onClick={close}>
              {syncing ? "Continue in background" : "Done"}
            </Button>
          ) : stage === "disclosure" ? (
            <Button
              variant="primary"
              disabled={!understood}
              onClick={() => setStage("sign_in")}
            >
              {primaryLabel}
            </Button>
          ) : (
            <Button
              variant="primary"
              type="submit"
              form={formId}
              loading={busy}
              disabled={stage === "verification" && !verification?.token}
            >
              {primaryLabel}
            </Button>
          )}
        </>
      }
    >
      {stage === "connected" && connection ? (
        <div className="space-y-4 text-sm">
          <SettingsCallout tone="success" title="Connected">
            <span className="flex items-start gap-2">
              <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
              <span>{connection.email || connection.display_name || "Your Proton account"}</span>
            </span>
          </SettingsCallout>
          {syncing ? (
            <>
              <CalendarSyncProgress connection={connection} />
              <p className="text-xs contrast-helper">
                The first sync carries on if you close this. The Calendars card shows its
                progress.
              </p>
            </>
          ) : connection.sync_status === "error" ||
            connection.sync_status === "reauthorisation_required" ? (
            <div role="alert">
              <SettingsCallout tone="error" title="Needs attention">
                {connection.sync_error || "The first sync did not finish."}
              </SettingsCallout>
            </div>
          ) : (
            <p className="text-contrast-muted">
              {connection.sync_status === "success"
                ? "The first sync has finished. Your events are on the dashboard."
                : "Your calendars will sync with the next scheduled sync."}
            </p>
          )}
        </div>
      ) : stage === "disclosure" ? (
        <div className="space-y-3 text-sm text-contrast-muted">
          <SettingsCallout tone="warning" title="Before you connect">
            <ul className="list-disc space-y-1.5 pl-4">
              <li>
                Proton offers no public calendar API. Nojoin uses the private one
                Proton&apos;s own apps use, which can change or stop working without
                notice.
              </li>
              <li>
                Proton&apos;s terms (clause 2.10) allow automated access only while it
                looks like a person using Proton&apos;s apps, and let Proton block it.
                This connection could lead Proton to restrict your account.
              </li>
              <li>
                Nojoin keeps your Proton session and the key passphrase derived from
                your password, encrypted. That passphrase opens every key on your
                account, so it could also decrypt your Proton Mail.
              </li>
              <li>
                Synced events are stored in Nojoin without Proton&apos;s encryption,
                as Google and Outlook events are. Your password, mailbox password and
                two-factor code are never stored.
              </li>
            </ul>
          </SettingsCallout>
          <label className="flex items-start gap-2 text-foreground">
            <input
              type="checkbox"
              className="mt-1"
              checked={understood}
              onChange={(event) => setUnderstood(event.target.checked)}
            />
            <span>I understand, and I want to connect my Proton account.</span>
          </label>
        </div>
      ) : (
        <form id={formId} onSubmit={submit} className="space-y-4">
          {error && (
            <div role="alert">
              <SettingsCallout tone="error">
                <span className="flex items-start gap-2">
                  <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
                  <span>{error}</span>
                </span>
              </SettingsCallout>
            </div>
          )}

          {(stage === "sign_in" || stage === "verification") && (
            <>
              <Input
                label="Proton address or username"
                autoComplete="username"
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                required
                autoFocus
              />
              <Input
                label="Password"
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                required
              />
            </>
          )}

          {stage === "verification" && (
            <SettingsCallout tone="info" title="Proton wants to check that you are a person">
              {verification?.verify_url ? (
                <>
                  <p>Complete the check in a new tab, then come back here.</p>
                  <a
                    href={verification.verify_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="mt-2 inline-flex items-center gap-1 font-semibold underline underline-offset-2"
                  >
                    Open Proton&apos;s check
                    <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
                  </a>
                </>
              ) : (
                <p>
                  Proton asked for a check Nojoin cannot show. Sign in to Proton&apos;s
                  web app from this network first, then try again.
                </p>
              )}
            </SettingsCallout>
          )}

          {stage === "two_factor" && (
            <Input
              label="Two-factor code"
              hint="The six-digit code from your authenticator app."
              autoComplete="one-time-code"
              inputMode="numeric"
              value={code}
              onChange={(event) => setCode(event.target.value)}
              required
              autoFocus
            />
          )}

          {stage === "mailbox_password" && (
            <Input
              label="Mailbox password"
              hint="Your account uses a second password to open its mailbox."
              type="password"
              autoComplete="current-password"
              value={mailboxPassword}
              onChange={(event) => setMailboxPassword(event.target.value)}
              required
              autoFocus
            />
          )}
        </form>
      )}
    </Modal>
  );
}
