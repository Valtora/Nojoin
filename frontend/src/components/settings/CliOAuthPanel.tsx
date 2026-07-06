"use client";

import { useCallback, useEffect, useState } from "react";
import { Check, Loader2, Trash2 } from "lucide-react";
import type { CliOAuthStatus } from "@/types";
import {
  connectCliOAuth,
  disconnectCliOAuth,
  getCliOAuthStatus,
} from "@/lib/api/cliOauth";

const MIN_TOKEN_LENGTH = 20;

/**
 * Connect panel for routing AI through a user's own Claude Pro/Max subscription.
 *
 * The user pastes the long-lived token from `claude setup-token`; Nojoin stores
 * it encrypted. Selecting CLI OAuth as the active usage model is enabled in a
 * later milestone, so this panel is connect-only for now.
 */
export default function CliOAuthPanel() {
  const [status, setStatus] = useState<CliOAuthStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
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

  const handleConnect = async () => {
    const trimmed = token.trim();
    if (trimmed.length < MIN_TOKEN_LENGTH) {
      setError("Paste the full token from `claude setup-token`.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      setStatus(await connectCliOAuth(trimmed));
      setToken("");
    } catch {
      setError("Could not save the token. Please try again.");
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

  return (
    <div className="col-span-2 p-4 bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-xl space-y-3">
      <div className="flex items-center justify-between gap-3">
        <div>
          <div className="text-sm font-semibold text-gray-900 dark:text-white">
            Claude subscription (CLI OAuth)
          </div>
          <p className="text-xs text-gray-500 mt-1">
            Route AI through your own Claude Pro/Max subscription. You can
            connect now; selecting CLI OAuth as your usage model is enabled in
            an upcoming update.
          </p>
        </div>
        <div className="shrink-0">
          {loading ? (
            <Loader2 className="w-4 h-4 animate-spin text-gray-400" />
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

      {!connected && (
        <div className="space-y-2">
          <div className="flex gap-2">
            <input
              type="password"
              autoComplete="off"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder="Paste the token from `claude setup-token`"
              disabled={busy}
              className="flex-1 px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500 outline-none disabled:opacity-50"
            />
            <button
              type="button"
              onClick={handleConnect}
              disabled={busy || token.trim().length === 0}
              className="px-4 py-2 rounded-lg bg-orange-500 hover:bg-orange-600 text-white text-sm font-semibold disabled:opacity-50 flex items-center gap-1"
            >
              {busy && <Loader2 className="w-4 h-4 animate-spin" />}
              Connect
            </button>
          </div>
          <p className="text-xs text-gray-500 dark:text-gray-400">
            Run{" "}
            <code className="bg-gray-100 dark:bg-gray-800 px-1 py-0.5 rounded">
              claude setup-token
            </code>{" "}
            in a terminal where Claude Code is installed, approve in your
            browser, and paste the token here. It is stored encrypted and never
            shown again.
          </p>
        </div>
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

      {error && <p className="text-xs text-red-600 dark:text-red-400">{error}</p>}
    </div>
  );
}
