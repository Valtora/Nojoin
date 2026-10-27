"use client";

import { ButtonBase, InputBase } from "@/components/ui/nativeBases";
import { SurfaceBase } from "@/components/ui/SurfaceBase";
import { useCallback, useEffect, useState } from "react";
import { ExternalLink, Loader2, Trash2 } from "lucide-react";
import Button from "../ui/Button";
import Modal from "../ui/Modal";
import type {
  CliOAuthProviderStatus,
  CliOAuthStatus,
  CliProvider,
} from "@/types";
import {
  completeCliOAuth,
  disconnectCliOAuth,
  getCliOAuthStatus,
  pollCliOAuth,
  startCliOAuth,
} from "@/lib/api/cliOauth";
import { useNotificationStore } from "@/lib/notificationStore";
import SettingsBlock from "./SettingsBlock";
import SettingsCard from "./SettingsCard";
import SettingsRow from "./SettingsRow";
import SettingsStatusBadge from "./SettingsStatusBadge";
import {
  SETTINGS_BUTTON_DANGER,
  SETTINGS_BUTTON_PRIMARY,
  SETTINGS_CONTROL_END,
  SETTINGS_INPUT_CLASS,
} from "./settingsControls";
import { loadingButtonProps } from "@/components/ui/loadingButton";
import { parseServerTimestamp } from "@/lib/serverTimestamp";
import { formatTimeZoneLocale } from "@/lib/timezone";
import { useUserTimeZone } from "@/lib/useUserTimeZone";

/** Compact token count, e.g. 1.2M / 340K / 512. */
function formatTokens(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return String(value);
}

/** Human message from an axios-style error, falling back to a default. */
function apiErrorMessage(err: unknown, fallback: string): string {
  const detail = (err as { response?: { data?: { detail?: unknown } } })
    ?.response?.data?.detail;
  return typeof detail === "string" && detail.trim() ? detail : fallback;
}

const PROVIDER_ORDER: CliProvider[] = ["claude_code", "codex"];

const PROVIDER_META: Record<
  CliProvider,
  {
    title: string;
    label: string;
    plan: string;
    connectLabel: string;
    vendor: string;
  }
> = {
  claude_code: {
    title: "Claude",
    label: "Claude",
    plan: "Claude Pro or Max",
    connectLabel: "Connect Claude",
    vendor: "Anthropic",
  },
  codex: {
    title: "ChatGPT",
    label: "ChatGPT",
    plan: "ChatGPT Plus or Pro",
    connectLabel: "Connect ChatGPT",
    vendor: "OpenAI",
  },
};

/**
 * Connect panel for routing AI through a user's own subscription — Claude or
 * ChatGPT, ONE at a time. One row per provider, each driving its own connect
 * flow: Claude uses a Nojoin-driven PKCE paste-code exchange; ChatGPT (Codex)
 * uses a device-code flow. Tokens are exchanged and stored server-side,
 * encrypted; nothing is echoed back here. All errors surface as toasts.
 */
export default function CliOAuthPanel({
  onStatusChange,
}: {
  onStatusChange?: (status: CliOAuthStatus | null) => void;
} = {}) {
  const [status, setStatus] = useState<CliOAuthStatus | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const next = await getCliOAuthStatus();
      setStatus(next);
      onStatusChange?.(next);
    } catch {
      setStatus(null);
      onStatusChange?.(null);
    } finally {
      setLoading(false);
    }
  }, [onStatusChange]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const byProvider = (provider: CliProvider): CliOAuthProviderStatus | undefined =>
    status?.providers?.find((entry) => entry.provider === provider);
  // Only one subscription may be connected at a time.
  const connectedProvider = status?.providers?.find((p) => p.connected)?.provider;

  return (
    <SettingsCard
      id="your-ai-subscription"
      title="Your subscriptions"
      description="Connect Claude or ChatGPT, one at a time, to run your meetings on your own plan."
    >
      {loading ? (
        <SettingsBlock>
          <p className="flex items-center gap-2 text-sm contrast-helper">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            Checking connections...
          </p>
        </SettingsBlock>
      ) : (
        PROVIDER_ORDER.map((provider) => (
          <ProviderConnectRow
            key={provider}
            provider={provider}
            status={byProvider(provider)}
            connectedProvider={connectedProvider}
            onChanged={refresh}
          />
        ))
      )}
    </SettingsCard>
  );
}

function ProviderConnectRow({
  provider,
  status,
  connectedProvider,
  onChanged,
}: {
  provider: CliProvider;
  status: CliOAuthProviderStatus | undefined;
  connectedProvider: CliProvider | undefined;
  onChanged: () => void;
}) {
  const meta = PROVIDER_META[provider];
  const addNotification = useNotificationStore((state) => state.addNotification);
  const [busy, setBusy] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  // Paste-code flow (Claude)
  const [authorizeUrl, setAuthorizeUrl] = useState<string | null>(null);
  const [code, setCode] = useState("");
  // Device flow (Codex). `flowKind` distinguishes the modal's mode; `device` is
  // null until the worker publishes the code (a "generating code" state).
  const [flowKind, setFlowKind] = useState<"device" | "paste_code" | null>(null);
  const [device, setDevice] = useState<{
    verificationUri: string;
    verificationUriComplete?: string | null;
    userCode: string;
  } | null>(null);

  const connected = Boolean(status?.connected);
  const timeZone = useUserTimeZone();
  const usageLimitedUntil = parseServerTimestamp(status?.usage_limited_until);
  const usageLimited =
    usageLimitedUntil !== null && usageLimitedUntil.getTime() > Date.now();
  const tokens7d = status?.tokens_7d;
  const tokensTotal = status?.tokens_total;
  // Another provider is connected — this one is blocked until it disconnects.
  const blockedBy =
    !connected && connectedProvider && connectedProvider !== provider
      ? PROVIDER_META[connectedProvider].label
      : null;

  const beginFlow = async () => {
    setBusy(true);
    try {
      const start = await startCliOAuth(provider);
      setFlowKind(start.kind);
      // Device flow: the code arrives via /poll (kept off /start so a slow login
      // can't block the request); start in a "generating code" state.
      setDevice(null);
      setAuthorizeUrl(
        start.kind === "paste_code" ? (start.authorize_url ?? null) : null,
      );
      setCode("");
      setModalOpen(true);
    } catch (err) {
      addNotification({
        type: "error",
        message: apiErrorMessage(err, "Could not start sign-in. Please try again."),
      });
    } finally {
      setBusy(false);
    }
  };

  // Device flow: while the modal is open, poll until the worker publishes the
  // code (shown to the user), the user approves (connected), or it lapses/times
  // out. The code + connection both arrive via /poll.
  useEffect(() => {
    if (!modalOpen || flowKind !== "device") return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    let attempts = 0;
    const maxAttempts = 60; // ~2.5 min at 2.5s
    const finish = (type: "error" | "success", message: string) => {
      setModalOpen(false);
      setDevice(null);
      setFlowKind(null);
      addNotification({ type, message });
    };
    const tick = async () => {
      attempts += 1;
      try {
        const result = await pollCliOAuth(provider);
        if (cancelled) return;
        if (result.status === "connected") {
          finish("success", `${meta.label} subscription connected.`);
          onChanged();
          return;
        }
        if (result.status === "expired") {
          finish("error", "Sign-in expired. Start again for a fresh code.");
          return;
        }
        if (result.user_code) {
          setDevice({
            verificationUri: result.verification_uri ?? "",
            userCode: result.user_code,
          });
        }
      } catch {
        // Transient poll error — keep trying.
      }
      if (cancelled) return;
      if (attempts >= maxAttempts) {
        finish("error", "ChatGPT sign-in took too long. Please try again.");
        return;
      }
      timer = setTimeout(tick, 2500);
    };
    timer = setTimeout(tick, 1500);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [modalOpen, flowKind, provider, onChanged, addNotification, meta.label]);

  const handleComplete = async () => {
    if (!code.trim()) {
      addNotification({
        type: "error",
        message: `Paste the code ${meta.vendor} showed you.`,
      });
      return;
    }
    setBusy(true);
    try {
      await completeCliOAuth(code.trim(), provider);
      setModalOpen(false);
      setCode("");
      addNotification({
        type: "success",
        message: `${meta.label} subscription connected.`,
      });
      onChanged();
    } catch (err) {
      addNotification({
        type: "error",
        message: apiErrorMessage(
          err,
          "Could not complete sign-in. The code may have expired, so open the sign-in page again for a fresh one.",
        ),
      });
    } finally {
      setBusy(false);
    }
  };

  const handleDisconnect = async () => {
    setBusy(true);
    try {
      await disconnectCliOAuth(provider);
      addNotification({
        type: "success",
        message: `${meta.label} subscription disconnected.`,
      });
      onChanged();
    } catch (err) {
      addNotification({
        type: "error",
        message: apiErrorMessage(err, "Could not disconnect. Please try again."),
      });
    } finally {
      setBusy(false);
    }
  };

  const closeConnectModal = () => {
    setModalOpen(false);
    setDevice(null);
    setFlowKind(null);
  };

  const statusBadge = usageLimited ? (
    <SettingsStatusBadge tone="warning">Rate-limited</SettingsStatusBadge>
  ) : connected ? (
    <SettingsStatusBadge tone="success">Connected</SettingsStatusBadge>
  ) : (
    <SettingsStatusBadge tone="neutral">Not connected</SettingsStatusBadge>
  );

  return (
    <SettingsRow
      label={meta.label}
      badge={statusBadge}
      description={
        <>
          <span className="block">Your {meta.plan} plan.</span>
          {usageLimited && (
            <span className="block text-status-warning-fg">
              Limit reached until {formatTimeZoneLocale(usageLimitedUntil!, timeZone)}. The
              server&apos;s providers answer until then.
            </span>
          )}
          {connected && typeof tokens7d === "number" && tokens7d > 0 && (
            <span className="block">
              {formatTokens(tokens7d)} tokens in the last 7 days
              {typeof tokensTotal === "number"
                ? `, ${formatTokens(tokensTotal)} in all`
                : ""}
              .
            </span>
          )}
          {!connected && blockedBy && (
            <span className="block">
              Disconnect {blockedBy} first. Only one subscription can be
              connected at a time.
            </span>
          )}
        </>
      }
      controlClassName={SETTINGS_CONTROL_END}
    >
      {connected ? (
        <ButtonBase
          data-ui-fill="card"
          data-ui-hover="inset"
          data-ui-active="inset"
          data-ui-border="full"
          chrome="action"
          type="button"
          {...loadingButtonProps(busy, undefined, handleDisconnect)}
          className={SETTINGS_BUTTON_DANGER}
        >
          {busy ? (
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          ) : (
            <Trash2 className="h-4 w-4" aria-hidden="true" />
          )}
          Disconnect
        </ButtonBase>
      ) : blockedBy ? null : (
        <ButtonBase
          data-ui-fill="action"
          data-ui-hover="action-hover"
          data-ui-active="action-active"
          data-ui-border="full"
          chrome="action"
          type="button"
          {...loadingButtonProps(busy, undefined, beginFlow)}
          className={SETTINGS_BUTTON_PRIMARY}
        >
          {busy ? (
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          ) : (
            <ExternalLink className="h-4 w-4" aria-hidden="true" />
          )}
          {meta.connectLabel}
        </ButtonBase>
      )}

      <Modal
        open={modalOpen}
        onClose={closeConnectModal}
        // Dismissal is blocked while a request is away: the device flow is
        // polling and the paste flow is exchanging a code.
        dismissible={!busy}
        size="md"
        title={`Connect your ${meta.label} subscription`}
        footer={
          <>
            <Button variant="ghost" onClick={closeConnectModal} disabled={busy}>
              {flowKind === "device" ? "Close" : "Cancel"}
            </Button>
            {flowKind === "paste_code" && (
              <Button
                variant="primary"
                onClick={handleComplete}
                disabled={busy || code.trim().length === 0}
                loading={busy}
              >
                Connect
              </Button>
            )}
          </>
        }
      >
        <div className="space-y-4">
              {flowKind === "device" ? (
                device ? (
                <>
                  <p className="text-sm text-contrast-muted">
                    Open the sign-in page, enter the code below, and approve
                    access. This page updates automatically once you approve.
                  </p>
                  <SurfaceBase
                    data-ui-fill="inset"
                    data-ui-border="full"
                    kind="inset" className="rounded-lg border border-surface-border bg-surface-inset px-4 py-3 text-center">
                    <div className="text-xs text-contrast-helper mb-1">Your code</div>
                    <div className="text-2xl font-mono font-semibold tracking-widest text-foreground">
                      {device.userCode}
                    </div>
                  </SurfaceBase>
                  <a
                    data-ui-fill="action"
                    href={device.verificationUriComplete || device.verificationUri}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1 px-4 py-2 rounded-lg bg-action hover:bg-action-hover text-action-on text-sm font-semibold no-underline"
                  >
                    <ExternalLink className="w-4 h-4" />
                    Open the {meta.vendor} sign-in page
                  </a>
                  <div className="flex items-center gap-2 text-xs text-contrast-helper">
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    Waiting for approval…
                  </div>
                </>
                ) : (
                  <div className="flex items-center gap-2 text-sm text-contrast-helper">
                    <Loader2 className="w-4 h-4 animate-spin" />
                    Generating your code…
                  </div>
                )
              ) : (
                <>
                  <p className="text-sm text-contrast-muted">
                    Grant Nojoin access to your subscription, then paste back the
                    code {meta.vendor} gives you.
                  </p>
                  {authorizeUrl && (
                    <a
                      data-ui-fill="action"
                      href={authorizeUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 px-4 py-2 rounded-lg bg-action hover:bg-action-hover text-action-on text-sm font-semibold no-underline"
                    >
                      <ExternalLink className="w-4 h-4" />
                      Grant access on {meta.vendor}
                    </a>
                  )}
                  <div>
                    <label
                      htmlFor={`cli-code-${provider}`}
                      className="mb-1 block text-sm font-medium text-foreground"
                    >
                      Paste the code {meta.vendor} shows you
                    </label>
                    <InputBase
                      data-ui-fill="control"
                      data-ui-disabled-fill="control-disabled"
                      data-ui-border="full"
                      id={`cli-code-${provider}`}
                      type="text"
                      autoComplete="off"
                      value={code}
                      onChange={(event) => setCode(event.target.value)}
                      placeholder="Paste the code here"
                      disabled={busy}
                      className={SETTINGS_INPUT_CLASS}
                    />
                  </div>
                  <ButtonBase
                    type="button"
                    {...loadingButtonProps(busy, undefined, beginFlow)}
                    className="inline-flex items-center gap-1 text-xs text-contrast-helper hover:text-foreground disabled:opacity-50 aria-disabled:opacity-50"
                  >
                    <ExternalLink className="w-3 h-3" />
                    Need a fresh code? Restart sign-in
                  </ButtonBase>
                </>
              )}
        </div>
      </Modal>
    </SettingsRow>
  );
}
