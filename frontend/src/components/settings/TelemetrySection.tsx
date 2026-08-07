"use client";

import { useCallback, useEffect, useState } from "react";
import { BarChart3, Loader2 } from "lucide-react";

import { getTelemetryStatus, updateTelemetryEnabled } from "@/lib/api";
import { getErrorMessage } from "@/lib/errors";
import { useNotificationStore } from "@/lib/notificationStore";
import type { TelemetryStatus } from "@/types";

import SettingsCallout from "./SettingsCallout";
import { Switch } from "@/components/ui/Switch";

import SettingsBlock from "./SettingsBlock";
import SettingsRow from "./SettingsRow";
import SettingsCard from "./SettingsCard";

/**
 * The exact fields the ping carries. Mirrored from build_payload in
 * backend/utils/telemetry.py, which is the single place the payload is
 * assembled, and locked there by test_payload_contains_exactly_the_documented_fields.
 */
const COLLECTED_FIELDS: Array<{ group: string; fields: string }> = [
  {
    group: "Install",
    fields:
      "a random install ID, Nojoin version, how long this install has existed, and whether it is served on a localhost origin",
  },
  {
    group: "Scale",
    fields:
      "number of active users, users who recorded in the last 28 days, total recordings, recordings in the last 28 days, and recorded hours in the last 28 days",
  },
  {
    group: "AI setup",
    fields:
      "which provider family is configured (never keys, endpoints, or model names), whether a secondary provider is set, whether CLI OAuth is connected, and whether Meeting Edge is on",
  },
  {
    group: "Transcription",
    fields: "the ASR engine, the Whisper model size, and whether a GPU is present",
  },
  {
    group: "Features in use",
    fields:
      "whether calendar, the MCP connector, meeting chat, documents, tasks, and the People library are being used",
  },
];

function formatTimestamp(value: string | null): string {
  if (!value) {
    return "Never";
  }
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? "Unknown" : parsed.toLocaleString();
}

export default function TelemetrySection() {
  const [status, setStatus] = useState<TelemetryStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const addNotification = useNotificationStore((state) => state.addNotification);

  const load = useCallback(async () => {
    try {
      setStatus(await getTelemetryStatus());
    } catch (error: unknown) {
      console.error("Failed to load telemetry status", error);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const handleToggle = async (enabled: boolean) => {
    setSaving(true);
    try {
      setStatus(await updateTelemetryEnabled(enabled));
      addNotification({
        type: "success",
        message: enabled
          ? "Anonymous usage data sharing is on."
          : "Anonymous usage data sharing is off. Nothing further will be sent.",
      });
    } catch (error: unknown) {
      addNotification({
        type: "error",
        message: getErrorMessage(error, "Could not update the telemetry setting."),
      });
      void load();
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <SettingsCard
        title="Anonymous Usage Data"
        description="Loading the current setting."
      >
        <Loader2 className="h-4 w-4 animate-spin text-contrast-icon-muted" />
      </SettingsCard>
    );
  }

  if (!status) {
    return null;
  }

  return (
    <SettingsCard
      title="Anonymous Usage Data"
      description="Helps decide what to build next by counting deployments and feature use. Never includes recordings, transcripts, notes, names, or keys, and the data is never sold."
    >
      {status.managed_by_env && (
        <SettingsBlock>
          <SettingsCallout
            tone="info"
            title="Pinned by the environment"
            message="NOJOIN_TELEMETRY_ENABLED is set in this deployment's environment, so this setting cannot be changed here. Update .env and restart Nojoin to change it."
          />
        </SettingsBlock>
      )}

      <SettingsRow
        id="privacy-telemetry"
        label="Share anonymous usage data"
        description={
          status.enabled
            ? "A ping is sent every six hours."
            : "Nothing is sent."
        }
        controlClassName="@min-[26rem]:min-w-0 @min-[26rem]:flex @min-[26rem]:justify-end"
      >
        <Switch
          checked={status.enabled}
          disabled={status.managed_by_env || saving}
          onCheckedChange={(checked) => void handleToggle(checked)}
        />
      </SettingsRow>

      {status.enabled && !status.consent_granted && (
        <SettingsBlock>
          <SettingsCallout
            tone="warning"
            title="Nothing has been sent yet"
            message={`This install was upgraded into this feature, so it stays silent until you confirm above, or until ${status.grace_period_days} days after the notice was first shown.`}
          />
        </SettingsBlock>
      )}

      {/* Only when consent is in place. Before that, silence is the intended
          behaviour and the callout above already explains it; reporting a
          failed send as well would be two warnings for one situation. */}
      {status.enabled && status.consent_granted && status.last_attempt_ok === false && (
        <SettingsBlock>
          <SettingsCallout
            tone="warning"
            title="The last ping did not arrive"
            message={`${status.last_attempt_detail ?? "The ping failed."} Attempted ${formatTimestamp(status.last_attempt_at)}. Nothing else in Nojoin is affected, and the next attempt is in at most six hours. If you block this endpoint deliberately, this is expected.`}
          />
        </SettingsBlock>
      )}

      <SettingsBlock
        label="Exactly what is sent, every six hours"
        contentClassName="settings-inset rounded-xl p-4"
      >
        <dl className="space-y-2 text-sm">
          {COLLECTED_FIELDS.map((entry) => (
            <div key={entry.group} className="sm:flex sm:gap-3">
              <dt className="min-w-32 font-medium text-contrast-muted">
                {entry.group}
              </dt>
              <dd className="text-contrast-helper">{entry.fields}</dd>
            </div>
          ))}
        </dl>
      </SettingsBlock>

      <SettingsBlock>
        <dl className="grid gap-3 text-sm @min-[33rem]:grid-cols-2">
          <div>
            <dt className="text-contrast-helper">Install ID</dt>
            <dd className="font-mono text-xs break-all text-foreground">
              {status.install_id}
            </dd>
          </div>
          <div>
            <dt className="text-contrast-helper">Last sent</dt>
            <dd className="text-foreground">
              {formatTimestamp(status.last_sent_at)}
            </dd>
          </div>
          <div className="@min-[33rem]:col-span-2">
            <dt className="text-contrast-helper">Endpoint</dt>
            <dd className="font-mono text-xs break-all text-foreground">
              {status.endpoint}
            </dd>
          </div>
        </dl>
      </SettingsBlock>

      <SettingsBlock>
        <p className="flex items-start gap-2 text-xs text-contrast-helper">
          <BarChart3 className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          <span>
            The collector that receives these pings is open source in the{" "}
            <code className="font-mono">telemetry/</code> directory of the Nojoin
            repository, so what is stored can be verified rather than taken on
            trust. Raw pings are kept for 13 months, then reduced to daily totals.
          </span>
        </p>
      </SettingsBlock>
    </SettingsCard>
  );
}
