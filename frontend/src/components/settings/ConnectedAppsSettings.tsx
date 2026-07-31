"use client";

import { useCallback, useEffect, useState } from "react";
import { Loader2, Plug, Trash2 } from "lucide-react";

import {
  getConnectedApps,
  revokeConnectedApp,
  type ConnectedApp,
} from "@/lib/api";
import { getErrorMessage } from "@/lib/errors";
import { useNotificationStore } from "@/lib/notificationStore";

import SettingsBlock from "./SettingsBlock";
import SettingsCard from "./SettingsCard";
import SettingsRow from "./SettingsRow";
import { SETTINGS_BUTTON_DANGER } from "./settingsControls";

// Keyed by the grant's full normalised scope string (space-separated,
// sorted), as recorded when the connection was authorised.
const SCOPE_LABELS: Record<string, string> = {
  "mcp:read": "Read-only",
  "mcp:read mcp:write": "Read · Write",
};

function formatTimestamp(value: string | null): string {
  if (!value) {
    return "Never";
  }
  const parsed = new Date(value.endsWith("Z") ? value : `${value}Z`);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleString();
}

export default function ConnectedAppsSettings() {
  const [apps, setApps] = useState<ConnectedApp[] | null>(null);
  const [revokingGrantId, setRevokingGrantId] = useState<string | null>(null);
  const { addNotification } = useNotificationStore();

  const loadApps = useCallback(async () => {
    try {
      setApps(await getConnectedApps());
    } catch (e: unknown) {
      addNotification({
        message: getErrorMessage(e, "Failed to load connected apps"),
        type: "error",
      });
      setApps([]);
    }
  }, [addNotification]);

  useEffect(() => {
    loadApps();
  }, [loadApps]);

  const handleRevoke = async (grantId: string) => {
    setRevokingGrantId(grantId);
    try {
      await revokeConnectedApp(grantId);
      addNotification({ message: "Connection revoked", type: "success" });
      await loadApps();
    } catch (e: unknown) {
      addNotification({
        message: getErrorMessage(e, "Failed to revoke the connection"),
        type: "error",
      });
    } finally {
      setRevokingGrantId(null);
    }
  };

  return (
    <SettingsCard
      id="integrations-connected-apps"
      title="Connected apps"
      description="Assistants connected through the Nojoin MCP connector. They can read your meeting library and, when granted, add or update people in your People library."
    >
      {apps === null ? (
        <SettingsBlock>
          <div className="flex justify-center py-2">
            <Loader2
              className="h-5 w-5 animate-spin text-action-text"
              aria-hidden="true"
            />
          </div>
        </SettingsBlock>
      ) : apps.length === 0 ? (
        <SettingsBlock>
          <p className="text-sm contrast-helper">
            No apps are connected. Add Nojoin as a custom connector from a
            supported assistant using{" "}
            <code className="rounded bg-surface-inset px-1 py-0.5 text-xs">
              {typeof window !== "undefined" ? window.location.origin : ""}/mcp
            </code>
            .
          </p>
        </SettingsBlock>
      ) : (
        apps.map((app) => (
          <SettingsRow
            key={app.grant_id}
            label={app.client_name}
            description={`${SCOPE_LABELS[app.scope] ?? app.scope} · Connected ${formatTimestamp(app.created_at)} · Last used ${formatTimestamp(app.last_used_at)}`}
            icon={<Plug className="h-4 w-4 contrast-icon-muted" aria-hidden="true" />}
            controlClassName="sm:min-w-0 sm:flex sm:justify-end"
          >
            <button
              type="button"
              onClick={() => handleRevoke(app.grant_id)}
              disabled={revokingGrantId === app.grant_id}
              className={SETTINGS_BUTTON_DANGER}
            >
              {revokingGrantId === app.grant_id ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : (
                <Trash2 className="h-4 w-4" aria-hidden="true" />
              )}
              Revoke
            </button>
          </SettingsRow>
        ))
      )}
    </SettingsCard>
  );
}
