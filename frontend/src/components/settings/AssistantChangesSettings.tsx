"use client";

import { useCallback, useEffect, useState } from "react";
import { Loader2 } from "lucide-react";

import Button from "@/components/ui/Button";
import { getAssistantChanges, getCalendarOverview } from "@/lib/api";
import { getErrorMessage } from "@/lib/errors";
import {
  formatTimeZoneDayTime,
  getCachedUserTimeZone,
  resolveTimeZone,
} from "@/lib/timezone";
import type { AssistantChange, CalendarConnection } from "@/types";

import SettingsBlock from "./SettingsBlock";
import SettingsCallout from "./SettingsCallout";
import SettingsCard from "./SettingsCard";
import SettingsStatusBadge, { type SettingsStatusBadgeTone } from "./SettingsStatusBadge";

const PAGE_SIZE = 20;

const ACTIONS: Record<AssistantChange["action"], string> = {
  create: "Created",
  update: "Changed",
  cancel: "Cancelled",
  respond: "Answered",
};

const OUTCOMES: Record<AssistantChange["outcome"], { label: string; tone: SettingsStatusBadgeTone }> = {
  succeeded: { label: "Done", tone: "success" },
  pending: { label: "In progress", tone: "neutral" },
  refused: { label: "Refused", tone: "warning" },
  failed: { label: "Failed", tone: "error" },
  uncertain: { label: "Unconfirmed", tone: "warning" },
};

function when(value: string | null | undefined, timeZone: string): string {
  if (!value) {
    return "";
  }
  return formatTimeZoneDayTime(new Date(value), timeZone);
}

/**
 * The change log of what assistants did to the user's calendars through the
 * MCP connector, newest first. Rows are kept 12 months.
 */
export default function AssistantChangesSettings() {
  const [connections, setConnections] = useState<CalendarConnection[]>([]);
  const [items, setItems] = useState<AssistantChange[]>([]);
  const [total, setTotal] = useState(0);
  const [connectionId, setConnectionId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const timeZone = resolveTimeZone(getCachedUserTimeZone());

  const load = useCallback(
    async (skip: number) => {
      setLoading(true);
      setError(null);
      try {
        const page = await getAssistantChanges({ connectionId, skip, limit: PAGE_SIZE });
        setItems((current) => (skip === 0 ? page.items : [...current, ...page.items]));
        setTotal(page.total);
      } catch (caught: unknown) {
        setError(getErrorMessage(caught, "Could not load the changes assistants made."));
      } finally {
        setLoading(false);
      }
    },
    [connectionId],
  );

  useEffect(() => {
    void load(0);
  }, [load]);

  // The accounts, to filter by; the list works without them.
  useEffect(() => {
    getCalendarOverview()
      .then((overview) => setConnections(overview.connections))
      .catch(() => setConnections([]));
  }, []);

  return (
    <SettingsCard
      id="integrations-assistant-changes"
      title="Changes made by assistants"
      description="What assistants connected through the MCP connector changed in your calendars, and who they emailed. Kept for 12 months."
    >
      {connections.length > 1 && (
        <SettingsBlock>
          <label className="flex flex-wrap items-center gap-2 text-sm text-contrast-muted">
            Account
            <select
              className="rounded-md border border-surface-border bg-surface px-2 py-1 text-sm text-foreground"
              value={connectionId ?? ""}
              onChange={(event) =>
                setConnectionId(event.target.value ? Number(event.target.value) : null)
              }
            >
              <option value="">All accounts</option>
              {connections.map((connection) => (
                <option key={connection.id} value={connection.id}>
                  {connection.email || connection.display_name || `Account ${connection.id}`}
                </option>
              ))}
            </select>
          </label>
        </SettingsBlock>
      )}

      {error && (
        <SettingsBlock>
          <div role="alert">
            <SettingsCallout tone="error" message={error} />
          </div>
        </SettingsBlock>
      )}

      {!error && !loading && items.length === 0 && (
        <SettingsBlock>
          <p className="text-sm contrast-helper">No assistant has changed your calendars yet.</p>
        </SettingsBlock>
      )}

      {items.length > 0 && (
        <SettingsBlock>
          <ul className="divide-y divide-surface-divider border-y settings-divider">
            {items.map((item) => {
              const outcome = OUTCOMES[item.outcome] ?? OUTCOMES.pending;
              return (
                <li key={item.id} className="flex flex-col gap-1 py-3 text-sm">
                  <p className="flex flex-wrap items-center gap-2 text-foreground">
                    <span className="font-medium">
                      {ACTIONS[item.action] ?? item.action}
                      {item.event_title ? `: ${item.event_title}` : ""}
                    </span>
                    <SettingsStatusBadge tone={outcome.tone}>{outcome.label}</SettingsStatusBadge>
                  </p>
                  <p className="text-xs contrast-helper">
                    {[
                      when(item.created_at, timeZone),
                      item.client_name || "An assistant",
                      item.calendar_name,
                      item.event_start ? `event on ${when(item.event_start, timeZone)}` : null,
                    ]
                      .filter(Boolean)
                      .join(" · ")}
                  </p>
                  {item.emailed.length > 0 ? (
                    <p className="text-xs contrast-helper">
                      Emailed {item.emailed.map((mail) => mail.email).join(", ")}
                    </p>
                  ) : item.notifications_summary ? (
                    <p className="text-xs contrast-helper">{item.notifications_summary}</p>
                  ) : null}
                  {item.error_code && (
                    <p className="text-xs contrast-helper">Reason: {item.error_code.replaceAll("_", " ")}</p>
                  )}
                </li>
              );
            })}
          </ul>
        </SettingsBlock>
      )}

      {(loading || items.length < total) && (
        <SettingsBlock>
          {loading ? (
            <span className="inline-flex items-center gap-2 text-sm contrast-helper">
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              Loading changes...
            </span>
          ) : (
            <Button variant="secondary" onClick={() => void load(items.length)}>
              Show older changes
            </Button>
          )}
        </SettingsBlock>
      )}
    </SettingsCard>
  );
}
