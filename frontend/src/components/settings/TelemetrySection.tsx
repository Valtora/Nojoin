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
import SettingsStatusBadge from "./SettingsStatusBadge";
import { SETTINGS_CONTROL_END } from "./settingsControls";

/**
 * The exact fields each payload carries. Mirrored from build_heartbeat and
 * build_payload in backend/utils/telemetry.py, the single places they are
 * assembled, and locked there by test_heartbeat_contains_only_the_install_id_and_version
 * and test_payload_contains_exactly_the_documented_fields.
 */
const HEARTBEAT_FIELDS = "the random install ID and the Nojoin version, and nothing else";

const COLLECTED_FIELDS: Array<{ group: string; fields: string }> = [
  {
    group: "Install",
    fields:
      "a random install ID, Nojoin version, how long this install has existed, and whether it is served on a localhost origin",
  },
  {
    group: "Scale",
    fields:
      "number of active users, users who recorded in the last 28 days, total recordings, recordings in the last 28 days and the last 24 hours, and recorded hours in the last 28 days",
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

function heartbeatDescription(status: TelemetryStatus): string {
  if (status.heartbeat_blocked_by === "environment") {
    return "Not sent. NOJOIN_TELEMETRY_ENABLED=false stops it.";
  }
  if (status.heartbeat_blocked_by === "earlier_opt_out") {
    return "Not sent. Telemetry was turned off on this install before the heartbeat existed, and that choice still stands. Turning diagnostics on below ends it.";
  }
  return "The install ID and version, once a day and when Nojoin starts on a new version. It counts installations, and only NOJOIN_TELEMETRY_ENABLED=false stops it.";
}

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
      const next = await updateTelemetryEnabled(enabled);
      setStatus(next);
      addNotification({
        type: "success",
        message: enabled
          ? "Usage diagnostics are on."
          : next.heartbeat_enabled
            ? "Usage diagnostics are off. The daily heartbeat continues."
            : "Usage diagnostics are off. Nothing will be sent.",
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
        title="Anonymous usage data"
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
      title="Anonymous usage data"
      description="A daily heartbeat counts installations, and optional diagnostics help decide what to build next. Neither includes recordings, transcripts, notes, names, or keys, and the data is never sold."
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
        id="privacy-heartbeat"
        label="Install heartbeat"
        description={heartbeatDescription(status)}
        badge={
          <SettingsStatusBadge tone={status.heartbeat_enabled ? "info" : "neutral"}>
            {status.heartbeat_enabled ? "Always on" : "Off"}
          </SettingsStatusBadge>
        }
      />

      {status.heartbeat_enabled && status.heartbeat_last_attempt_ok === false && (
        <SettingsBlock>
          <SettingsCallout
            tone="warning"
            title="The last heartbeat did not arrive"
            message={`${status.heartbeat_last_attempt_detail ?? "The heartbeat failed."} Attempted ${formatTimestamp(status.heartbeat_last_attempt_at)}. Nothing else in Nojoin is affected, and the next attempt is in an hour. If you block this endpoint deliberately, this is expected.`}
          />
        </SettingsBlock>
      )}

      <SettingsRow
        id="privacy-telemetry"
        label="Share usage diagnostics"
        description={
          status.enabled
            ? "Counts and settings, every six hours."
            : "No diagnostics are sent."
        }
        controlClassName={SETTINGS_CONTROL_END}
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
            title="No diagnostics have been sent yet"
            message={`This install was upgraded into diagnostics, so they stay off until you confirm above, or until ${status.grace_period_days} days after the notice was first shown.`}
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
            title="The last diagnostics ping did not arrive"
            message={`${status.last_attempt_detail ?? "The ping failed."} Attempted ${formatTimestamp(status.last_attempt_at)}. Nothing else in Nojoin is affected, and the next attempt is in at most six hours. If you block this endpoint deliberately, this is expected.`}
          />
        </SettingsBlock>
      )}

      <SettingsBlock
        label="Exactly what is sent"
        contentClassName="settings-inset rounded-xl p-4"
      >
        <dl className="space-y-2 text-sm">
          <div className="sm:flex sm:gap-3">
            <dt className="min-w-32 font-medium text-contrast-muted">Heartbeat</dt>
            <dd className="text-contrast-helper">{HEARTBEAT_FIELDS}</dd>
          </div>
        </dl>
        <p className="mt-4 mb-2 text-xs font-medium text-contrast-muted">
          Diagnostics, every six hours while switched on
        </p>
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
          <div className="@min-[33rem]:col-span-2">
            <dt className="text-contrast-helper">Install ID</dt>
            <dd className="font-mono text-xs break-all text-foreground">
              {status.install_id}
            </dd>
          </div>
          <div>
            <dt className="text-contrast-helper">Heartbeat last sent</dt>
            <dd className="text-foreground">
              {formatTimestamp(status.heartbeat_last_sent_at)}
            </dd>
          </div>
          <div>
            <dt className="text-contrast-helper">Diagnostics last sent</dt>
            <dd className="text-foreground">
              {formatTimestamp(status.last_sent_at)}
            </dd>
          </div>
          <div className="@min-[33rem]:col-span-2">
            <dt className="text-contrast-helper">Endpoints</dt>
            <dd className="font-mono text-xs break-all text-foreground">
              {status.heartbeat_endpoint}
              <br />
              {status.endpoint}
            </dd>
          </div>
        </dl>
      </SettingsBlock>

      <SettingsBlock>
        <p className="flex items-start gap-2 text-xs text-contrast-helper">
          <BarChart3 className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          <span>
            The collector that receives these is open source in the{" "}
            <code className="font-mono">telemetry/</code> directory of the Nojoin
            repository, so what is stored can be verified rather than taken on
            trust. Everything it receives is kept indefinitely, so use can be
            followed over several years.
          </span>
        </p>
      </SettingsBlock>
    </SettingsCard>
  );
}
