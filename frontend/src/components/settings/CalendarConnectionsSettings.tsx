"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import ColorPicker from "@/components/ColorPicker";
import { Switch } from "@/components/ui/Switch";
import { COLOR_PALETTE } from "@/lib/constants";
import {
  CalendarConnection,
  CalendarOverview,
  CalendarProvider,
  CalendarSyncStatus,
} from "@/types";
import {
  disconnectCalendarConnection,
  getCalendarOverview,
  getCalendarAuthorisationStartUrl,
  syncCalendarConnection,
  updateCalendarColor,
  updateCalendarSelection,
} from "@/lib/api";
import { useNotificationStore } from "@/lib/notificationStore";
import {
  CalendarRange,
  CheckCircle2,
  ExternalLink,
  Loader2,
  RefreshCw,
  ShieldAlert,
  Trash2,
} from "lucide-react";


const PROVIDER_LABELS: Record<CalendarProvider, string> = {
  google: "Google",
  microsoft: "Microsoft",
};

const CONNECT_LABELS: Record<CalendarProvider, string> = {
  google: "Gmail Calendar",
  microsoft: "Outlook Calendar",
};

const STATUS_LABELS: Record<CalendarSyncStatus, string> = {
  idle: "Idle",
  syncing: "Syncing",
  success: "Up to date",
  error: "Needs attention",
  reauthorisation_required: "Reconnect required",
};


function replaceConnection(
  currentOverview: CalendarOverview | null,
  updatedConnection: CalendarConnection,
): CalendarOverview | null {
  if (!currentOverview) {
    return currentOverview;
  }

  return {
    ...currentOverview,
    connections: currentOverview.connections.map((connection) =>
      connection.id === updatedConnection.id ? updatedConnection : connection,
    ),
  };
}


function getCalendarColourIndicator(colour: string | null | undefined) {
  const paletteColour = colour
    ? COLOR_PALETTE.find((option) => option.key === colour.toLowerCase())
    : null;

  if (paletteColour) {
    return { className: paletteColour.dot, style: undefined };
  }

  if (colour) {
    return { className: "", style: { backgroundColor: colour } };
  }

  return { className: "bg-orange-500", style: undefined };
}


export default function CalendarConnectionsSettings() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const [overview, setOverview] = useState<CalendarOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const { addNotification } = useNotificationStore();
  const handledCallbackRef = useRef(false);

  const loadOverview = async () => {
    setLoading(true);
    try {
      setOverview(await getCalendarOverview());
    } catch (error: any) {
      addNotification({
        type: "error",
        message:
          error.response?.data?.detail ||
          "Failed to load calendar connections",
      });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadOverview();
  }, []);

  useEffect(() => {
    if (handledCallbackRef.current) {
      return;
    }

    const calendarStatus = searchParams.get("calendar");
    const provider = searchParams.get("provider");
    if (!calendarStatus || !provider) {
      return;
    }

    handledCallbackRef.current = true;
    const nextSearchParams = new URLSearchParams(searchParams.toString());
    nextSearchParams.delete("calendar");
    nextSearchParams.delete("provider");
    const nextQuery = nextSearchParams.toString();
    const providerLabel = CONNECT_LABELS[provider as CalendarProvider] || "calendar";
    addNotification({
      type: calendarStatus === "success" ? "success" : "error",
      message: (() => {
        if (calendarStatus === "success") {
          return `${providerLabel} connected successfully`;
        }
        if (calendarStatus === "config-error") {
          return `${providerLabel} sign-in is not configured yet. An administrator must add the installation OAuth app credentials first.`;
        }
        if (calendarStatus === "tenant-config-error") {
          return `${providerLabel} sign-in is configured to use tenant common, but the Microsoft app registration is still single-tenant. In Entra, change Supported account types to include organisational directories and personal Microsoft accounts, or enter a specific tenant ID instead of common.`;
        }
        if (calendarStatus === "cancelled") {
          return `${providerLabel} connection was cancelled.`;
        }
        return `Failed to connect ${providerLabel}`;
      })(),
    });
    router.replace(nextQuery ? `${pathname}?${nextQuery}` : pathname, {
      scroll: false,
    });
    void loadOverview();
  }, [addNotification, pathname, router, searchParams]);

  const configuredProviders = useMemo(
    () => overview?.providers.filter((provider) => provider.configured) ?? [],
    [overview],
  );

  const handleConnect = async (provider: CalendarProvider) => {
    setBusyKey(`connect:${provider}`);
    window.location.assign(getCalendarAuthorisationStartUrl(provider));
  };

  const handleToggleCalendar = async (
    connection: CalendarConnection,
    calendarId: number,
    checked: boolean,
  ) => {
    const currentSelectedIds = connection.calendars
      .filter((calendar) => calendar.is_selected)
      .map((calendar) => calendar.id);
    const nextSelectedIds = checked
      ? [...currentSelectedIds, calendarId]
      : currentSelectedIds.filter((selectedId) => selectedId !== calendarId);

    setBusyKey(`selection:${connection.id}`);
    try {
      const updatedConnection = await updateCalendarSelection(
        connection.id,
        nextSelectedIds,
      );
      setOverview((currentOverview) =>
        replaceConnection(currentOverview, updatedConnection),
      );
    } catch (error: any) {
      addNotification({
        type: "error",
        message:
          error.response?.data?.detail ||
          "Failed to update selected calendars",
      });
    } finally {
      setBusyKey(null);
    }
  };

  const handleCalendarColourChange = async (
    connectionId: number,
    calendarId: number,
    colour: string | null,
  ) => {
    setBusyKey(`colour:${calendarId}`);
    try {
      const updatedConnection = await updateCalendarColor(
        connectionId,
        calendarId,
        colour,
      );
      setOverview((currentOverview) =>
        replaceConnection(currentOverview, updatedConnection),
      );
    } catch (error: any) {
      addNotification({
        type: "error",
        message:
          error.response?.data?.detail ||
          "Failed to update calendar colour",
      });
    } finally {
      setBusyKey(null);
    }
  };

  const handleManualSync = async (connectionId: number) => {
    setBusyKey(`sync:${connectionId}`);
    try {
      const updatedConnection = await syncCalendarConnection(connectionId);
      setOverview((currentOverview) =>
        replaceConnection(currentOverview, updatedConnection),
      );
      addNotification({
        type: "success",
        message: "Calendar sync completed",
      });
    } catch (error: any) {
      addNotification({
        type: "error",
        message: error.response?.data?.detail || "Calendar sync failed",
      });
    } finally {
      setBusyKey(null);
    }
  };

  const handleDisconnect = async (connectionId: number) => {
    if (!window.confirm("Disconnect this calendar account?")) {
      return;
    }

    setBusyKey(`disconnect:${connectionId}`);
    try {
      await disconnectCalendarConnection(connectionId);
      setOverview((currentOverview) =>
        currentOverview
          ? {
              ...currentOverview,
              connections: currentOverview.connections.filter(
                (connection) => connection.id !== connectionId,
              ),
            }
          : currentOverview,
      );
      addNotification({
        type: "success",
        message: "Calendar connection removed",
      });
    } catch (error: any) {
      addNotification({
        type: "error",
        message:
          error.response?.data?.detail ||
          "Failed to disconnect calendar account",
      });
    } finally {
      setBusyKey(null);
    }
  };

  return (
    <div className="bg-white dark:bg-gray-800/50 rounded-lg p-6 border border-gray-300 dark:border-gray-600 space-y-6">
      <div className="flex items-start gap-3">
        <div className="rounded-xl bg-orange-100 p-2 text-orange-700 dark:bg-orange-500/10 dark:text-orange-300">
          <CalendarRange className="w-5 h-5" />
        </div>
        <div>
          <h3 className="text-lg font-medium text-gray-900 dark:text-white">
            Calendar Connections
          </h3>
          <p className="mt-1 text-sm text-gray-600 dark:text-gray-300">
            Connect Gmail or Outlook calendars, approve access in the provider&apos;s
            own consent screen, then choose which calendars Nojoin should sync.
            No personal client IDs or secrets are entered here.
          </p>
        </div>
      </div>

      {loading ? (
        <div className="flex items-center gap-2 text-sm text-gray-600 dark:text-gray-300">
          <Loader2 className="w-4 h-4 animate-spin" />
          Loading calendar connections...
        </div>
      ) : (
        <>
          <div className="grid gap-3 md:grid-cols-2">
            {overview?.providers.map((provider) => {
              const isConnecting = busyKey === `connect:${provider.provider}`;
              return (
                <div
                  key={provider.provider}
                  className="rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900/60 p-4"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <div className="text-sm font-semibold text-gray-900 dark:text-white">
                        {CONNECT_LABELS[provider.provider]}
                      </div>
                      <div className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                        {provider.configured
                          ? `Ready. You will be redirected to ${PROVIDER_LABELS[provider.provider]} to sign in and approve access.`
                          : "Not configured by an administrator"}
                      </div>
                    </div>
                    <button
                      type="button"
                      disabled={!provider.configured || isConnecting}
                      onClick={() => handleConnect(provider.provider)}
                      className="inline-flex items-center gap-2 rounded-md bg-orange-600 px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-orange-700 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {isConnecting ? (
                        <Loader2 className="w-4 h-4 animate-spin" />
                      ) : (
                        <ExternalLink className="w-4 h-4" />
                      )}
                      Connect
                    </button>
                  </div>
                </div>
              );
            })}
          </div>

          {configuredProviders.length === 0 && (
            <div className="rounded-lg border border-dashed border-orange-300 bg-orange-50 px-4 py-3 text-sm text-orange-900 dark:border-orange-500/30 dark:bg-orange-900/20 dark:text-orange-100">
              Calendar providers are not configured yet. An owner or admin must
              add the one-time Google and Microsoft OAuth app credentials first.
              End users only click Connect and approve access in Google or Microsoft.
            </div>
          )}

          {overview?.connections.length ? (
            <div className="space-y-4">
              {overview.connections.map((connection) => {
                const isBusy = busyKey?.endsWith(`:${connection.id}`) ?? false;
                return (
                  <div
                    key={connection.id}
                    className="rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900/60 p-4 space-y-4"
                  >
                    <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                      <div>
                        <div className="flex items-center gap-2">
                          <span className="inline-flex rounded-full bg-orange-100 px-2.5 py-1 text-xs font-medium text-orange-700 dark:bg-orange-500/10 dark:text-orange-300">
                            {PROVIDER_LABELS[connection.provider]}
                          </span>
                          <span className="text-xs text-gray-500 dark:text-gray-400">
                            {STATUS_LABELS[connection.sync_status]}
                          </span>
                        </div>
                        <div className="mt-2 text-base font-semibold text-gray-900 dark:text-white">
                          {connection.email || connection.display_name || "Connected account"}
                        </div>
                        <div className="mt-1 text-sm text-gray-600 dark:text-gray-300">
                          {connection.selected_calendar_count} selected calendar{connection.selected_calendar_count === 1 ? "" : "s"}
                        </div>
                        {connection.sync_error && (
                          <div className="mt-2 flex items-start gap-2 rounded-md border border-orange-300 bg-orange-50 px-3 py-2 text-xs text-orange-900 dark:border-orange-500/30 dark:bg-orange-900/20 dark:text-orange-100">
                            <ShieldAlert className="mt-0.5 w-4 h-4 shrink-0" />
                            <span>{connection.sync_error}</span>
                          </div>
                        )}
                      </div>

                      <div className="flex flex-wrap items-center gap-2">
                        <button
                          type="button"
                          disabled={isBusy}
                          onClick={() => handleManualSync(connection.id)}
                          className="inline-flex items-center gap-2 rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 text-sm font-medium text-gray-700 dark:text-gray-200 transition-colors hover:border-orange-400 hover:text-orange-700 dark:hover:text-orange-300 disabled:opacity-50"
                        >
                          {busyKey === `sync:${connection.id}` ? (
                            <Loader2 className="w-4 h-4 animate-spin" />
                          ) : (
                            <RefreshCw className="w-4 h-4" />
                          )}
                          Sync now
                        </button>
                        <button
                          type="button"
                          disabled={isBusy}
                          onClick={() => handleDisconnect(connection.id)}
                          className="inline-flex items-center gap-2 rounded-md border border-red-200 bg-white px-3 py-2 text-sm font-medium text-red-700 transition-colors hover:bg-red-50 disabled:opacity-50 dark:border-red-500/30 dark:bg-gray-800 dark:text-red-300 dark:hover:bg-red-500/10"
                        >
                          {busyKey === `disconnect:${connection.id}` ? (
                            <Loader2 className="w-4 h-4 animate-spin" />
                          ) : (
                            <Trash2 className="w-4 h-4" />
                          )}
                          Disconnect
                        </button>
                      </div>
                    </div>

                    <div className="grid gap-2 sm:grid-cols-2">
                      {connection.calendars.map((calendar) => (
                        <div
                          key={calendar.id}
                          className={`rounded-xl border px-4 py-4 transition-all ${
                            calendar.is_selected
                              ? "border-orange-300 bg-orange-50/80 shadow-sm shadow-orange-900/5 hover:border-orange-400 hover:bg-orange-50 dark:border-orange-500/30 dark:bg-orange-500/10 dark:hover:border-orange-400/40 dark:hover:bg-orange-500/12"
                              : "border-gray-200 bg-white/80 hover:border-orange-200 hover:bg-orange-50/70 dark:border-gray-700 dark:bg-gray-800/80 dark:hover:border-orange-500/20 dark:hover:bg-gray-800"
                          }`}
                        >
                          <div className="flex items-start justify-between gap-3">
                            <div className="min-w-0 flex-1">
                              <div className="flex items-center gap-2 text-gray-900 dark:text-white">
                                <span className="truncate text-sm font-semibold">
                                  {calendar.name}
                                </span>
                                {calendar.is_primary && (
                                  <span className="inline-flex items-center gap-1 rounded-full bg-orange-100 px-2 py-0.5 text-[11px] font-medium text-orange-700 dark:bg-orange-500/10 dark:text-orange-300">
                                    <CheckCircle2 className="h-3 w-3" />
                                    Primary
                                  </span>
                                )}
                              </div>
                              {calendar.description && (
                                <p className="mt-1 text-xs text-gray-500 dark:text-gray-400 line-clamp-2">
                                  {calendar.description}
                                </p>
                              )}
                            </div>

                            <div className="flex shrink-0 items-center gap-2">
                              {busyKey === `colour:${calendar.id}` ? (
                                <div className="inline-flex items-center gap-2 rounded-full border border-gray-200 bg-white px-3 py-2 text-xs font-medium text-gray-600 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-300">
                                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                  Saving
                                </div>
                              ) : (
                                <ColorPicker
                                  selectedColor={calendar.custom_colour ?? undefined}
                                  onColorSelect={(colorKey) =>
                                    void handleCalendarColourChange(
                                      connection.id,
                                      calendar.id,
                                      colorKey,
                                    )
                                  }
                                  trigger={(() => {
                                    const indicator = getCalendarColourIndicator(
                                      calendar.colour,
                                    );
                                    return (
                                      <div className="inline-flex items-center gap-2 rounded-full border border-gray-200 bg-white px-3 py-2 text-xs font-medium text-gray-700 transition-colors hover:border-orange-300 hover:text-orange-700 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200 dark:hover:border-orange-500/30 dark:hover:text-orange-300">
                                        <span
                                          className={`h-3 w-3 rounded-full ${indicator.className}`}
                                          style={indicator.style}
                                        />
                                        <span>
                                          {calendar.custom_colour ? "Custom" : "Auto"}
                                        </span>
                                      </div>
                                    );
                                  })()}
                                />
                              )}

                              {calendar.custom_colour && busyKey !== `colour:${calendar.id}` && (
                                <button
                                  type="button"
                                  onClick={() =>
                                    void handleCalendarColourChange(
                                      connection.id,
                                      calendar.id,
                                      null,
                                    )
                                  }
                                  className="rounded-full border border-gray-200 px-2.5 py-2 text-xs font-medium text-gray-600 transition-colors hover:border-orange-300 hover:text-orange-700 dark:border-gray-700 dark:text-gray-300 dark:hover:border-orange-500/30 dark:hover:text-orange-300"
                                >
                                  Auto
                                </button>
                              )}

                              <Switch
                                checked={calendar.is_selected}
                                onCheckedChange={(checked) =>
                                  void handleToggleCalendar(
                                    connection,
                                    calendar.id,
                                    checked,
                                  )
                                }
                                disabled={busyKey === `selection:${connection.id}`}
                              />
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                );
              })}
            </div>
          ) : configuredProviders.length > 0 ? (
            <div className="rounded-lg border border-dashed border-gray-300 dark:border-gray-700 px-4 py-5 text-sm text-gray-600 dark:text-gray-300">
              No calendar accounts are connected yet.
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}