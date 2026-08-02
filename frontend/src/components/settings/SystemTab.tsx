import {
  useState,
  useEffect,
  useCallback,
  useMemo,
  useRef,
  memo,
  Fragment,
  type ComponentType,
} from "react";
import { Popover, Transition } from "@headlessui/react";
// import axios from "axios"; // Not used anymore
import {
  Activity,
  AlertTriangle,
  Cpu,
  Database,
  Download,
  FolderTree,
  HardDrive,
  Loader2,
  Terminal,
  Play,
  Trash2,
  Settings,
  Pause,
  Check,
  Server,
  Sparkles,
} from "lucide-react";
import api, { API_BASE_URL, getAdminHealth } from "@/lib/api";
import { useNotificationStore } from "@/lib/notificationStore";
import { useNavigationStore } from "@/lib/store";
import type { AdminHealthCheckStatus, AdminHealthStatus } from "@/types";

import SettingsBlock from "./SettingsBlock";
import SettingsCallout from "./SettingsCallout";
import SettingsCard from "./SettingsCard";

const HEALTH_REFRESH_INTERVAL_MS = 30_000;

type HealthCardKey = keyof AdminHealthStatus["checks"];
type HealthIcon = ComponentType<{ className?: string }>;

const HEALTH_CARDS: Array<{
  key: HealthCardKey;
  title: string;
  icon: HealthIcon;
}> = [
  { key: "database", title: "Database", icon: Database },
  { key: "queue", title: "Queue", icon: Server },
  { key: "worker", title: "Worker", icon: Activity },
  { key: "ffmpeg", title: "FFmpeg", icon: Terminal },
  { key: "storage", title: "Storage", icon: FolderTree },
  { key: "transcription_model", title: "Transcription", icon: HardDrive },
  { key: "diarization", title: "Diarization", icon: Activity },
  { key: "device", title: "Device", icon: Cpu },
  { key: "optional_ai", title: "Optional AI", icon: Sparkles },
];

const STATUS_STYLES: Record<
  AdminHealthCheckStatus,
  {
    badge: string;
    dot: string;
    iconSurface: string;
    iconColor: string;
  }
> = {
  ok: {
    badge:
      "bg-status-success-bg text-status-success-fg",
    dot: "bg-status-success-bg",
    iconSurface: "bg-status-success-bg",
    iconColor: "text-status-success-fg",
  },
  warning: {
    badge:
      "bg-status-warning-bg text-status-warning-fg",
    dot: "bg-status-warning-bg",
    iconSurface: "bg-status-warning-bg",
    iconColor: "text-status-warning-fg",
  },
  error: {
    badge: "bg-status-danger-bg text-status-danger-fg",
    dot: "bg-status-danger-bg",
    iconSurface: "bg-status-danger-bg",
    iconColor: "text-status-danger-fg",
  },
  disabled: {
    badge:
      "bg-surface-inset text-contrast-muted",
    dot: "bg-surface-card",
    iconSurface: "bg-surface-inset",
    iconColor: "text-contrast-helper",
  },
  info: {
    badge:
      "bg-status-info-bg text-status-info-fg",
    dot: "bg-status-info-bg",
    iconSurface: "bg-status-info-bg",
    iconColor: "text-status-info-fg",
  },
  unknown: {
    badge:
      "bg-surface-inset text-contrast-muted",
    dot: "bg-surface-card",
    iconSurface: "bg-surface-inset",
    iconColor: "text-contrast-helper",
  },
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
 * One rendered log line. Declared at module scope and memoised: it used to be
 * defined inside SystemTab, which made it a new component type on every render,
 * so React discarded and rebuilt every visible line each time a log arrived.
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
    // Remove container and following whitespace from remainder
    remainder = remainder.substring(containerMatch[0].length);
  }

  // 2. Extract Timestamp: 2024-05-22T...
  // Look for ISO-like timestamp at start of remainder
  const timeMatch = remainder.match(/^(\d{4}-\d{2}-\d{2}T\S+)\s*/);
  if (timeMatch) {
    timestamp = timeMatch[1];
    // Remove timestamp and following whitespace
    remainder = remainder.substring(timeMatch[0].length);
  }

  // 3. Remaining text is the content
  content = remainder;

  // Determine Level and Color
  // Default to INFO (Green) as requested for "LOG" level
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
    <div className="flex gap-3 hover:bg-surface-card py-0.5 px-2 -mx-2 rounded">
      {showTimestamps && (
        <span
          className="text-contrast-helper shrink-0 select-none w-[68px] font-mono"
          title={timestamp}
        >
          {timeDisplay}
        </span>
      )}

      {/* Container Name */}
      <span
        className="text-contrast-helper shrink-0 select-none w-[110px] truncate text-right"
        title={container}
      >
        {container}
      </span>

      {/* Log Level */}
      <span
        className={`${levelColor} font-bold shrink-0 w-10 select-none text-right`}
      >
        {level}
      </span>

      {/* Content */}
      <span
        className={`break-all flex-1 ${wordWrap ? "whitespace-pre-wrap" : "whitespace-nowrap"}`}
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
    "nojoin-frontend",
    "nojoin-nginx",
    "nojoin-redis",
    "nojoin-db",
  ];

  const logLevels = ["ALL", "DEBUG", "INFO", "WARN", "ERROR"];

  // Derived, not stored: storing it in state re-rendered the whole tab twice
  // for every line that arrived.
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
        // Simple heuristic: check if line contains level string
        // Assuming logs contain "INFO", "WARN", "ERROR", "DEBUG"
        // Uses includes for safety across mixed formats.
        // Also handling mapping: "WARNING" -> "WARN", "CRITICAL" -> "ERROR"
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

  // Auto-scroll
  // Follow new output only while the viewer is already at the bottom. Writing
  // scrollTop on every batch regardless is what made scrolling back through
  // history feel like it kept snapping away.
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
    // Reset connection state
    setIsConnected(false);

    // Buffer arrivals and flush on a timer: setLogs per message re-rendered the
    // component (and copied the whole array) once for every line.
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
    <SettingsCard
      id="system-logs"
      title="Service Health and Logs"
      description="Live operational output from the Nojoin services."
    >
      <SettingsBlock contentClassName="animate-in fade-in duration-500 space-y-4">
      {adminHealth ? (
        <SettingsCallout tone={summaryTone} title={summaryTitle}>
          <div className="space-y-2">
            <p>{adminHealth.summary.message}</p>
            <div className="flex flex-wrap items-center gap-2 text-xs font-medium uppercase tracking-[0.2em] opacity-80">
              <span>Server {adminHealth.version}</span>
              <span aria-hidden="true">/</span>
              <span>{adminHealth.summary.pipeline_status}</span>
            </div>
            {summaryReasons.length > 0 && (
              <ul className="space-y-1 text-xs leading-5 opacity-90">
                {summaryReasons.map((reason) => (
                  <li key={reason} className="flex items-start gap-2">
                    <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                    <span>{reason}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </SettingsCallout>
      ) : healthLoading ? (
        <SettingsCallout
          tone="neutral"
          title="Operational readiness"
          message="Checking worker status, queue reachability, model readiness, and fallback state."
        />
      ) : null}

      {healthError && (
        <SettingsCallout
          tone="warning"
          title="Health data may be stale"
          message={healthError}
        />
      )}

      {adminHealth?.download.in_progress && (
        <div className="settings-inset rounded-xl p-4 space-y-3">
          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="text-sm font-semibold text-foreground">
                Model preparation in progress
              </div>
              <p className="mt-1 text-xs contrast-helper">
                {adminHealth.download.message ||
                  "Model assets are still being prepared for the pipeline."}
              </p>
            </div>
            <div className="flex items-center gap-2 text-sm font-medium text-contrast-muted">
              <Loader2 className="h-4 w-4 animate-spin" />
              <span>
                {typeof adminHealth.download.progress === "number"
                  ? `${Math.min(adminHealth.download.progress, 100)}%`
                  : "Running"}
              </span>
            </div>
          </div>
          <div className="h-2 overflow-hidden rounded-full bg-surface-inset">
            <div
              className="h-full rounded-full bg-action transition-all"
              style={{
                width: `${Math.min(adminHealth.download.progress ?? 10, 100)}%`,
              }}
            />
          </div>
        </div>
      )}

      {adminHealth && (
        <div className="grid gap-4 @min-[33rem]:grid-cols-2 @min-[67rem]:grid-cols-4">
          {HEALTH_CARDS.map(({ key, title, icon: Icon }) => {
            const check = adminHealth.checks[key];
            const styles = STATUS_STYLES[check.status as AdminHealthCheckStatus];
            const meta = formatCheckMeta(key, check);

            return (
              <div key={key} className="settings-inset rounded-xl p-4 space-y-3">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="text-xs font-semibold uppercase tracking-[0.2em] contrast-helper">
                      {title}
                    </div>
                    <div className="mt-1 text-sm font-semibold text-foreground">
                      {check.label}
                    </div>
                  </div>
                  <div
                    className={`rounded-full p-2 ${styles.iconSurface}`}
                    aria-hidden="true"
                  >
                    <Icon className={`h-4 w-4 ${styles.iconColor}`} />
                  </div>
                </div>

                <div>
                  <span
                    className={`inline-flex items-center gap-2 rounded-full px-2.5 py-1 text-xs font-medium ${styles.badge}`}
                  >
                    <span className={`h-2 w-2 rounded-full ${styles.dot}`} />
                    {check.status.replace(/_/g, " ")}
                  </span>
                </div>

                {meta.length > 0 && (
                  <div className="flex flex-wrap gap-2 text-xs contrast-helper">
                    {meta.map((item) => (
                      <span
                        key={item}
                        className="rounded-full bg-surface-inset px-2.5 py-1"
                      >
                        {item}
                      </span>
                    ))}
                  </div>
                )}

                <p className="text-sm contrast-helper">{check.detail}</p>

                {check.action && (
                  <p className="text-xs font-medium text-contrast-muted">
                    {check.action}
                  </p>
                )}
              </div>
            );
          })}
        </div>
      )}

      <div className="bg-[#0d1117] rounded-lg border border-control-border shadow-float overflow-hidden flex flex-col h-[600px]">
          {/* Toolbar */}
          <div className="flex flex-wrap items-center gap-3 p-2 bg-[#161b22] border-b border-control-border">
            {/* Container Select */}
            <div className="relative">
              <select
                value={selectedContainer}
                onChange={(e) => setSelectedContainer(e.target.value)}
                className="appearance-none bg-[#0d1117] text-contrast-icon-muted text-xs font-medium px-3 py-1.5 pr-8 rounded border border-control-border focus:border-status-info-border focus:ring-1 focus:ring-status-info-border outline-none w-40"
              >
                {containers.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
              {/* Status Dot Overlay */}
              <div className="absolute right-7 top-1/2 -translate-y-1/2 pointer-events-none">
                <span
                  className={`block w-2 h-2 rounded-full ${isConnected ? "bg-status-success-bg" : "bg-surface-card"}`}
                />
              </div>
            </div>

            {/* Log Level Select */}
            <div className="relative">
              <select
                value={logLevel}
                onChange={(e) => setLogLevel(e.target.value)}
                className="appearance-none bg-[#0d1117] text-contrast-icon-muted text-xs font-medium px-3 py-1.5 pr-6 rounded border border-control-border focus:border-status-info-border focus:ring-1 focus:ring-status-info-border outline-none w-20 text-center"
              >
                {logLevels.map((l) => (
                  <option key={l} value={l}>
                    {l}
                  </option>
                ))}
              </select>
            </div>

            <div className="flex-1 relative">
              <input
                type="text"
                placeholder="Enter a regex pattern to filter logs by..."
                className="w-full bg-[#0d1117] text-contrast-icon-muted text-xs px-3 py-1.5 rounded border border-control-border focus:border-status-info-border focus:ring-1 focus:ring-status-info-border outline-none placeholder:text-control-placeholder font-mono"
                value={logFilter}
                onChange={(e) => setLogFilter(e.target.value)}
              />
            </div>

            {/* Actions */}
            <button
              onClick={() => setAutoScroll(!autoScroll)}
              title={autoScroll ? "Pause Auto-scroll" : "Resume Auto-scroll"}
              className={`p-1.5 rounded transition-colors ${autoScroll ? "text-status-success-fg hover:bg-surface-card" : "text-contrast-icon-muted hover:text-foreground hover:bg-surface-card"}`}
            >
              {autoScroll ? (
                <Pause className="w-4 h-4" />
              ) : (
                <Play className="w-4 h-4" />
              )}
            </button>
            <button
              onClick={() => setLogs([])}
              title="Clear Logs"
              className="p-1.5 text-contrast-icon-muted hover:text-foreground hover:bg-surface-card rounded transition-colors"
            >
              <Trash2 className="w-4 h-4" />
            </button>
            <Popover className="relative">
              <Popover.Button
                className="p-1.5 text-contrast-icon-muted hover:text-foreground hover:bg-surface-card rounded transition-colors outline-none"
                title="Log Settings"
              >
                <Settings className="w-4 h-4" />
              </Popover.Button>

              <Transition
                as={Fragment}
                enter="transition ease-out duration-100"
                enterFrom="transform opacity-0 scale-95"
                enterTo="transform opacity-100 scale-100"
                leave="transition ease-in duration-75"
                leaveFrom="transform opacity-100 scale-100"
                leaveTo="transform opacity-0 scale-95"
              >
                <Popover.Panel className="absolute right-0 z-10 mt-2 w-56 origin-top-right rounded-md bg-[#1c2128] shadow-float ring-1 ring-surface-border ring-opacity-5 focus:outline-none border border-control-border p-1">
                  <div className="p-1 space-y-1">
                    <button
                      onClick={toggleLogShowTimestamps}
                      className="group flex w-full items-center justify-between rounded-md px-2 py-2 text-sm text-contrast-icon-muted hover:bg-surface-card hover:text-foreground"
                    >
                      <span>Show Timestamps</span>
                      {logShowTimestamps && (
                        <Check className="h-4 w-4 text-action-text" />
                      )}
                    </button>
                    <button
                      onClick={toggleLogWordWrap}
                      className="group flex w-full items-center justify-between rounded-md px-2 py-2 text-sm text-contrast-icon-muted hover:bg-surface-card hover:text-foreground"
                    >
                      <span>Word Wrap</span>
                      {logWordWrap && (
                        <Check className="h-4 w-4 text-action-text" />
                      )}
                    </button>
                  </div>
                </Popover.Panel>
              </Transition>
            </Popover>
            <div className="w-px h-4 bg-surface-card mx-1"></div>
            <button
              onClick={handleDownloadLogs}
              className="flex items-center gap-2 px-3 py-1.5 bg-[#21262d] hover:bg-[#30363d] text-contrast-icon-muted text-xs font-medium rounded border border-control-border transition-colors"
            >
              <Download className="w-3.5 h-3.5" />
              Download
            </button>
          </div>

          {/* Log Output */}
          <div
            ref={scrollRef}
            onScroll={handleLogScroll}
            className="flex-1 overflow-y-auto overscroll-contain p-4 font-mono text-[11px] leading-relaxed text-contrast-icon-muted scrollbar-thin scrollbar-thumb-gray-700 scrollbar-track-transparent"
          >
            {filteredLogs.length === 0 ? (
              <div className="h-full flex flex-col items-center justify-center opacity-30 select-none">
                <Terminal className="w-12 h-12 mb-4" />
                <p>No logs to display</p>
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

          {/* Status Footer */}
          <div className="px-3 py-1 bg-[#161b22] border-t border-control-border flex items-center justify-between text-[10px] text-contrast-helper font-mono">
            <span>{isConnected ? "Connected" : "Disconnected"}</span>
            <span>{filteredLogs.length} lines</span>
          </div>
      </div>
      </SettingsBlock>
    </SettingsCard>
  );
}
