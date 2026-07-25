"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { BarChart3, X } from "lucide-react";

import {
  getTelemetryStatus,
  markTelemetryNoticeShown,
  updateTelemetryEnabled,
} from "@/lib/api";
import { useNotificationStore } from "@/lib/notificationStore";
import { useServiceStatusStore } from "@/lib/serviceStatusStore";

/**
 * One-time admin notice for installs that were upgraded into telemetry.
 *
 * This banner is load-bearing for consent, not decoration: an upgraded install
 * sends nothing at all until this component reports itself rendered, which is
 * what starts the grace period. That is deliberately stricter than stamping the
 * clock when an admin session appears — it means the notice must actually have
 * reached a screen. The consequence, accepted and documented in
 * docs/TELEMETRY.md, is that an install nobody signs into never pings.
 *
 * Three affordances, matching the three real intents:
 *   Keep it on  -> acknowledges, and sending starts immediately
 *   Turn it off -> disables permanently, nothing is ever sent
 *   Dismiss     -> closes for now, leaving the grace-period clock running
 *
 * Dismiss is per-session by design: the banner returns on the next load until an
 * explicit choice is made, so silence is never mistaken for a decision that was
 * never actually taken.
 */
export default function TelemetryNotice() {
  const [visible, setVisible] = useState(false);
  const [busy, setBusy] = useState(false);
  const stampedRef = useRef(false);
  const addNotification = useNotificationStore((state) => state.addNotification);
  const telemetryNoticePending = useServiceStatusStore(
    (state) => state.telemetryNoticePending,
  );

  useEffect(() => {
    if (!telemetryNoticePending || stampedRef.current) {
      return;
    }

    stampedRef.current = true;
    setVisible(true);

    // Starting the clock is the whole point of rendering, so it is stamped here
    // rather than on any user action. Write-once server-side, so a reload
    // cannot push the grace period forward.
    void markTelemetryNoticeShown().catch(() => {
      // A failed stamp simply means the clock has not started yet, which fails
      // safe: the install stays silent and the banner returns next load.
    });
  }, [telemetryNoticePending]);

  const decide = useCallback(
    async (enabled: boolean) => {
      setBusy(true);
      try {
        await updateTelemetryEnabled(enabled);
        setVisible(false);
        addNotification({
          type: "success",
          message: enabled
            ? "Thank you. Nojoin will share anonymous usage data. You can turn it off in Settings at any time."
            : "Anonymous usage data sharing is off. Nothing will be sent.",
        });
        // Refresh so notice_pending clears and the banner does not return.
        void getTelemetryStatus().catch(() => undefined);
      } catch {
        addNotification({
          type: "error",
          message: "Could not save that choice. Please try again from Settings.",
        });
      } finally {
        setBusy(false);
      }
    },
    [addNotification],
  );

  if (!visible) {
    return null;
  }

  return (
    <div className="sticky bottom-0 z-40 mx-4 mb-4 rounded-2xl border border-orange-200 bg-orange-50 p-4 shadow-lg dark:border-orange-500/30 dark:bg-orange-950/60">
      <div className="flex items-start gap-3">
        <BarChart3 className="mt-0.5 h-5 w-5 shrink-0 text-orange-600 dark:text-orange-400" />
        <div className="flex-1 text-sm">
          <p className="font-semibold text-gray-900 dark:text-white">
            Nojoin can now share anonymous usage data
          </p>
          <p className="mt-1 text-gray-600 dark:text-gray-300">
            One anonymous ping a day: a random install ID, your version, how many
            users and recordings this server has, and which features are on. It
            never includes recordings, transcripts, notes, names, or API keys,
            and the data is never sold. Nothing has been sent yet.
          </p>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <button
              type="button"
              disabled={busy}
              onClick={() => void decide(true)}
              className="rounded-lg bg-orange-600 px-3 py-1.5 text-sm font-medium text-white transition-colors hover:bg-orange-700 disabled:opacity-60"
            >
              Keep it on
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => void decide(false)}
              className="rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-50 disabled:opacity-60 dark:border-gray-600 dark:bg-gray-900 dark:text-gray-200 dark:hover:bg-gray-800"
            >
              Turn it off
            </button>
            <a
              href="https://www.nojoin.co.uk/docs/TELEMETRY"
              target="_blank"
              rel="noopener noreferrer"
              className="px-1 text-sm text-orange-700 underline-offset-2 hover:underline dark:text-orange-400"
            >
              What is collected
            </a>
          </div>
        </div>
        <button
          type="button"
          aria-label="Dismiss for now"
          onClick={() => setVisible(false)}
          className="rounded-md p-1 text-gray-500 transition-colors hover:bg-orange-100 hover:text-gray-700 dark:hover:bg-orange-900/40 dark:hover:text-gray-200"
        >
          <X className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}
