import { useEffect, useState } from "react";
import {
  ChevronLeft,
  ChevronRight,
  Loader2,
  RefreshCw,
  Search,
} from "lucide-react";

import { CliUsageRow } from "@/types";
import { getCliUsageOverview } from "@/lib/api/cliOauth";
import { useNotificationStore } from "@/lib/notificationStore";
import SettingsBlock from "./SettingsBlock";
import SettingsCallout from "./SettingsCallout";
import SettingsCard from "./SettingsCard";

const PAGE_SIZE = 10;

/** Compact token count, e.g. 1.2M / 340K / 512. */
function formatTokens(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return String(value);
}

// Backend datetimes are naive UTC; ensure Date parses them as UTC.
function parseUtcDate(value?: string | null): Date | null {
  if (!value) return null;
  const iso = /[zZ]|[+-]\d\d:?\d\d$/.test(value) ? value : `${value}Z`;
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : date;
}

type QuotaTone = "success" | "warning" | "error" | "neutral";

const TONE_CLASS: Record<QuotaTone, string> = {
  success:
    "bg-status-success-bg text-status-success-fg",
  warning: "bg-status-warning-bg text-status-warning-fg",
  error: "bg-status-danger-bg text-status-danger-fg",
  neutral: "bg-surface-inset text-foreground",
};

/**
 * Map a row to a quota pill. A Claude subscription exposes no live remaining
 * quota, so this reflects the rate-limit signal (status + reset time) plus the
 * best-effort utilisation percentage of the current window when known.
 */
function quotaPill(row: CliUsageRow): { label: string; tone: QuotaTone } {
  const limitedUntil = parseUtcDate(row.usage_limited_until);
  const util =
    typeof row.utilization === "number"
      ? Math.round(row.utilization * 100)
      : null;

  if (limitedUntil && limitedUntil.getTime() > Date.now()) {
    return {
      label: `Limited until ${limitedUntil.toLocaleString()}`,
      tone: "error",
    };
  }
  if (!row.connected) {
    return { label: "Not connected", tone: "neutral" };
  }
  if (row.rate_limit_status === "allowed_warning") {
    return {
      label: util !== null ? `Approaching (${util}%)` : "Approaching",
      tone: "warning",
    };
  }
  return { label: util !== null ? `OK (${util}%)` : "OK", tone: "success" };
}

/**
 * Admin-only table of per-user CLI (Claude subscription) token usage and
 * rate-limit status. Read-only; the data is written by the worker as users run
 * inference through their own subscriptions.
 */
export default function CliUsageTab() {
  const [rows, setRows] = useState<CliUsageRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const { addNotification } = useNotificationStore();

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedSearch(search), 500);
    return () => clearTimeout(timer);
  }, [search]);

  const fetchUsage = async () => {
    setLoading(true);
    try {
      const data = await getCliUsageOverview(
        (page - 1) * PAGE_SIZE,
        PAGE_SIZE,
        debouncedSearch,
      );
      setRows(data.items);
      setTotal(data.total);
    } catch {
      addNotification({ message: "Failed to load CLI usage", type: "error" });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchUsage();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, debouncedSearch]);

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <SettingsCard
      id="ai-providers-usage"
      title="Usage and Quota"
      description="Per-user subscription token usage and rate-limit status, across Claude and ChatGPT."
    >
      <SettingsBlock contentClassName="space-y-4">
      <SettingsCallout tone="info">
        Tokens are what Nojoin sent through each user&apos;s own Claude
        subscription. A subscription exposes no live remaining-quota figure, so
        status shows the rate-limit signal (OK, approaching, or limited), not a
        balance.
      </SettingsCallout>

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="relative w-full sm:max-w-xs">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-contrast-helper" />
          <input
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setPage(1);
            }}
            placeholder="Search users..."
            className="w-full pl-9 pr-4 py-2 text-sm border border-control-border rounded-xl bg-surface-card text-foreground focus:ring-2 focus:ring-action focus:border-transparent"
          />
        </div>
        <button
          onClick={fetchUsage}
          disabled={loading}
          className="inline-flex items-center justify-center gap-2 rounded-xl border border-control-border px-4 py-2 text-sm font-semibold text-contrast-muted transition hover:bg-surface-inset disabled:opacity-50"
        >
          <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} />
          Refresh
        </button>
      </div>

      <SettingsBlock className="px-0 pb-0 sm:px-0" contentClassName="overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm text-foreground whitespace-nowrap">
            <thead className="bg-surface-inset text-foreground uppercase font-medium">
              <tr>
                <th className="px-4 py-3">User</th>
                <th className="px-4 py-3 text-right">Last 7 days</th>
                <th className="px-4 py-3 text-right">Last 30 days</th>
                <th className="px-4 py-3 text-right">Lifetime</th>
                <th className="px-4 py-3 text-right">Requests</th>
                <th className="px-4 py-3">Quota status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-control-border">
              {loading ? (
                <tr>
                  <td colSpan={6} className="p-4 text-center">
                    <Loader2 className="w-5 h-5 animate-spin mx-auto" />
                  </td>
                </tr>
              ) : rows.length === 0 ? (
                <tr>
                  <td
                    colSpan={6}
                    className="p-6 text-center text-contrast-helper"
                  >
                    No CLI subscription usage recorded yet.
                  </td>
                </tr>
              ) : (
                rows.map((row) => {
                  const pill = quotaPill(row);
                  const lastUsed = parseUtcDate(row.last_used_on);
                  return (
                    <tr
                      key={row.user_id}
                      className="hover:bg-surface-inset"
                    >
                      <td className="px-4 py-3">
                        <div className="font-medium text-foreground">
                          {row.username}
                        </div>
                        {lastUsed && (
                          <div className="text-xs text-contrast-helper">
                            Last used {lastUsed.toLocaleDateString()}
                          </div>
                        )}
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums">
                        {formatTokens(row.tokens_7d)}
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums">
                        {formatTokens(row.tokens_30d)}
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums">
                        {formatTokens(row.tokens_total)}
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums">
                        {row.requests_total}
                      </td>
                      <td className="px-4 py-3">
                        <span
                          className={`px-2 py-0.5 rounded text-xs ${TONE_CLASS[pill.tone]}`}
                        >
                          {pill.label}
                        </span>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
        <div className="flex items-center justify-between border-t border-surface-border px-4 py-3">
          <div className="text-sm contrast-helper">
            Showing {rows.length > 0 ? (page - 1) * PAGE_SIZE + 1 : 0} to{" "}
            {Math.min(page * PAGE_SIZE, total)} of {total} users
          </div>
          <div className="flex gap-2">
            <button
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page === 1}
              className="p-1 rounded text-contrast-muted hover:bg-surface-inset disabled:opacity-50"
            >
              <ChevronLeft className="w-5 h-5" />
            </button>
            <button
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={page >= totalPages}
              className="p-1 rounded text-contrast-muted hover:bg-surface-inset disabled:opacity-50"
            >
              <ChevronRight className="w-5 h-5" />
            </button>
          </div>
        </div>
      </SettingsBlock>
      </SettingsBlock>
    </SettingsCard>
  );
}
