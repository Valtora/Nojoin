"use client";

import { AlertTriangle, CheckCircle2, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import {
  getVoiceprintMethodStatus,
  rebuildVoiceprints,
  type VoiceprintMethodStatus,
} from "@/lib/api";
import { useNotificationStore } from "@/lib/notificationStore";

import SettingsPanel from "./SettingsPanel";
import SettingsSection from "./SettingsSection";

/**
 * Surfaces voiceprints that predate the current extraction method.
 *
 * These cannot be compared with newly extracted ones, so they silently stop
 * contributing to automatic speaker identification. Making that visible, and
 * offering the rebuild, is the whole point of this panel: the alternative is a
 * user wondering why their saved people stopped being recognised.
 */
export default function VoiceprintMaintenanceSection() {
  const [status, setStatus] = useState<VoiceprintMethodStatus | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [queueing, setQueueing] = useState(false);
  const { addNotification } = useNotificationStore();

  const refresh = useCallback(async () => {
    try {
      setStatus(await getVoiceprintMethodStatus());
      setLoadError(null);
    } catch (err: unknown) {
      // Surfaced rather than swallowed. A silent catch here made a broken
      // endpoint look identical to "nothing to report", which hid a real
      // server error behind an empty page.
      setStatus(null);
      setLoadError(
        err instanceof Error && err.message
          ? err.message
          : "Could not read voiceprint status.",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const handleRebuild = async () => {
    setQueueing(true);
    try {
      await rebuildVoiceprints();
      addNotification({
        type: "success",
        message:
          "Voiceprint rebuild queued. It runs in the background and may take a while.",
      });
      await refresh();
    } catch (err: unknown) {
      addNotification({
        type: "error",
        message:
          err instanceof Error && err.message
            ? err.message
            : "Failed to queue the voiceprint rebuild.",
      });
    } finally {
      setQueueing(false);
    }
  };

  if (loading) {
    return null;
  }

  if (!status) {
    return (
      <SettingsSection
        eyebrow="Maintenance"
        title="Voiceprints"
        description="Rebuild saved voiceprints after an upgrade improves how they are extracted."
        width="regular"
      >
        <SettingsPanel className="mx-auto max-w-3xl">
          <div className="flex items-start gap-3 rounded-xl border border-amber-200 bg-amber-50 p-4 dark:border-amber-800 dark:bg-amber-900/20">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600 dark:text-amber-400" />
            <div className="text-sm text-amber-800 dark:text-amber-200">
              Voiceprint status is unavailable.
              <p className="mt-1 text-xs opacity-80">{loadError}</p>
            </div>
          </div>
        </SettingsPanel>
      </SettingsSection>
    );
  }

  const staleTotal = status.stale_people + status.stale_recording_speakers;

  return (
    <SettingsSection
      eyebrow="Maintenance"
      title="Voiceprints"
      description="Rebuild saved voiceprints after an upgrade improves how they are extracted."
      width="regular"
    >
      <SettingsPanel className="mx-auto max-w-3xl space-y-4">
        <div className="flex flex-col gap-3 rounded-xl border border-gray-200 bg-gray-50 p-4 dark:border-gray-700 dark:bg-gray-900 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <div className="flex items-center gap-2 text-sm font-semibold text-gray-900 dark:text-white">
              {status.rebuild_required ? (
                <AlertTriangle className="h-4 w-4 text-amber-500" />
              ) : (
                <CheckCircle2 className="h-4 w-4 text-green-600" />
              )}
              {status.rebuild_required
                ? "Some voiceprints need rebuilding"
                : "All voiceprints are up to date"}
            </div>
            <p className="mt-1 text-xs text-gray-500">
              {status.rebuild_required ? (
                <>
                  {status.stale_people} of {status.total_people_with_voiceprint}{" "}
                  saved {status.total_people_with_voiceprint === 1
                    ? "person"
                    : "people"}{" "}
                  and {status.stale_recording_speakers} meeting{" "}
                  {status.stale_recording_speakers === 1
                    ? "speaker"
                    : "speakers"}{" "}
                  were created by an older extraction method
                  {staleTotal > 0
                    ? " and are not being used for automatic identification until rebuilt."
                    : "."}{" "}
                  Rebuilding re-reads the original audio, so it can take a while
                  on a large library. Meetings whose audio has been removed
                  cannot be rebuilt.
                </>
              ) : (
                <>
                  Every stored voiceprint was produced by extraction method v
                  {status.current_method_version} and is being used for
                  automatic speaker identification.
                </>
              )}
            </p>
          </div>
          {status.rebuild_required && (
            <button
              type="button"
              onClick={handleRebuild}
              disabled={queueing}
              className="inline-flex shrink-0 items-center justify-center gap-2 rounded-xl bg-orange-600 px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-orange-700 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <RefreshCw
                className={`h-4 w-4 ${queueing ? "animate-spin" : ""}`}
              />
              {queueing ? "Queueing..." : "Rebuild voiceprints"}
            </button>
          )}
        </div>
      </SettingsPanel>
    </SettingsSection>
  );
}
