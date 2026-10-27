"use client";

import { ButtonBase } from "@/components/ui/nativeBases";
import { useCallback, useEffect, useState } from "react";
import { Loader2, Trash2 } from "lucide-react";

import {
  getConnectedApps,
  revokeConnectedApp,
  type ConnectedApp,
} from "@/lib/api";
import { getErrorMessage } from "@/lib/errors";
import { useNotificationStore } from "@/lib/notificationStore";
import { parseServerTimestamp } from "@/lib/serverTimestamp";
import { formatTimeZoneLocale } from "@/lib/timezone";
import { useUserTimeZone } from "@/lib/useUserTimeZone";

import SettingsBlock from "./SettingsBlock";
import SettingsCard from "./SettingsCard";
import SettingsRow from "./SettingsRow";
import { SETTINGS_BUTTON_DANGER, SETTINGS_CONTROL_END } from "./settingsControls";

// Keyed by the grant's full normalised scope string (space-separated,
// sorted), as recorded when the connection was authorised.
const SCOPE_LABELS: Record<string, string> = {
  "mcp:read": "Read-only",
  "mcp:read mcp:write": "Read · Write",
};

function formatTimestamp(value: string | null, timeZone: string): string {
  if (!value) {
    return "Never";
  }
  const parsed = parseServerTimestamp(value);
  if (!parsed) {
    return value;
  }
  return formatTimeZoneLocale(parsed, timeZone);
}

export default function ConnectedAppsSettings() {
  const timeZone = useUserTimeZone();
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
      description="Assistants connected through the Nojoin MCP connector. They can read your meeting library, and, when write access is granted, change it: correcting transcripts, regenerating notes, re-running processing, organising recordings, managing tasks, attaching documents, and maintaining your People library. Nothing they do is permanent, and edits they make are labelled as theirs in the transcript."
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
            description={`${SCOPE_LABELS[app.scope] ?? app.scope} · Connected ${formatTimestamp(app.created_at, timeZone)} · Last used ${formatTimestamp(app.last_used_at, timeZone)}`}
            controlClassName={SETTINGS_CONTROL_END}
          >
            <ButtonBase
              data-ui-fill="card"
              data-ui-hover="inset"
              data-ui-active="inset"
              data-ui-border="full"
              chrome="action"
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
            </ButtonBase>
          </SettingsRow>
        ))
      )}
    </SettingsCard>
  );
}
