import { ButtonBase, InputBase } from "@/components/ui/nativeBases";
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
import { cn } from "@/lib/cn";
import SettingsBlock from "./SettingsBlock";
import SettingsCard from "./SettingsCard";
import SettingsStatusBadge from "./SettingsStatusBadge";
import {
  SETTINGS_BUTTON_SECONDARY,
  SETTINGS_INPUT_CLASS,
} from "./settingsControls";
import { loadingButtonProps } from "@/components/ui/loadingButton";

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
      addNotification({ message: "Could not load subscription usage.", type: "error" });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchUsage();
    // fetchUsage is redefined every render; the page and the debounced search
    // term are the only inputs that should start a fetch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, debouncedSearch]);

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <SettingsCard
      id="ai-providers-usage"
      title="Subscription usage"
      description="Tokens each person sent through their own Claude or ChatGPT subscription in the last 7 and 30 days. A subscription reports no remaining balance, so Status shows whether it is being rate-limited."
    >
      <SettingsBlock className="flex flex-col gap-3 @min-[32rem]:flex-row @min-[32rem]:items-center @min-[32rem]:justify-between">
        <div className="relative w-full @min-[32rem]:max-w-xs">
          <Search
            className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 contrast-icon-muted"
            aria-hidden="true"
          />
          <InputBase
            data-ui-fill="control"
            data-ui-disabled-fill="control-disabled"
            data-ui-border="full"
            type="search"
            aria-label="Search people"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setPage(1);
            }}
            placeholder="Search people"
            className={cn(SETTINGS_INPUT_CLASS, "pl-9")}
          />
        </div>
        <ButtonBase
          data-ui-fill="card"
          data-ui-hover="inset"
          data-ui-active="inset"
          data-ui-border="full"
          chrome="action"
          type="button"
          {...loadingButtonProps(loading, undefined, fetchUsage)}
          className={SETTINGS_BUTTON_SECONDARY}
        >
          <RefreshCw
            className={cn("h-4 w-4", loading && "animate-spin")}
            aria-hidden="true"
          />
          Refresh
        </ButtonBase>
      </SettingsBlock>

      <SettingsBlock>
        <table className="settings-table w-full text-left text-sm text-foreground">
          <thead className="border-b settings-divider text-xs font-medium contrast-helper">
            <tr>
              <th className="py-2 pr-4 font-medium">Person</th>
              <th className="py-2 pr-4 text-right font-medium">Last 7 days</th>
              <th className="py-2 pr-4 text-right font-medium">Last 30 days</th>
              <th className="py-2 pr-4 text-right font-medium">All time</th>
              <th className="py-2 pr-4 text-right font-medium">Requests</th>
              <th className="py-2 font-medium">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-surface-divider">
            {loading ? (
              <tr>
                <td colSpan={6} className="py-6 text-center">
                  <Loader2
                    className="mx-auto h-5 w-5 animate-spin contrast-icon-muted"
                    aria-label="Loading"
                  />
                </td>
              </tr>
            ) : rows.length === 0 ? (
              <tr>
                <td colSpan={6} className="py-6 text-center contrast-helper">
                  {debouncedSearch
                    ? "Nobody matches that search."
                    : "Nobody has used their own subscription yet."}
                </td>
              </tr>
            ) : (
              rows.map((row) => {
                const pill = quotaPill(row);
                const lastUsed = parseUtcDate(row.last_used_on);
                return (
                  <tr key={row.user_id}>
                    <td className="py-3 pr-4">
                      <div className="font-medium text-foreground">
                        {row.username}
                      </div>
                      {lastUsed && (
                        <div className="text-xs contrast-helper">
                          Last used {lastUsed.toLocaleDateString()}
                        </div>
                      )}
                    </td>
                    <td data-label="Last 7 days" className="py-3 pr-4 text-right tabular-nums">
                      {formatTokens(row.tokens_7d)}
                    </td>
                    <td data-label="Last 30 days" className="py-3 pr-4 text-right tabular-nums">
                      {formatTokens(row.tokens_30d)}
                    </td>
                    <td data-label="All time" className="py-3 pr-4 text-right tabular-nums">
                      {formatTokens(row.tokens_total)}
                    </td>
                    <td data-label="Requests" className="py-3 pr-4 text-right tabular-nums">
                      {row.requests_total}
                    </td>
                    <td data-label="Status" className="py-3">
                      <SettingsStatusBadge tone={pill.tone}>
                        {pill.label}
                      </SettingsStatusBadge>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>

        {total > 0 && (
          <div className="mt-3 flex items-center justify-between gap-3 border-t pt-3 settings-divider">
            <p className="text-sm contrast-helper">
              {`${(page - 1) * PAGE_SIZE + 1} to ${Math.min(page * PAGE_SIZE, total)} of ${total}`}
            </p>
            <div className="flex gap-2">
              <ButtonBase
                data-ui-fill="card"
                data-ui-hover="inset"
                data-ui-active="inset"
                data-ui-border="full"
                chrome="action"
                type="button"
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page === 1}
                aria-label="Previous page"
                className={cn(SETTINGS_BUTTON_SECONDARY, "h-9 w-9 px-0")}
              >
                <ChevronLeft className="h-4 w-4" aria-hidden="true" />
              </ButtonBase>
              <ButtonBase
                data-ui-fill="card"
                data-ui-hover="inset"
                data-ui-active="inset"
                data-ui-border="full"
                chrome="action"
                type="button"
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                disabled={page >= totalPages}
                aria-label="Next page"
                className={cn(SETTINGS_BUTTON_SECONDARY, "h-9 w-9 px-0")}
              >
                <ChevronRight className="h-4 w-4" aria-hidden="true" />
              </ButtonBase>
            </div>
          </div>
        )}
      </SettingsBlock>
    </SettingsCard>
  );
}
