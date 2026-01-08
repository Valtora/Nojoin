import { useState, useEffect, useRef } from "react";
import axios from "axios";
import {
  RefreshCw,
  Download,
  Terminal,
  AlertTriangle,
  Play,
  Trash2,
  Settings,
  Pause,
} from "lucide-react";
import api, { API_BASE_URL } from "@/lib/api";

export default function SystemTab() {
  const [logs, setLogs] = useState<string[]>([]);
  const [filteredLogs, setFilteredLogs] = useState<string[]>([]);
  const [selectedContainer, setSelectedContainer] = useState("all");
  const [isConnected, setIsConnected] = useState(false);
  const [isRestarting, setIsRestarting] = useState(false);
  const [showRestartConfirm, setShowRestartConfirm] = useState(false);
  const [logFilter, setLogFilter] = useState("");
  const [logLevel, setLogLevel] = useState("ALL");
  const [autoScroll, setAutoScroll] = useState(true);

  const wsRef = useRef<WebSocket | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

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
    const apiProtocol = API_BASE_URL.startsWith("https") ? "wss:" : "ws:";
    // API_BASE_URL is like "https://localhost:14443/api/v1"
    const urlObj = new URL(API_BASE_URL);

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

  const handleRestart = async () => {
    try {
      setIsRestarting(true);
      setShowRestartConfirm(false);

      await api.post("/system/restart");

      // Poll immediately
      pollHealth();
    } catch (error) {
      console.error("Restart request failed or interrupted:", error);

      // Only alert if it's a client error (4xx), otherwise assume network error means success (server died)
      if (
        axios.isAxiosError(error) &&
        error.response &&
        error.response.status >= 400 &&
        error.response.status < 500
      ) {
        alert(
          "Failed to trigger restart: " +
            (error.response.data.detail || "Unknown error"),
        );
        setIsRestarting(false);
      } else {
        // Assume network error means server is restarting
        pollHealth();
      }
    }
  };

  const pollHealth = () => {
    const interval = setInterval(async () => {
      try {
        // Short timeout to fail fast
        await api.get("/health", { timeout: 2000 });
        // If succeeds, we are back!
        clearInterval(interval);
        setIsRestarting(false);
        window.location.reload();
      } catch {
        // Still down
      }
    }, 3000);
  };

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
    // Attempt rudimentary parsing
    // Format 1: HH:MM:SS LEVEL Message
    // Format 2: Date Time | Message

    // Simple regex for "HH:MM:SS LEVEL"
    const simpleMatch = text.match(
      /^(\d{2}:\d{2}:\d{2})\s+([A-Z]{3,5})\s+(.*)$/,
    );

    if (simpleMatch) {
      const time = simpleMatch[1];
      const level = simpleMatch[2];
      const msg = simpleMatch[3];

      let levelColor = "text-gray-400";
      if (level.includes("INF")) levelColor = "text-green-500";
      if (level.includes("WRN") || level.includes("WARN"))
        levelColor = "text-yellow-500";
      if (level.includes("ERR") || level.includes("FAIL"))
        levelColor = "text-red-500";
      if (level.includes("DBG")) levelColor = "text-blue-500";

      return (
        <div className="flex gap-3 hover:bg-gray-800/50 py-0.5 px-2 -mx-2 rounded">
          <span className="text-gray-500 shrink-0 select-none w-[68px]">
            {time}
          </span>
          <span
            className={`${levelColor} font-bold shrink-0 w-10 select-none text-right`}
          >
            {level}
          </span>
          <span className="break-all whitespace-pre-wrap flex-1">{msg}</span>
        </div>
      );
    }

    const plainText = text;
    let colorClass = "text-gray-300";
    if (plainText.toLowerCase().includes("error")) colorClass = "text-red-400";
    else if (plainText.toLowerCase().includes("warn"))
      colorClass = "text-yellow-400";

    return (
      <div
        className={`break-all whitespace-pre-wrap py-0.5 px-2 -mx-2 hover:bg-gray-800/50 rounded ${colorClass}`}
      >
        {text}
      </div>
    );
  };

  if (isRestarting) {
    return (
      <div className="flex flex-col items-center justify-center p-12 space-y-4">
        <RefreshCw className="w-12 h-12 text-orange-500 animate-spin" />
        <h3 className="text-xl font-medium text-gray-900 dark:text-white">
          System Restarting...
        </h3>
        <p className="text-gray-500">
          Please wait while the containers reboot. The page will reload
          automatically.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-8 animate-in fade-in duration-500">
      {/* System Controls Section */}
      <section>
        <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6 shadow-sm">
          <div className="flex items-center gap-2 mb-4">
            <h3 className="text-lg font-medium text-gray-900 dark:text-white">
              Controls
            </h3>
          </div>

          <div className="bg-gray-50 dark:bg-gray-900/50 rounded-md p-4 border border-gray-200 dark:border-gray-700">
            <h4 className="flex items-center gap-2 text-sm font-medium text-gray-900 dark:text-gray-100 mb-2">
              <RefreshCw className="w-4 h-4 text-orange-500" />
              System Restart
            </h4>
            <p className="text-xs text-gray-500 dark:text-gray-400 mb-4 max-w-2xl">
              This will restart all Nojoin containers (API, Worker, Database,
              etc.). Active tasks will be interrupted. The system will be
              unavailable for approximately 10-20 seconds.
            </p>

            {!showRestartConfirm ? (
              <button
                onClick={() => setShowRestartConfirm(true)}
                className="px-3 py-1.5 bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-300 border border-gray-300 dark:border-gray-600 rounded text-xs font-medium hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors shadow-sm"
              >
                Restart System
              </button>
            ) : (
              <div className="bg-red-50 dark:bg-red-900/10 border border-red-100 dark:border-red-900/30 p-3 rounded-md animate-in slide-in-from-top-2 duration-200">
                <div className="flex items-center gap-3">
                  <AlertTriangle className="w-4 h-4 text-red-600 dark:text-red-400 shrink-0" />
                  <span className="text-xs font-medium text-red-800 dark:text-red-300">
                    Are you sure? All connections will be dropped.
                  </span>
                  <div className="flex gap-2 ml-auto">
                    <button
                      onClick={handleRestart}
                      className="px-2 py-1 bg-red-600 text-white rounded text-xs font-medium hover:bg-red-700 shadow-sm"
                    >
                      Confirm Restart
                    </button>
                    <button
                      onClick={() => setShowRestartConfirm(false)}
                      className="px-2 py-1 bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-300 border border-gray-300 dark:border-gray-600 rounded text-xs hover:bg-gray-50 dark:hover:bg-gray-700"
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      </section>

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
            <button
              onClick={() => {
                /* Open settings? Maybe just visual for now as per screenshot */
              }}
              title="Settings"
              className="p-1.5 text-gray-400 hover:text-white hover:bg-gray-700 rounded transition-colors"
            >
              <Settings className="w-4 h-4" />
            </button>
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
