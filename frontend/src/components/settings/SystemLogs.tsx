import { ButtonBase, InputBase, SelectBase } from "@/components/ui/nativeBases";
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
  Check,
  Download,
  Pause,
  Play,
  Settings,
  Terminal,
  Trash2,
} from "lucide-react";
import api, { API_BASE_URL } from "@/lib/api";
import { cn } from "@/lib/cn";
import { useNotificationStore } from "@/lib/notificationStore";
import { useNavigationStore } from "@/lib/store";
import { useOnDepsChange } from "@/lib/useOnDepsChange";

import SettingsBlock from "./SettingsBlock";
import SettingsCard from "./SettingsCard";
import {
  SETTINGS_BUTTON_SECONDARY,
  SETTINGS_INPUT_CLASS,
  SETTINGS_SELECT_CLASS,
} from "./settingsControls";

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

/**
 * One rendered log line. Declared at module scope and memoised: defined inside
 * SystemLogs it would be a new component type on every render, and React would
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
    <div className="flex flex-col gap-0.5 rounded px-2 py-1 -mx-2 hover:bg-surface-card @min-[36rem]:flex-row @min-[36rem]:gap-3 @min-[36rem]:py-0.5">
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

/** The file name the API's Content-Disposition gives the download. */
function logFileName(container: string): string {
  return container === "all" ? "nojoin-all_logs.txt" : `${container}_logs.txt`;
}

/**
 * The live log console and download, of one service or all of them. The API
 * serves both to the superuser alone, so SystemTab renders this only for that
 * account and no other account opens the stream.
 */
export default function SystemLogs() {
  const [logs, setLogs] = useState<string[]>([]);
  const pendingLogsRef = useRef<string[]>([]);
  const pinnedToBottomRef = useRef(true);
  const [selectedContainer, setSelectedContainer] = useState("all");
  const [isConnected, setIsConnected] = useState(false);
  const [logFilter, setLogFilter] = useState("");
  const [logLevel, setLogLevel] = useState("ALL");
  const [autoScroll, setAutoScroll] = useState(true);

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

  // Another service starts an empty, disconnected console.
  useOnDepsChange([selectedContainer], () => {
    setLogs([]);
    setIsConnected(false);
  });

  // WebSocket for logs
  useEffect(() => {
    pendingLogsRef.current = [];

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
      link.setAttribute("download", logFileName(selectedContainer));
      document.body.appendChild(link);
      link.click();
      link.remove();
      // A log can run to tens of megabytes; the page keeps none of it.
      window.URL.revokeObjectURL(url);
    } catch (error: unknown) {
      console.error("Download failed", error);
      addNotification({ type: "error", message: "Failed to download logs." });
    }
  };

  return (
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
          <SelectBase
            id="logs-service"
            value={selectedContainer}
            onChange={(e) => setSelectedContainer(e.target.value)}
            className={SETTINGS_SELECT_CLASS}
          >
            {containers.map((c) => (
              <option key={c} value={c}>
                {c === "all" ? "All services" : c}
              </option>
            ))}
          </SelectBase>
        </div>
        <div>
          <label htmlFor="logs-level" className="mb-1 block text-xs font-medium contrast-helper">
            Level
          </label>
          <SelectBase
            id="logs-level"
            value={logLevel}
            onChange={(e) => setLogLevel(e.target.value)}
            className={SETTINGS_SELECT_CLASS}
          >
            {logLevels.map((l) => (
              <option key={l} value={l}>
                {l === "ALL" ? "All levels" : l}
              </option>
            ))}
          </SelectBase>
        </div>
        <div>
          <label htmlFor="logs-filter" className="mb-1 block text-xs font-medium contrast-helper">
            Filter
          </label>
          <InputBase
            id="logs-filter"
            type="text"
            placeholder="A regular expression"
            className={cn(SETTINGS_INPUT_CLASS, "font-mono")}
            value={logFilter}
            onChange={(e) => setLogFilter(e.target.value)}
          />
        </div>
        <div className="flex flex-wrap items-end gap-2">
          <ButtonBase
            chrome="action"
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
          </ButtonBase>
          <ButtonBase
            chrome="action"
            type="button"
            onClick={() => setLogs([])}
            aria-label="Clear the console"
            title="Clear the console"
            className={cn(SETTINGS_BUTTON_SECONDARY, "w-10 px-0")}
          >
            <Trash2 className="h-4 w-4" aria-hidden="true" />
          </ButtonBase>
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
              <Popover.Panel data-ui="surface" data-ui-surface="float" className="absolute right-0 z-[var(--z-dropdown)] mt-2 w-56 rounded-lg border border-surface-float-border bg-surface-float p-1 shadow-float">
                <ButtonBase
                  type="button"
                  aria-pressed={logShowTimestamps}
                  onClick={toggleLogShowTimestamps}
                  className="flex w-full items-center justify-between rounded-md px-3 py-2 text-sm text-foreground hover:bg-surface-inset"
                >
                  <span>Show times</span>
                  {logShowTimestamps && <Check className="h-4 w-4 text-action-text" aria-hidden="true" />}
                </ButtonBase>
                <ButtonBase
                  type="button"
                  aria-pressed={logWordWrap}
                  onClick={toggleLogWordWrap}
                  className="flex w-full items-center justify-between rounded-md px-3 py-2 text-sm text-foreground hover:bg-surface-inset"
                >
                  <span>Wrap long lines</span>
                  {logWordWrap && <Check className="h-4 w-4 text-action-text" aria-hidden="true" />}
                </ButtonBase>
              </Popover.Panel>
            </Transition>
          </Popover>
          <ButtonBase
            chrome="action"
            type="button"
            onClick={handleDownloadLogs}
            className={SETTINGS_BUTTON_SECONDARY}
          >
            <Download className="h-4 w-4" aria-hidden="true" />
            Download
          </ButtonBase>
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
  );
}
