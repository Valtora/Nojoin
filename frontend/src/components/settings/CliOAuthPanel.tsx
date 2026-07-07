"use client";

import { useCallback, useEffect, useState } from "react";
import { Check, ExternalLink, Loader2, Trash2, X } from "lucide-react";
import type { CliOAuthStatus } from "@/types";
import {
  completeCliOAuth,
  disconnectCliOAuth,
  getCliOAuthStatus,
  startCliOAuth,
} from "@/lib/api/cliOauth";

// Backend datetimes are naive UTC (no offset); ensure Date parses them as UTC.
function parseUtcDate(value?: string | null): Date | null {
  if (!value) return null;
  const iso = /[zZ]|[+-]\d\d:?\d\d$/.test(value) ? value : `${value}Z`;
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : date;
}

/**
 * Connect panel for routing AI through a user's own Claude subscription.
 *
 * Nojoin drives the PKCE OAuth: "Connect" opens Anthropic's authorize page in a
 * new tab and a modal to paste back the code Anthropic shows. Nojoin exchanges
 * the code server-side and stores the tokens encrypted. Once connected, the
 * user selects "My Claude subscription" in the AI routing section to route
 * inference through it.
 */
export default function CliOAuthPanel({
  onConnectedChange,
}: {
  onConnectedChange?: (connected: boolean) => void;
} = {}) {
  const [status, setStatus] = useState<CliOAuthStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [authorizeUrl, setAuthorizeUrl] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      setStatus(await getCliOAuthStatus());
    } catch {
      setStatus(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Start (or restart) the flow: mint a fresh PKCE challenge and open the modal.
  // The authorize URL is rendered as a real link (clicked directly by the user)
  // rather than window.open'd after the await, which popup blockers would eat.
  const beginFlow = async () => {
    setBusy(true);
    setError(null);
    try {
      const { authorize_url } = await startCliOAuth();
      setAuthorizeUrl(authorize_url);
      setCode("");
      setModalOpen(true);
    } catch {
      setError("Could not start sign-in. Please try again.");
    } finally {
      setBusy(false);
    }
  };

  const handleComplete = async () => {
    if (!code.trim()) {
      setError("Paste the code Anthropic showed you.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      setStatus(await completeCliOAuth(code.trim()));
      setModalOpen(false);
      setCode("");
    } catch {
      // The code is single-use and expires quickly; the pending flow is spent,
      // so recovery is to open the sign-in page again for a fresh code.
      setError(
        "Could not complete sign-in — the code may have expired. Open the sign-in page again for a fresh code.",
      );
    } finally {
      setBusy(false);
    }
  };

  const handleDisconnect = async () => {
    setBusy(true);
    setError(null);
    try {
      setStatus(await disconnectCliOAuth());
    } catch {
      setError("Could not disconnect. Please try again.");
    } finally {
      setBusy(false);
    }
  };

  const connected = Boolean(status?.connected);
  const usageLimitedUntil = parseUtcDate(status?.usage_limited_until);
  const usageLimited =
    usageLimitedUntil !== null && usageLimitedUntil.getTime() > Date.now();

  // Surface connection state to the parent so the usage-model selector can gate
  // the "CLI OAuth" option on a live credential.
  useEffect(() => {
    onConnectedChange?.(connected);
  }, [connected, onConnectedChange]);

  return (
    <div className="col-span-2 p-4 bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-xl space-y-3">
      <div className="flex items-center justify-between gap-3">
        <div>
          <div className="text-sm font-semibold text-gray-900 dark:text-white">
            Claude subscription (CLI OAuth)
          </div>
          <p className="text-xs text-gray-500 mt-1">
            Route AI through your own Claude Pro/Max subscription. Once
            connected, choose &ldquo;CLI OAuth&rdquo; as your usage model above.
          </p>
        </div>
        <div className="shrink-0">
          {loading ? (
            <Loader2 className="w-4 h-4 animate-spin text-gray-400" />
          ) : usageLimited ? (
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
          Claude subscription limit reached; it resets around{" "}
          {usageLimitedUntil!.toLocaleString()}. Your fallback provider (if
          configured) is used until then.
        </p>
      )}

      {!connected && (
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
          Connect Claude subscription
        </button>
      )}

      {connected && (
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
      )}

      {error && !modalOpen && (
        <p className="text-xs text-red-600 dark:text-red-400">{error}</p>
      )}

      {modalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm">
          <div className="bg-white dark:bg-gray-800 rounded-xl shadow-2xl max-w-lg w-full border border-gray-200 dark:border-gray-700 flex flex-col">
            <div className="flex items-center justify-between p-5 border-b border-gray-200 dark:border-gray-700">
              <h2 className="text-lg font-semibold text-gray-900 dark:text-white">
                Connect your Claude subscription
              </h2>
              <button
                type="button"
                onClick={() => setModalOpen(false)}
                className="text-gray-400 hover:text-gray-900 dark:hover:text-white"
                aria-label="Close"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="p-5 space-y-4">
              <p className="text-sm text-gray-700 dark:text-gray-300">
                Grant Nojoin access to your Claude subscription, then paste back
                the code Anthropic gives you.
              </p>

              {authorizeUrl && (
                <a
                  href={authorizeUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 px-4 py-2 rounded-lg bg-orange-500 hover:bg-orange-600 text-white text-sm font-semibold no-underline"
                >
                  <ExternalLink className="w-4 h-4" />
                  Grant access on Anthropic
                </a>
              )}

              <div>
                <label className="block text-xs font-medium text-gray-600 dark:text-gray-300 mb-1">
                  Paste the code Anthropic shows you
                </label>
                <input
                  type="text"
                  autoComplete="off"
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
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

              {error && (
                <p className="text-xs text-red-600 dark:text-red-400">{error}</p>
              )}
            </div>

            <div className="flex justify-end gap-2 p-5 border-t border-gray-200 dark:border-gray-700">
              <button
                type="button"
                onClick={() => setModalOpen(false)}
                disabled={busy}
                className="px-4 py-2 rounded-lg text-sm font-medium text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleComplete}
                disabled={busy || code.trim().length === 0}
                className="inline-flex items-center gap-1 px-4 py-2 rounded-lg bg-orange-500 hover:bg-orange-600 text-white text-sm font-semibold disabled:opacity-50"
              >
                {busy && <Loader2 className="w-4 h-4 animate-spin" />}
                Connect
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
