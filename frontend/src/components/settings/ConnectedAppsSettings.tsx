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

import SettingsSection from "./SettingsSection";

const SCOPE_LABELS: Record<string, string> = {
  "mcp:read": "Read-only",
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
    <SettingsSection
      eyebrow="Integrations"
      title="Connected Apps"
      description="External assistants connected through the Nojoin MCP connector, such as Claude. Connections are read-only and can be revoked at any time."
      width="compact"
    >
      {apps === null ? (
        <div className="flex justify-center py-6">
          <Loader2 className="h-5 w-5 animate-spin text-orange-600" />
        </div>
      ) : apps.length === 0 ? (
        <p className="text-sm text-gray-500 dark:text-gray-400">
          No apps are connected. Add Nojoin as a custom connector from a
          supported assistant using{" "}
          <code className="rounded bg-gray-100 px-1 py-0.5 text-xs dark:bg-gray-800">
            {typeof window !== "undefined" ? window.location.origin : ""}/mcp
          </code>
          .
        </p>
      ) : (
        <ul className="space-y-3">
          {apps.map((app) => (
            <li
              key={app.grant_id}
              className="flex items-center justify-between gap-4 rounded-xl border border-gray-200 bg-white px-4 py-3 dark:border-gray-700 dark:bg-gray-900"
            >
              <div className="flex items-center gap-3 min-w-0">
                <Plug className="h-5 w-5 shrink-0 text-orange-600" />
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium text-gray-900 dark:text-white">
                    {app.client_name}
                  </p>
                  <p className="text-xs text-gray-500 dark:text-gray-400">
                    {SCOPE_LABELS[app.scope] ?? app.scope} · Connected{" "}
                    {formatTimestamp(app.created_at)} · Last used{" "}
                    {formatTimestamp(app.last_used_at)}
                  </p>
                </div>
              </div>
              <button
                type="button"
                onClick={() => handleRevoke(app.grant_id)}
                disabled={revokingGrantId === app.grant_id}
                className="inline-flex items-center gap-1.5 rounded-lg border border-gray-300 px-3 py-1.5 text-xs font-medium text-gray-700 transition hover:border-red-400 hover:text-red-600 disabled:opacity-60 dark:border-gray-700 dark:text-gray-300 dark:hover:border-red-500 dark:hover:text-red-400"
              >
                {revokingGrantId === app.grant_id ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Trash2 className="h-3.5 w-3.5" />
                )}
                Revoke
              </button>
            </li>
          ))}
        </ul>
      )}
    </SettingsSection>
  );
}
