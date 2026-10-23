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
import { loadingButtonProps } from "@/components/ui/loadingButton";

/**
 * One-time admin notice for installs that were upgraded into telemetry.
 *
 * This banner is load-bearing for consent, not decoration: an upgraded install
 * sends no diagnostics until this component reports itself rendered, which is
 * what starts the grace period. That is deliberately stricter than stamping the
 * clock when an admin session appears: the notice must have reached a screen.
 * The install heartbeat does not wait for it (docs/TELEMETRY.md), so the copy
 * says the heartbeat is already being sent.
 *
 * Three affordances, matching the three real intents:
 *   Keep diagnostics on  -> acknowledges, and diagnostics start immediately
 *   Turn diagnostics off -> no diagnostics are ever sent
 *   Dismiss              -> closes for now, leaving the grace-period clock running
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
            ? "Thank you. Nojoin will share usage diagnostics. You can turn them off in Settings at any time."
            : "Usage diagnostics are off. The daily heartbeat continues.",
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
    <div className="sticky bottom-0 z-[var(--z-sticky)] mx-4 mb-4 rounded-2xl border border-action-border bg-action-tint p-4 shadow-float">
      <div className="flex items-start gap-3">
        <BarChart3 className="mt-0.5 h-5 w-5 shrink-0 text-action-text" />
        <div className="flex-1 text-sm">
          <p className="font-semibold text-foreground">
            Nojoin can now share anonymous usage data
          </p>
          <p className="mt-1 text-contrast-helper">
            Nojoin sends a daily heartbeat with a random install ID and your
            version, so installations can be counted. It can also share usage
            diagnostics every six hours: how many users and recordings this
            server has, and which features are on. Neither includes
            recordings, transcripts, notes, names, or API keys, and the data
            is never sold. No diagnostics have been sent yet.
          </p>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <button
              type="button"
              {...loadingButtonProps(busy, undefined, () => void decide(true))}
              className="rounded-lg bg-action px-3 py-1.5 text-sm font-medium text-action-on transition-colors hover:bg-action-hover disabled:opacity-60 aria-disabled:opacity-60"
            >
              Keep diagnostics on
            </button>
            <button
              type="button"
              {...loadingButtonProps(busy, undefined, () => void decide(false))}
              className="rounded-lg border border-control-border bg-surface-card px-3 py-1.5 text-sm font-medium text-contrast-muted transition-colors hover:bg-surface-inset disabled:opacity-60 aria-disabled:opacity-60"
            >
              Turn diagnostics off
            </button>
            <a
              href="https://www.nojoin.co.uk/docs/TELEMETRY"
              target="_blank"
              rel="noopener noreferrer"
              className="px-1 text-sm text-action-text underline-offset-2 hover:underline"
            >
              What is collected
            </a>
          </div>
        </div>
        <button
          type="button"
          aria-label="Dismiss for now"
          onClick={() => setVisible(false)}
          className="rounded-md p-1 text-contrast-helper transition-colors hover:bg-action-tint hover:text-contrast-muted"
        >
          <X className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}
