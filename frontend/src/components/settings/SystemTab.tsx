import { useState, useEffect, useRef, Fragment } from "react";
import { Popover, Transition } from "@headlessui/react";
// import axios from "axios"; // Not used anymore
import {
  Download,
  Terminal,
  Play,
  Trash2,
  Settings,
  Pause,
  Check,
} from "lucide-react";
import api, { API_BASE_URL } from "@/lib/api";
import { useNavigationStore } from "@/lib/store";

export default function SystemTab() {
  const [logs, setLogs] = useState<string[]>([]);
  const [filteredLogs, setFilteredLogs] = useState<string[]>([]);
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

  const containers = [
    "all",
    "nojoin-api",
    "nojoin-worker",
    "nojoin-frontend",
    "nojoin-nginx",
    "nojoin-redis",
    "nojoin-db",
  ];

  const logLevels = ["ALL", "DEBUG", "INFO", "WARN", "ERROR"];

  // Filter logs logic
  useEffect(() => {
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
        // Strict match might be better if we parsed, but simple includes is safer for mixed formats.
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

    setFilteredLogs(result);
  }, [logs, logFilter, logLevel]);

  // Auto-scroll
  useEffect(() => {
    if (autoScroll && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [filteredLogs, autoScroll]);

  // WebSocket for logs
  useEffect(() => {
    setLogs([]); // Clear logs on switch
    // Reset connection state
    setIsConnected(false);

    const token = localStorage.getItem("token");

    // Construct WS URL from API_BASE_URL to match Protocol and Host
    let apiBase = API_BASE_URL;

    // Handle relative URLs (e.g. "/api/v1") by appending to window.location.origin
    if (apiBase.startsWith("/")) {
      apiBase = window.location.origin + apiBase;
    }

    const apiProtocol = apiBase.startsWith("https") ? "wss:" : "ws:";
    const urlObj = new URL(apiBase);

    // We want: wss://<host>:<port>/api/v1/system/logs/live
    const wsUrl = `${apiProtocol}//${urlObj.host}${urlObj.pathname}/system/logs/live?container=${selectedContainer}&token=${token}`;

    const ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      setIsConnected(true);
      // setLogs((prev) => [...prev, "--- Connected to Log Stream ---"]);
    };

    ws.onmessage = (event) => {
      setLogs((prev) => [...prev, event.data]);
    };

    ws.onclose = () => {
      setIsConnected(false);
      // setLogs((prev) => [...prev, "--- Disconnected ---"]);
    };

    ws.onerror = (error) => {
      console.error("WebSocket error:", error);
      setLogs((prev) => [
        ...prev,
        "--- Connection Error (Check Console) - Ensure API is reachable ---",
      ]);
    };

    wsRef.current = ws;

    return () => {
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
    } catch (error) {
      console.error("Download failed", error);
      alert("Failed to download logs.");
    }
  };

  const LogLine = ({ text }: { text: string }) => {
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
    let levelColor = "text-green-500";
    const upperContent = content.toUpperCase();

    // Check for specific levels (overrides default INFO)
    if (upperContent.includes("WARN") || upperContent.includes("WRN")) {
      level = "WARN";
      levelColor = "text-yellow-500";
    } else if (
      upperContent.includes("ERR") ||
      upperContent.includes("FAIL") ||
      upperContent.includes("CRIT")
    ) {
      level = "ERROR";
      levelColor = "text-red-500";
    } else if (upperContent.includes("DBG") || upperContent.includes("DEBUG")) {
      level = "DEBUG";
      levelColor = "text-blue-500";
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
      <div className="flex gap-3 hover:bg-gray-800/50 py-0.5 px-2 -mx-2 rounded">
        {logShowTimestamps && (
          <span
            className="text-gray-500 shrink-0 select-none w-[68px] font-mono"
            title={timestamp}
          >
            {timeDisplay}
          </span>
        )}

        {/* Container Name */}
        <span
          className="text-gray-600 shrink-0 select-none w-[110px] truncate text-right"
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
          className={`break-all flex-1 ${logWordWrap ? "whitespace-pre-wrap" : "whitespace-nowrap"}`}
        >
          {content}
        </span>
      </div>
    );
  };

  return (
    <div className="space-y-8 animate-in fade-in duration-500">
      {/* Logs Section */}
      <section>
        <div className="flex items-center gap-2 mb-4">
          <span className="text-gray-400 dark:text-gray-500 font-mono text-lg">
            {">_"}
          </span>
          <h3 className="text-lg font-medium text-gray-900 dark:text-white">
            Logs
          </h3>
        </div>

        <div className="bg-[#0d1117] rounded-lg border border-gray-800 shadow-xl overflow-hidden flex flex-col h-[600px]">
          {/* Toolbar */}
          <div className="flex items-center gap-3 p-2 bg-[#161b22] border-b border-gray-800">
            {/* Container Select */}
            <div className="relative">
              <select
                value={selectedContainer}
                onChange={(e) => setSelectedContainer(e.target.value)}
                className="appearance-none bg-[#0d1117] text-gray-300 text-xs font-medium px-3 py-1.5 pr-8 rounded border border-gray-700 focus:border-blue-500 focus:ring-1 focus:ring-blue-500 outline-none w-40"
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
                  className={`block w-2 h-2 rounded-full ${isConnected ? "bg-green-500" : "bg-gray-500"}`}
                />
              </div>
            </div>

            {/* Log Level Select */}
            <div className="relative">
              <select
                value={logLevel}
                onChange={(e) => setLogLevel(e.target.value)}
                className="appearance-none bg-[#0d1117] text-gray-300 text-xs font-medium px-3 py-1.5 pr-6 rounded border border-gray-700 focus:border-blue-500 focus:ring-1 focus:ring-blue-500 outline-none w-20 text-center"
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
                className="w-full bg-[#0d1117] text-gray-300 text-xs px-3 py-1.5 rounded border border-gray-700 focus:border-blue-500 focus:ring-1 focus:ring-blue-500 outline-none placeholder-gray-600 font-mono"
                value={logFilter}
                onChange={(e) => setLogFilter(e.target.value)}
              />
            </div>

            {/* Actions */}
            <button
              onClick={() => setAutoScroll(!autoScroll)}
              title={autoScroll ? "Pause Auto-scroll" : "Resume Auto-scroll"}
              className={`p-1.5 rounded transition-colors ${autoScroll ? "text-green-500 hover:bg-gray-700" : "text-gray-400 hover:text-white hover:bg-gray-700"}`}
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
              className="p-1.5 text-gray-400 hover:text-white hover:bg-gray-700 rounded transition-colors"
            >
              <Trash2 className="w-4 h-4" />
            </button>
            <Popover className="relative">
              <Popover.Button
                className="p-1.5 text-gray-400 hover:text-white hover:bg-gray-700 rounded transition-colors outline-none"
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
                <Popover.Panel className="absolute right-0 z-10 mt-2 w-56 origin-top-right rounded-md bg-[#1c2128] shadow-lg ring-1 ring-black ring-opacity-5 focus:outline-none border border-gray-700 p-1">
                  <div className="p-1 space-y-1">
                    <button
                      onClick={toggleLogShowTimestamps}
                      className="group flex w-full items-center justify-between rounded-md px-2 py-2 text-sm text-gray-300 hover:bg-gray-800 hover:text-white"
                    >
                      <span>Show Timestamps</span>
                      {logShowTimestamps && (
                        <Check className="h-4 w-4 text-orange-500" />
                      )}
                    </button>
                    <button
                      onClick={toggleLogWordWrap}
                      className="group flex w-full items-center justify-between rounded-md px-2 py-2 text-sm text-gray-300 hover:bg-gray-800 hover:text-white"
                    >
                      <span>Word Wrap</span>
                      {logWordWrap && (
                        <Check className="h-4 w-4 text-orange-500" />
                      )}
                    </button>
                  </div>
                </Popover.Panel>
              </Transition>
            </Popover>
            <div className="w-px h-4 bg-gray-700 mx-1"></div>
            <button
              onClick={handleDownloadLogs}
              className="flex items-center gap-2 px-3 py-1.5 bg-[#21262d] hover:bg-[#30363d] text-gray-300 text-xs font-medium rounded border border-gray-600 transition-colors"
            >
              <Download className="w-3.5 h-3.5" />
              Download
            </button>
          </div>

          {/* Log Output */}
          <div
            ref={scrollRef}
            className="flex-1 overflow-y-auto p-4 font-mono text-[11px] leading-relaxed text-gray-300 scrollbar-thin scrollbar-thumb-gray-700 scrollbar-track-transparent"
          >
            {filteredLogs.length === 0 ? (
              <div className="h-full flex flex-col items-center justify-center opacity-30 select-none">
                <Terminal className="w-12 h-12 mb-4" />
                <p>No logs to display</p>
              </div>
            ) : (
              filteredLogs.map((log, i) => <LogLine key={i} text={log} />)
            )}
          </div>

          {/* Status Footer */}
          <div className="px-3 py-1 bg-[#161b22] border-t border-gray-800 flex items-center justify-between text-[10px] text-gray-500 font-mono">
            <span>{isConnected ? "Connected" : "Disconnected"}</span>
            <span>{filteredLogs.length} lines</span>
          </div>
        </div>
      </section>
    </div>
  );
}
