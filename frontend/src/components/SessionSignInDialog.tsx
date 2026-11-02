"use client";

import { LogIn, Square } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";

import { getCurrentUser, login } from "@/lib/api";
import { leaveForSignIn } from "@/lib/api/client";
import { useCapture, useCaptureState } from "@/lib/capture/CaptureProvider";
import { getErrorMessage } from "@/lib/errors";
import { useSessionStore } from "@/lib/session";
import { useOnDepsChange } from "@/lib/useOnDepsChange";

import Button from "./ui/Button";
import Input from "./ui/Input";
import Modal from "./ui/Modal";

/**
 * How often an open dialog asks the server whether the session is back. Tabs
 * share the session cookie, so signing in from another tab restores this one,
 * and this is how it finds out when it is not the tab in front.
 */
export const SESSION_PROBE_INTERVAL_MS = 15_000;

const FORM_ID = "session-sign-in-form";

function formatQueuedAudio(seconds: number) {
  const whole = Math.max(0, Math.round(seconds));
  const minutes = Math.floor(whole / 60);
  const rest = whole % 60;
  if (minutes === 0) {
    return `${rest} s`;
  }
  return rest === 0 ? `${minutes} min` : `${minutes} min ${rest} s`;
}

function situation(status: string) {
  if (status === "finalizing") {
    return "Your sign-in ended while this recording was stopping. Recording has stopped, and the rest of the audio uploads and the recording is processed once you sign in.";
  }
  if (status === "paused") {
    return "Your sign-in ended while this recording was paused. Sign in to carry on with it.";
  }
  return "Your sign-in ended while this meeting was recording. Nojoin is still recording, and the audio waits in this browser until you sign in.";
}

/** The dialog's words for what is waiting: a capture's audio, or notes alone. */
const CAPTURE_WORDING = {
  title: "Sign in to keep uploading",
  owned: "The recording belongs to",
  carriesOn: "it carries on",
  blocked: "uploads cannot continue",
};
const NOTES_WORDING = {
  title: "Sign in to save your notes",
  owned: "The notes belong to",
  carriesOn: "they save",
  blocked: "your notes cannot save",
};
const NOTES_SITUATION =
  "Your sign-in ended with notes on this page not saved yet. They stay on this page and save once you sign in.";
const NOTES_LEAVING =
  "Going to the sign-in page leaves this page, and your browser asks first where it supports that. Signing in there as a different account removes the notes kept in this browser.";

/**
 * Asks for the password in place when the session ends during a capture, or
 * while the page holds notes not saved yet.
 *
 * Going to the sign-in page would reload the tab and end the capture, so the
 * capture holds sign-in in place and this dialog stands in for that page, and
 * notes not saved yet hold it the same way. It signs in through the same
 * session endpoint, and only as the account that held it, since the
 * recording's uploads and the notes belong to it.
 *
 * The dialog covers the page's own capture controls, so it offers Stop itself.
 * Stopping needs no session until the upload: the recorder stops and the
 * microphone and shared tab are released at once, and the rest waits for the
 * sign-in. Pause and discard both need the server, so they wait until the
 * user has signed in and the page's controls are back.
 */
export default function SessionSignInDialog() {
  const open = useSessionStore((state) => state.signInRequired && state.heldInPlace);
  const knownUsername = useSessionStore((state) => state.username);
  const otherUsername = useSessionStore((state) => state.otherUsername);
  const heldBy = useSessionStore((state) => state.heldBy);
  const forCapture = heldBy.includes("capture");
  const wording = forCapture ? CAPTURE_WORDING : NOTES_WORDING;
  const { runtimeActive, status, stop } = useCapture();
  const uploadQueue = useCaptureState((state) => state.uploadQueue);
  const [typedUsername, setTypedUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [passwordReset, setPasswordReset] = useState(false);
  const [signingIn, setSigningIn] = useState(false);

  useOnDepsChange([open], () => {
    setTypedUsername("");
    setPassword("");
    setError(null);
    setPasswordReset(false);
  });

  useEffect(() => {
    if (!open) {
      return;
    }

    const probe = () => {
      // A success as the recording's account marks the session back and
      // closes the dialog; any other answer changes nothing.
      getCurrentUser().catch(() => {});
    };
    const probeWhenVisible = () => {
      if (document.visibilityState === "visible") {
        probe();
      }
    };

    const interval = window.setInterval(probe, SESSION_PROBE_INTERVAL_MS);
    window.addEventListener("focus", probe);
    document.addEventListener("visibilitychange", probeWhenVisible);
    return () => {
      window.clearInterval(interval);
      window.removeEventListener("focus", probe);
      document.removeEventListener("visibilitychange", probeWhenVisible);
    };
  }, [open]);

  const username = knownUsername ?? typedUsername;
  const canStop = runtimeActive && (status === "recording" || status === "paused");

  const handleStop = () => {
    // A failed stop reports itself through the capture's own error, and the
    // recording settles as paused so it can be stopped again.
    stop().catch(() => {});
  };

  // The reload goes through the leave warning, which the notes not saved yet
  // keep armed; cancelled, the page and this dialog stay as they are.
  const handleLeave = () => {
    leaveForSignIn();
  };

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    setSigningIn(true);
    try {
      const response = await login(username, password);
      // A reset password does not count: uploads stay refused until a new one
      // is chosen.
      if (response.force_password_change) {
        setPasswordReset(true);
        setPassword("");
        return;
      }
      // The sign-in counts once the server names the account by id, so a new
      // account under the old one's name never takes its place. That marks
      // the session back, which closes this dialog and lets everything
      // waiting go.
      await getCurrentUser();
    } catch (signInError: unknown) {
      setError(getErrorMessage(signInError, "Sign-in failed. Please try again."));
    } finally {
      setSigningIn(false);
    }
  };

  return (
    <Modal
      open={open}
      // Everything waiting on the session waits on this dialog, so it can't be
      // dismissed. Notes alone may leave for the sign-in page (an account
      // renamed meanwhile can't sign in here); a capture can't, as the reload
      // would end it.
      onClose={() => {}}
      dismissible={false}
      hideCloseButton
      title={wording.title}
      footer={
        <>
          {forCapture ? null : (
            <Button variant="secondary" onClick={handleLeave} disabled={signingIn}>
              Go to the sign-in page
            </Button>
          )}
          {canStop ? (
            <Button
              variant="secondary"
              onClick={handleStop}
              disabled={signingIn}
              iconLeft={<Square aria-hidden="true" className="h-4 w-4 fill-current" />}
            >
              Stop recording
            </Button>
          ) : null}
          <Button
            type="submit"
            form={FORM_ID}
            variant="primary"
            loading={signingIn}
            disabled={!password || !username}
            iconLeft={<LogIn aria-hidden="true" className="h-4 w-4" />}
          >
            Sign in
          </Button>
        </>
      }
    >
      <form id={FORM_ID} className="space-y-4" onSubmit={handleSubmit}>
        <p className="text-sm leading-6 text-contrast-helper">
          {forCapture ? situation(status) : NOTES_SITUATION}
        </p>

        {forCapture ? null : (
          <p className="text-sm leading-6 text-contrast-helper">{NOTES_LEAVING}</p>
        )}

        {forCapture && heldBy.includes("notes") ? (
          <p className="text-sm leading-6 text-contrast-helper">
            Notes not saved yet wait too, and save once you sign in.
          </p>
        ) : null}

        {uploadQueue.seconds >= 1 ? (
          <p className="text-sm font-medium text-foreground" role="status">
            {formatQueuedAudio(uploadQueue.seconds)} of audio is waiting to upload.
          </p>
        ) : null}

        {otherUsername && knownUsername ? (
          <p className="rounded-lg border border-status-warning-border bg-status-warning-bg px-3 py-2 text-sm text-status-warning-fg">
            This browser is now signed in as {otherUsername}. {wording.owned}{" "}
            {knownUsername}, so {wording.carriesOn} only once {knownUsername} signs in again.
          </p>
        ) : null}

        {passwordReset ? (
          <p className="rounded-lg border border-status-warning-border bg-status-warning-bg px-3 py-2 text-sm text-status-warning-fg">
            Your password was reset, so {wording.blocked} until you choose a new one.
            Open Nojoin in another tab and choose a new password there, then sign in
            again with it. This tab carries on meanwhile.
          </p>
        ) : null}

        <Input
          label="Username"
          name="username"
          autoComplete="username"
          autoCapitalize="none"
          spellCheck={false}
          value={username}
          readOnly={knownUsername !== null}
          onChange={(event) => setTypedUsername(event.target.value)}
          hint={
            knownUsername !== null
              ? `${wording.owned} this account, so sign in as it.`
              : undefined
          }
        />
        <Input
          label="Password"
          name="password"
          type="password"
          autoComplete="current-password"
          autoFocus
          data-autofocus
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          error={error ?? undefined}
        />

        <p className="text-xs leading-5 text-contrast-helper">
          Keep this tab open until you have signed in. Signing in from another tab
          works too.
        </p>
      </form>
    </Modal>
  );
}
