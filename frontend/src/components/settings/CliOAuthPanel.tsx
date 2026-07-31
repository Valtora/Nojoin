"use client";

import { useCallback, useEffect, useState } from "react";
import { Check, ExternalLink, Loader2, Trash2, X } from "lucide-react";
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

// Backend datetimes are naive UTC (no offset); ensure Date parses them as UTC.
function parseUtcDate(value?: string | null): Date | null {
  if (!value) return null;
  const iso = /[zZ]|[+-]\d\d:?\d\d$/.test(value) ? value : `${value}Z`;
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : date;
}

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
    title: "Claude subscription",
    label: "Claude",
    plan: "Claude Pro/Max",
    connectLabel: "Connect Claude",
    vendor: "Anthropic",
  },
  codex: {
    title: "ChatGPT subscription",
    label: "ChatGPT",
    plan: "ChatGPT Plus/Pro",
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
    <div className="col-span-2 p-4 bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-xl space-y-3">
      <div>
        <div className="text-sm font-semibold text-gray-900 dark:text-white">
          Your AI subscription
        </div>
        <p className="text-xs text-gray-500 mt-1">
          Connect Claude or ChatGPT (one at a time) to route AI through your own
          plan, then choose &ldquo;My own AI subscription&rdquo; above.
        </p>
      </div>

      {loading ? (
        <div className="flex items-center gap-2 text-xs text-gray-500">
          <Loader2 className="w-4 h-4 animate-spin" /> Checking connections…
        </div>
      ) : (
        <div className="space-y-3">
          {PROVIDER_ORDER.map((provider) => (
            <ProviderConnectRow
              key={provider}
              provider={provider}
              status={byProvider(provider)}
              connectedProvider={connectedProvider}
              onChanged={refresh}
            />
          ))}
        </div>
      )}
    </div>
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
  const usageLimitedUntil = parseUtcDate(status?.usage_limited_until);
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
          "Could not complete sign-in — the code may have expired. Open the sign-in page again for a fresh code.",
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

  return (
    <div className="settings-inset space-y-2 rounded-xl p-3">
      <div className="flex items-center justify-between gap-3">
        <div>
          <div className="text-sm font-semibold text-gray-900 dark:text-white">
            {meta.title}
          </div>
          <p className="text-xs text-gray-500 mt-0.5">
            Route AI through your own {meta.plan} plan.
          </p>
        </div>
        <div className="shrink-0">
          {usageLimited ? (
            <span className="inline-flex items-center px-3 py-1 rounded-full text-xs font-semibold bg-amber-100 text-amber-800 dark:bg-amber-950/40 dark:text-amber-400">
              Usage limited
            </span>
          ) : connected ? (
            <span className="inline-flex items-center gap-1 px-3 py-1 rounded-full text-xs font-semibold bg-green-100 text-green-800 dark:bg-green-950/40 dark:text-green-400">
              <Check className="w-3 h-3" /> Connected
            </span>
          ) : (
            <span className="inline-flex items-center px-3 py-1 rounded-full text-xs font-semibold bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400">
              Not connected
            </span>
          )}
        </div>
      </div>

      {usageLimited && (
        <p className="text-xs text-amber-600 dark:text-amber-400">
          Subscription limit reached; it resets around{" "}
          {usageLimitedUntil!.toLocaleString()}. Your fallback provider (if
          configured) is used until then.
        </p>
      )}

      {connected && typeof tokens7d === "number" && tokens7d > 0 && (
        <p className="text-xs text-gray-500 dark:text-gray-400">
          {formatTokens(tokens7d)} tokens in the last 7 days
          {typeof tokensTotal === "number"
            ? ` (${formatTokens(tokensTotal)} all time)`
            : ""}
          .
        </p>
      )}

      {connected ? (
        <button
          type="button"
          onClick={handleDisconnect}
          disabled={busy}
          className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg border border-red-200 dark:border-red-900 text-red-600 dark:text-red-400 text-sm font-medium hover:bg-red-50 dark:hover:bg-red-950/30 disabled:opacity-50"
        >
          {busy ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            <Trash2 className="w-4 h-4" />
          )}
          Disconnect
        </button>
      ) : blockedBy ? (
        <p className="text-xs contrast-helper">
          Disconnect your {blockedBy} subscription above to switch to {meta.label}{" "}
          — only one can be connected at a time.
        </p>
      ) : (
        <button
          type="button"
          onClick={beginFlow}
          disabled={busy}
          className="inline-flex items-center gap-1 px-4 py-2 rounded-lg bg-orange-500 hover:bg-orange-600 text-white text-sm font-semibold disabled:opacity-50"
        >
          {busy ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            <ExternalLink className="w-4 h-4" />
          )}
          {meta.connectLabel}
        </button>
      )}

      {modalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-scrim">
          <div className="bg-white dark:bg-gray-800 rounded-xl shadow-2xl max-w-lg w-full border border-gray-200 dark:border-gray-700 flex flex-col">
            <div className="flex items-center justify-between p-5 border-b border-gray-200 dark:border-gray-700">
              <h2 className="text-lg font-semibold text-gray-900 dark:text-white">
                Connect your {meta.label} subscription
              </h2>
              <button
                type="button"
                onClick={() => {
                  setModalOpen(false);
                  setDevice(null);
                  setFlowKind(null);
                }}
                className="text-gray-400 hover:text-gray-900 dark:hover:text-white"
                aria-label="Close"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="p-5 space-y-4">
              {flowKind === "device" ? (
                device ? (
                <>
                  <p className="text-sm text-gray-700 dark:text-gray-300">
                    Open the sign-in page, enter the code below, and approve
                    access. This page updates automatically once you approve.
                  </p>
                  <div className="rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900 px-4 py-3 text-center">
                    <div className="text-xs text-gray-500 mb-1">Your code</div>
                    <div className="text-2xl font-mono font-semibold tracking-widest text-gray-900 dark:text-white">
                      {device.userCode}
                    </div>
                  </div>
                  <a
                    href={device.verificationUriComplete || device.verificationUri}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1 px-4 py-2 rounded-lg bg-orange-500 hover:bg-orange-600 text-white text-sm font-semibold no-underline"
                  >
                    <ExternalLink className="w-4 h-4" />
                    Open the {meta.vendor} sign-in page
                  </a>
                  <div className="flex items-center gap-2 text-xs text-gray-500">
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    Waiting for approval…
                  </div>
                </>
                ) : (
                  <div className="flex items-center gap-2 text-sm text-gray-500">
                    <Loader2 className="w-4 h-4 animate-spin" />
                    Generating your code…
                  </div>
                )
              ) : (
                <>
                  <p className="text-sm text-gray-700 dark:text-gray-300">
                    Grant Nojoin access to your subscription, then paste back the
                    code {meta.vendor} gives you.
                  </p>
                  {authorizeUrl && (
                    <a
                      href={authorizeUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 px-4 py-2 rounded-lg bg-orange-500 hover:bg-orange-600 text-white text-sm font-semibold no-underline"
                    >
                      <ExternalLink className="w-4 h-4" />
                      Grant access on {meta.vendor}
                    </a>
                  )}
                  <div>
                    <label className="block text-xs font-medium text-gray-600 dark:text-gray-300 mb-1">
                      Paste the code {meta.vendor} shows you
                    </label>
                    <input
                      type="text"
                      autoComplete="off"
                      value={code}
                      onChange={(event) => setCode(event.target.value)}
                      placeholder="Paste the code here"
                      disabled={busy}
                      className="w-full px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500 outline-none disabled:opacity-50"
                    />
                  </div>
                  <button
                    type="button"
                    onClick={beginFlow}
                    disabled={busy}
                    className="inline-flex items-center gap-1 text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 disabled:opacity-50"
                  >
                    <ExternalLink className="w-3 h-3" />
                    Need a fresh code? Restart sign-in
                  </button>
                </>
              )}
            </div>

            <div className="flex justify-end gap-2 p-5 border-t border-gray-200 dark:border-gray-700">
              <button
                type="button"
                onClick={() => {
                  setModalOpen(false);
                  setDevice(null);
                  setFlowKind(null);
                }}
                disabled={busy}
                className="px-4 py-2 rounded-lg text-sm font-medium text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 disabled:opacity-50"
              >
                {flowKind === "device" ? "Close" : "Cancel"}
              </button>
              {flowKind === "paste_code" && (
                <button
                  type="button"
                  onClick={handleComplete}
                  disabled={busy || code.trim().length === 0}
                  className="inline-flex items-center gap-1 px-4 py-2 rounded-lg bg-orange-500 hover:bg-orange-600 text-white text-sm font-semibold disabled:opacity-50"
                >
                  {busy && <Loader2 className="w-4 h-4 animate-spin" />}
                  Connect
                </button>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
