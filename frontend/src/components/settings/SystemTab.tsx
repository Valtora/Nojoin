import { useState, useEffect } from "react";
import { AlertTriangle, Loader2 } from "lucide-react";
import { getAdminHealth } from "@/lib/api";
import type { AdminHealthCheckStatus, AdminHealthStatus } from "@/types";

import SettingsBlock from "./SettingsBlock";
import SettingsCallout from "./SettingsCallout";
import SettingsCard from "./SettingsCard";
import { useSettingsContext } from "./SettingsProvider";
import SettingsRow from "./SettingsRow";
import SettingsStatusBadge, {
  type SettingsStatusBadgeTone,
} from "./SettingsStatusBadge";
import SystemLogs from "./SystemLogs";

const HEALTH_REFRESH_INTERVAL_MS = 30_000;

type HealthCardKey = keyof AdminHealthStatus["checks"];
const HEALTH_CARDS: Array<{ key: HealthCardKey; title: string }> = [
  { key: "database", title: "Database" },
  { key: "queue", title: "Queue" },
  { key: "worker", title: "Worker" },
  { key: "ffmpeg", title: "FFmpeg" },
  { key: "storage", title: "Storage" },
  { key: "transcription_model", title: "Transcription model" },
  { key: "diarization", title: "Speaker separation" },
  { key: "device", title: "Processing device" },
  { key: "optional_ai", title: "AI" },
];

const STATUS_TONE: Record<AdminHealthCheckStatus, SettingsStatusBadgeTone> = {
  ok: "success",
  warning: "warning",
  error: "error",
  disabled: "neutral",
  info: "info",
  unknown: "neutral",
};

const SUMMARY_TONES = {
  ready: "success",
  degraded: "warning",
  blocked: "error",
} as const;

const SUMMARY_TITLES = {
  ready: "Pipeline ready",
  degraded: "Pipeline degraded",
  blocked: "Pipeline blocked",
} as const;

function formatCheckMeta(
  key: HealthCardKey,
  check: AdminHealthStatus["checks"][HealthCardKey],
): string[] {
  const items: string[] = [];

  if (key === "transcription_model") {
    if (typeof check.backend === "string") {
      items.push(`Backend: ${check.backend}`);
    }
    if (typeof check.configured_model === "string") {
      items.push(`Model: ${check.configured_model}`);
    }
  }

  if (key === "device") {
    if (typeof check.requested_device === "string") {
      items.push(`Requested: ${check.requested_device}`);
    }
    if (typeof check.active_device === "string") {
      items.push(`Active: ${check.active_device}`);
    }
    if (typeof check.gpu_name === "string" && check.gpu_name.length > 0) {
      items.push(check.gpu_name);
    }
  }

  if (key === "diarization") {
    if (typeof check.pyannote_downloaded === "boolean") {
      items.push(
        `Pyannote: ${check.pyannote_downloaded ? "cached" : "missing"}`,
      );
    }
    if (typeof check.embedding_downloaded === "boolean") {
      items.push(
        `Embedding: ${check.embedding_downloaded ? "cached" : "missing"}`,
      );
    }
    if (typeof check.token_valid === "boolean") {
      items.push(`HF token: ${check.token_valid ? "valid" : "invalid"}`);
    } else if (check.token_configured === false) {
      items.push("HF token: missing");
    }
  }

  return items;
}

export default function SystemTab() {
  const { isSuperuser } = useSettingsContext();
  const [adminHealth, setAdminHealth] = useState<AdminHealthStatus | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [healthLoading, setHealthLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;

    const refreshAdminHealth = async () => {
      try {
        const nextHealth = await getAdminHealth();
        if (cancelled) {
          return;
        }

        setAdminHealth(nextHealth);
        setHealthError(null);

            } catch (error: unknown) {
        console.error("Failed to load admin health dashboard", error);
        if (!cancelled) {
          setHealthError(
            "Unable to refresh operational readiness right now. Existing data may be stale.",
          );
        }
      } finally {
        if (!cancelled) {
          setHealthLoading(false);
        }
      }
    };

    void refreshAdminHealth();
    const intervalId = window.setInterval(() => {
      void refreshAdminHealth();
    }, HEALTH_REFRESH_INTERVAL_MS);

    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, []);

  const summaryTone = adminHealth
    ? SUMMARY_TONES[adminHealth.summary.pipeline_status]
    : "neutral";
  const summaryTitle = adminHealth
    ? SUMMARY_TITLES[adminHealth.summary.pipeline_status]
    : "Operational readiness";
  const summaryReasons = adminHealth
    ? [
        ...adminHealth.summary.blocking_reasons,
        ...adminHealth.summary.degraded_reasons,
      ]
    : [];

  return (
    <>
      <SettingsCard
        id="system-health"
        title="Service health"
        description="Whether each part of the processing pipeline is ready."
      >
        {adminHealth ? (
          <SettingsBlock>
            <SettingsCallout tone={summaryTone} title={summaryTitle}>
              <div className="space-y-2">
                <p>
                  {adminHealth.summary.message} Server {adminHealth.version}.
                </p>
                {summaryReasons.length > 0 && (
                  <ul className="space-y-1 text-xs leading-5">
                    {summaryReasons.map((reason) => (
                      <li key={reason} className="flex items-start gap-2">
                        <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                        <span>{reason}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </SettingsCallout>
          </SettingsBlock>
        ) : healthLoading ? (
          <SettingsBlock>
            <p className="flex items-center gap-2 text-sm contrast-helper">
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              Checking the worker, queue, models and AI...
            </p>
          </SettingsBlock>
        ) : null}

        {healthError && (
          <SettingsBlock>
            <SettingsCallout
              tone="warning"
              title="Health data may be out of date"
              message={healthError}
            />
          </SettingsBlock>
        )}

        {adminHealth?.download.in_progress && (
          <SettingsBlock
            label="Preparing models"
            description={
              adminHealth.download.message ||
              "Model files are still being prepared for the pipeline."
            }
            aside={
              <span className="text-sm tabular-nums text-foreground">
                {typeof adminHealth.download.progress === "number"
                  ? `${Math.min(adminHealth.download.progress, 100)}%`
                  : "Running"}
              </span>
            }
          >
            <div
              role="progressbar"
              aria-label="Model preparation"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={
                typeof adminHealth.download.progress === "number"
                  ? Math.min(adminHealth.download.progress, 100)
                  : undefined
              }
              className="h-2 overflow-hidden rounded-full bg-action-tint"
            >
              <div
                className="h-full rounded-full bg-action transition-all"
                style={{
                  width: `${Math.min(adminHealth.download.progress ?? 10, 100)}%`,
                }}
              />
            </div>
          </SettingsBlock>
        )}

        {adminHealth &&
          HEALTH_CARDS.map(({ key, title }) => {
            const check = adminHealth.checks[key];
            const meta = formatCheckMeta(key, check);

            return (
              <SettingsRow
                key={key}
                label={title}
                badge={
                  <SettingsStatusBadge
                    tone={STATUS_TONE[check.status as AdminHealthCheckStatus] ?? "neutral"}
                  >
                    {check.label}
                  </SettingsStatusBadge>
                }
                description={
                  <>
                    <span className="block">{check.detail}</span>
                    {meta.length > 0 && (
                      <span className="block">{meta.join(", ")}.</span>
                    )}
                    {check.action && (
                      <span className="mt-1 block font-medium text-foreground">
                        {check.action}
                      </span>
                    )}
                  </>
                }
              />
            );
          })}
      </SettingsCard>

      {isSuperuser && <SystemLogs />}
    </>
  );
}
