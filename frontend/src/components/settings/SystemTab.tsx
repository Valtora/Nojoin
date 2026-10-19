import {
  useState,
  useEffect,
  useCallback,
  useMemo,
  useRef,
  memo,
  Fragment,
} from "react";
import { Popover, Transition } from "@headlessui/react";
import {
  AlertTriangle,
  Check,
  Download,
  Loader2,
  Pause,
  Play,
  Settings,
  Terminal,
  Trash2,
} from "lucide-react";
import api, { API_BASE_URL, getAdminHealth } from "@/lib/api";
import { useNotificationStore } from "@/lib/notificationStore";
import { useNavigationStore } from "@/lib/store";
import type { AdminHealthCheckStatus, AdminHealthStatus } from "@/types";

import SettingsBlock from "./SettingsBlock";
import SettingsCallout from "./SettingsCallout";
import SettingsCard from "./SettingsCard";
import SettingsRow from "./SettingsRow";
import SettingsStatusBadge, {
  type SettingsStatusBadgeTone,
} from "./SettingsStatusBadge";
import {
  SETTINGS_BUTTON_SECONDARY,
  SETTINGS_INPUT_CLASS,
  SETTINGS_SELECT_CLASS,
} from "./settingsControls";
import { cn } from "@/lib/cn";

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

/**
 * The live log view is a firehose: a busy container emits lines faster than
 * React can usefully render them. Three limits keep it responsive.
 *
 * MAX_LOG_LINES bounds both memory and the DOM — older lines are dropped rather
 * than accumulated forever. LOG_FLUSH_MS batches arrivals so a burst of fifty
 * lines costs one render instead of fifty. SCROLL_PIN_SLOP decides when the
 * viewer is "at the bottom" and may follow new output; outside it, the user is
 * reading history and must not be yanked back down.
 */
const MAX_LOG_LINES = 2000;
const LOG_FLUSH_MS = 200;
const SCROLL_PIN_SLOP = 40;

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

/**
 * One rendered log line. Declared at module scope and memoised: defined inside
 * SystemTab it would be a new component type on every render, and React would
 * rebuild every visible line for each log that arrived.
 */
const LogLine = memo(function LogLine({
  text,
  showTimestamps,
  wordWrap,
}: {
  text: string;
  showTimestamps: boolean;
  wordWrap: boolean;
}) {
  // Expected format from backend (with timestamps enabled):
  // [container-name] 2024-05-22T15:30:00.123456Z Log Message...

  let container = "";
  let timestamp = "";
  let content = text;

  let remainder = text;

  // 1. Extract Container Prefix: [nojoin-api]
  const containerMatch = remainder.match(/^(\[.*?\])\s*/);
  if (containerMatch) {
    container = containerMatch[1];
    remainder = remainder.substring(containerMatch[0].length);
  }

  // 2. Extract Timestamp: 2024-05-22T...
  // Look for ISO-like timestamp at start of remainder
  const timeMatch = remainder.match(/^(\d{4}-\d{2}-\d{2}T\S+)\s*/);
  if (timeMatch) {
    timestamp = timeMatch[1];
    remainder = remainder.substring(timeMatch[0].length);
  }

  content = remainder;

  // Default to INFO (green), which also covers lines tagged LOG.
  let level = "INFO";
  let levelColor = "text-status-success-fg";
  const upperContent = content.toUpperCase();

  // Check for specific levels (overrides default INFO)
  if (upperContent.includes("WARN") || upperContent.includes("WRN")) {
    level = "WARN";
    levelColor = "text-status-warning-fg";
  } else if (
    upperContent.includes("ERR") ||
    upperContent.includes("FAIL") ||
    upperContent.includes("CRIT")
  ) {
    level = "ERROR";
    levelColor = "text-status-danger-fg";
  } else if (upperContent.includes("DBG") || upperContent.includes("DEBUG")) {
    level = "DEBUG";
    levelColor = "text-status-info-fg";
  }

  // 4. Strip redundant level prefixes to avoid duplication (e.g. "INFO: ...")
  // Matches start of string: Level + optional colon + whitespace
  content = content.replace(
    /^(INFO|WARN|WARNING|ERROR|ERR|DEBUG|DBG|LOG)(:|)\s+/i,
    "",
  );

  // Format time for display (HH:mm:ss)
  let timeDisplay = "--:--:--";
  if (timestamp) {
    const tParts = timestamp.split("T");
    if (tParts.length > 1) {
      // Take HH:mm:ss from "...T15:30:00.123Z"
      timeDisplay = tParts[1].substring(0, 8);
    } else {
      timeDisplay = timestamp.substring(0, 8);
    }
  }

  return (
    // Below the console's own 36rem the metadata sits above the message, so a
    // phone still gets the full width for the text.
    <div className="flex flex-col rounded px-2 py-0.5 -mx-2 hover:bg-surface-card @min-[36rem]:flex-row @min-[36rem]:gap-3">
      <span className="flex shrink-0 select-none gap-3">
        {showTimestamps && (
          <span className="w-[68px] shrink-0 contrast-helper" title={timestamp}>
            {timeDisplay}
          </span>
        )}
        <span
          className="w-[110px] shrink-0 truncate contrast-helper @min-[36rem]:text-right"
          title={container}
        >
          {container}
        </span>
        <span className={`${levelColor} w-12 shrink-0 font-semibold @min-[36rem]:text-right`}>
          {level}
        </span>
      </span>
      <span
        className={`min-w-0 flex-1 break-words text-foreground ${wordWrap ? "whitespace-pre-wrap" : "whitespace-nowrap"}`}
      >
        {content}
      </span>
    </div>
  );
});

export default function SystemTab() {
  const [logs, setLogs] = useState<string[]>([]);
  const pendingLogsRef = useRef<string[]>([]);
  const pinnedToBottomRef = useRef(true);
  const [selectedContainer, setSelectedContainer] = useState("all");
  const [isConnected, setIsConnected] = useState(false);
  const [logFilter, setLogFilter] = useState("");
  const [logLevel, setLogLevel] = useState("ALL");
  const [autoScroll, setAutoScroll] = useState(true);
  const [adminHealth, setAdminHealth] = useState<AdminHealthStatus | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [healthLoading, setHealthLoading] = useState(true);

  const wsRef = useRef<WebSocket | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const {
    logShowTimestamps,
    toggleLogShowTimestamps,
    logWordWrap,
    toggleLogWordWrap,
  } = useNavigationStore();
  const { addNotification } = useNotificationStore();

  const containers = [
    "all",
    "nojoin-api",
    "nojoin-worker-gpu",
    "nojoin-worker-cpu",
    "nojoin-worker-io",
    "nojoin-worker-parse",
    "nojoin-frontend",
    "nojoin-nginx",
    "nojoin-redis",
    "nojoin-db",
  ];

  const logLevels = ["ALL", "DEBUG", "INFO", "WARN", "ERROR"];

  // Derived, not stored: state would re-render the whole tab twice for every
  // line that arrived.
  const filteredLogs = useMemo(() => {
    let result = logs;

    // 1. Text/Regex Filter
    if (logFilter) {
      try {
        const regex = new RegExp(logFilter, "i");
        result = result.filter((log) => regex.test(log));
      } catch {
        result = result.filter((log) =>
          log.toLowerCase().includes(logFilter.toLowerCase()),
        );
      }
    }

    // 2. Log Level Filter
    if (logLevel !== "ALL") {
      result = result.filter((log) => {
        // Substring match on the level name, counting WARNING as WARN and
        // CRITICAL as ERROR.
        const upper = log.toUpperCase();
        if (logLevel === "INFO") return upper.includes("INFO");
        if (logLevel === "WARN")
          return upper.includes("WARN") || upper.includes("WRN");
        if (logLevel === "ERROR")
          return (
            upper.includes("ERR") ||
            upper.includes("FAIL") ||
            upper.includes("CRIT")
          );
        if (logLevel === "DEBUG")
          return upper.includes("DBG") || upper.includes("DEBUG");
        return true;
      });
    }

    return result;
  }, [logs, logFilter, logLevel]);

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

  // Follow new output only while the viewer is already at the bottom, so
  // scrolling back through history is never snapped away.
  useEffect(() => {
    const element = scrollRef.current;
    if (!autoScroll || !element || !pinnedToBottomRef.current) {
      return;
    }
    element.scrollTop = element.scrollHeight;
  }, [filteredLogs, autoScroll]);

  const handleLogScroll = useCallback(() => {
    const element = scrollRef.current;
    if (!element) {
      return;
    }
    const distanceFromBottom =
      element.scrollHeight - element.scrollTop - element.clientHeight;
    pinnedToBottomRef.current = distanceFromBottom <= SCROLL_PIN_SLOP;
  }, []);

  // WebSocket for logs
  useEffect(() => {
    setLogs([]); // Clear logs on switch
    pendingLogsRef.current = [];
    setIsConnected(false);

    // Buffer arrivals and flush on a timer, so a burst of lines costs one
    // render rather than one per line.
    const flush = () => {
      const batch = pendingLogsRef.current;
      if (batch.length === 0) {
        return;
      }
      pendingLogsRef.current = [];
      setLogs((prev) => {
        const next = prev.concat(batch);
        return next.length > MAX_LOG_LINES
          ? next.slice(next.length - MAX_LOG_LINES)
          : next;
      });
    };

    const flushTimer = window.setInterval(flush, LOG_FLUSH_MS);

    const connectWs = () => {
      try {
        // Construct WS URL from API_BASE_URL to match Protocol and Host
        let apiBase = API_BASE_URL;

        // Handle relative URLs (e.g. "/api/v1") by appending to window.location.origin
        if (apiBase.startsWith("/")) {
          apiBase = window.location.origin + apiBase;
        }

        const apiProtocol = apiBase.startsWith("https") ? "wss:" : "ws:";
        const urlObj = new URL(apiBase);

        // Target URL format: wss://<host>:<port>/api/v1/system/logs/live
        const wsUrl = `${apiProtocol}//${urlObj.host}${urlObj.pathname}/system/logs/live?container=${selectedContainer}`;

        const ws = new WebSocket(wsUrl);

        ws.onopen = () => {
          setIsConnected(true);
        };

        ws.onmessage = (event) => {
          pendingLogsRef.current.push(event.data as string);
        };

        ws.onclose = () => {
          setIsConnected(false);
        };

        ws.onerror = (error) => {
          console.error("WebSocket error:", error);
          pendingLogsRef.current.push(
            "--- Connection Error (Check Console) - Ensure API is reachable ---",
          );
        };

        wsRef.current = ws;

            } catch (err: unknown) {
        console.error("Failed to connect to log stream:", err);
        pendingLogsRef.current.push(
          "--- Auth Error - Unable to connect to the live log stream ---",
        );
      }
    };

    connectWs();

    return () => {
      window.clearInterval(flushTimer);
      pendingLogsRef.current = [];
      if (wsRef.current) {
        wsRef.current.close();
      }
    };
  }, [selectedContainer]);

  const handleDownloadLogs = async () => {
    try {
      const response = await api.get(
        `/system/logs/download?container=${selectedContainer}`,
        {
          responseType: "blob",
        },
      );

      const url = window.URL.createObjectURL(new Blob([response.data]));
      const link = document.createElement("a");
      link.href = url;
      link.setAttribute("download", `${selectedContainer}_logs.txt`);
      document.body.appendChild(link);
      link.click();
      link.remove();

        } catch (error: unknown) {
      console.error("Download failed", error);
      addNotification({ type: "error", message: "Failed to download logs." });
    }
  };

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

      <SettingsCard
        id="system-logs"
        title="Logs"
        description="Live output from the Nojoin services."
      >
        <SettingsBlock className="grid gap-3 @min-[44rem]:grid-cols-[10rem_7rem_minmax(0,1fr)_auto]">
          <div>
            <label htmlFor="logs-service" className="mb-1 block text-xs font-medium contrast-helper">
              Service
            </label>
            <select
              id="logs-service"
              value={selectedContainer}
              onChange={(e) => setSelectedContainer(e.target.value)}
              className={SETTINGS_SELECT_CLASS}
            >
              {containers.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor="logs-level" className="mb-1 block text-xs font-medium contrast-helper">
              Level
            </label>
            <select
              id="logs-level"
              value={logLevel}
              onChange={(e) => setLogLevel(e.target.value)}
              className={SETTINGS_SELECT_CLASS}
            >
              {logLevels.map((l) => (
                <option key={l} value={l}>
                  {l}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor="logs-filter" className="mb-1 block text-xs font-medium contrast-helper">
              Filter
            </label>
            <input
              id="logs-filter"
              type="text"
              placeholder="A regular expression"
              className={cn(SETTINGS_INPUT_CLASS, "font-mono")}
              value={logFilter}
              onChange={(e) => setLogFilter(e.target.value)}
            />
          </div>
          <div className="flex flex-wrap items-end gap-2">
            <button
              type="button"
              onClick={() => setAutoScroll(!autoScroll)}
              aria-pressed={!autoScroll}
              className={SETTINGS_BUTTON_SECONDARY}
            >
              {autoScroll ? (
                <Pause className="h-4 w-4" aria-hidden="true" />
              ) : (
                <Play className="h-4 w-4" aria-hidden="true" />
              )}
              {autoScroll ? "Pause" : "Follow"}
            </button>
            <button
              type="button"
              onClick={() => setLogs([])}
              aria-label="Clear the console"
              title="Clear the console"
              className={cn(SETTINGS_BUTTON_SECONDARY, "w-10 px-0")}
            >
              <Trash2 className="h-4 w-4" aria-hidden="true" />
            </button>
            <Popover className="relative">
              <Popover.Button
                aria-label="Display options"
                title="Display options"
                className={cn(SETTINGS_BUTTON_SECONDARY, "w-10 px-0")}
              >
                <Settings className="h-4 w-4" aria-hidden="true" />
              </Popover.Button>
              <Transition
                as={Fragment}
                enter="transition ease-out duration-100"
                enterFrom="opacity-0"
                enterTo="opacity-100"
                leave="transition ease-in duration-75"
                leaveFrom="opacity-100"
                leaveTo="opacity-0"
              >
                <Popover.Panel className="absolute right-0 z-[var(--z-dropdown)] mt-2 w-56 rounded-lg border border-surface-float-border bg-surface-float p-1 shadow-float">
                  <button
                    type="button"
                    aria-pressed={logShowTimestamps}
                    onClick={toggleLogShowTimestamps}
                    className="flex w-full items-center justify-between rounded-md px-3 py-2 text-sm text-foreground hover:bg-surface-inset"
                  >
                    <span>Show times</span>
                    {logShowTimestamps && <Check className="h-4 w-4 text-action-text" aria-hidden="true" />}
                  </button>
                  <button
                    type="button"
                    aria-pressed={logWordWrap}
                    onClick={toggleLogWordWrap}
                    className="flex w-full items-center justify-between rounded-md px-3 py-2 text-sm text-foreground hover:bg-surface-inset"
                  >
                    <span>Wrap long lines</span>
                    {logWordWrap && <Check className="h-4 w-4 text-action-text" aria-hidden="true" />}
                  </button>
                </Popover.Panel>
              </Transition>
            </Popover>
            <button
              type="button"
              onClick={handleDownloadLogs}
              className={SETTINGS_BUTTON_SECONDARY}
            >
              <Download className="h-4 w-4" aria-hidden="true" />
              Download
            </button>
          </div>
        </SettingsBlock>

        <SettingsBlock>
          <div className="@container flex h-[32rem] flex-col overflow-hidden rounded-lg border border-control-border bg-control-bg">
            <div
              ref={scrollRef}
              onScroll={handleLogScroll}
              role="log"
              aria-label="Service logs"
              className="flex-1 overflow-y-auto overscroll-contain p-3 font-mono text-[11px] leading-relaxed"
            >
              {filteredLogs.length === 0 ? (
                <div className="flex h-full flex-col items-center justify-center gap-3 select-none contrast-helper">
                  <Terminal className="h-10 w-10" aria-hidden="true" />
                  <p>No log lines to show</p>
                </div>
              ) : (
                filteredLogs.map((log, i) => (
                  <LogLine
                    key={i}
                    text={log}
                    showTimestamps={logShowTimestamps}
                    wordWrap={logWordWrap}
                  />
                ))
              )}
            </div>
            <div className="flex items-center justify-between border-t border-control-border px-3 py-1.5 text-xs contrast-helper">
              <span className="flex items-center gap-2">
                <span
                  aria-hidden="true"
                  className={cn(
                    "h-2 w-2 rounded-full",
                    isConnected ? "bg-status-success-fg" : "bg-control-border",
                  )}
                />
                {isConnected ? "Connected" : "Disconnected"}
              </span>
              <span className="tabular-nums">{filteredLogs.length} lines</span>
            </div>
          </div>
        </SettingsBlock>
      </SettingsCard>
    </>
  );
}
