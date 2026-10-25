"use client";

import { ButtonBase } from "@/components/ui/nativeBases";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
  startCalendarAuthorisation,
  syncCalendarConnection,
  updateCalendarColor,
  updateCalendarSelection,
} from "@/lib/api";
import { getErrorMessage } from "@/lib/errors";
import { useNotificationStore } from "@/lib/notificationStore";
import {
  CheckCircle2,
  ExternalLink,
  Loader2,
  RefreshCw,
  ShieldAlert,
  Trash2,
  Zap,
} from "lucide-react";
import {
  CALENDAR_CONNECT_LABELS as CONNECT_LABELS,
  describeCalendarConnectFailure,
} from "./calendarConnectResult";
import SettingsBlock from "./SettingsBlock";
import SettingsCallout from "./SettingsCallout";
import SettingsCard from "./SettingsCard";
import { useSettingsContext } from "./SettingsProvider";
import SettingsRow from "./SettingsRow";
import SettingsStatusBadge from "./SettingsStatusBadge";
import {
  SETTINGS_BUTTON_DANGER,
  SETTINGS_BUTTON_PRIMARY,
  SETTINGS_BUTTON_SECONDARY,
  SETTINGS_CONTROL_END,
} from "./settingsControls";
import { cn } from "@/lib/cn";
import { loadingButtonProps } from "@/components/ui/loadingButton";

const PROVIDER_LABELS: Record<CalendarProvider, string> = {
  google: "Google",
  microsoft: "Microsoft",
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

  return { className: "bg-action", style: undefined };
}


export default function CalendarConnectionsSettings() {
  const { isAdmin } = useSettingsContext();
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const [overview, setOverview] = useState<CalendarOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const { addNotification } = useNotificationStore();
  const handledCallbackRef = useRef(false);
  // A sign-in arrives by a full page load, so the outcome it carries in the
  // query string is read once, when the card first renders. The effect below
  // then strips it from the address bar.
  const [connectFailure, setConnectFailure] = useState(() =>
    describeCalendarConnectFailure(
      searchParams.get("calendar"),
      searchParams.get("provider"),
      searchParams.get("reason"),
    ),
  );

  const loadOverview = useCallback(async () => {
    setLoading(true);
    try {
      setOverview(await getCalendarOverview());

        } catch (error: unknown) {
      addNotification({
        type: "error",
        message:
          getErrorMessage(error, "Failed to load calendar connections"),
      });
    } finally {
      setLoading(false);
    }
  }, [addNotification]);

  useEffect(() => {
    void loadOverview();
  }, [loadOverview]);

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
    nextSearchParams.delete("reason");
    const nextQuery = nextSearchParams.toString();
    const providerLabel = CONNECT_LABELS[provider as CalendarProvider] || "calendar";
    // A failure is shown in the card, where it stays until dismissed.
    if (calendarStatus === "success") {
      addNotification({
        type: "success",
        message: `${providerLabel} connected successfully`,
      });
    } else if (calendarStatus === "cancelled") {
      addNotification({
        type: "error",
        message: `${providerLabel} connection was cancelled.`,
      });
    }
    router.replace(nextQuery ? `${pathname}?${nextQuery}` : pathname, {
      scroll: false,
    });
    void loadOverview();
  }, [addNotification, loadOverview, pathname, router, searchParams]);

  const configuredProviders = useMemo(
    () => overview?.providers.filter((provider) => provider.configured) ?? [],
    [overview],
  );

  const handleConnect = async (provider: CalendarProvider) => {
    setConnectFailure(null);
    setBusyKey(`connect:${provider}`);
    try {
      const { authorisation_url } = await startCalendarAuthorisation(provider);
      window.location.assign(authorisation_url);

        } catch (error: unknown) {
      addNotification({
        type: "error",
        message:
          getErrorMessage(error, `Could not start ${CONNECT_LABELS[provider] || "calendar"} sign-in`),
      });
      setBusyKey(null);
    }
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

        } catch (error: unknown) {
      addNotification({
        type: "error",
        message:
          getErrorMessage(error, "Failed to update selected calendars"),
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

        } catch (error: unknown) {
      addNotification({
        type: "error",
        message:
          getErrorMessage(error, "Failed to update calendar colour"),
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

        } catch (error: unknown) {
      addNotification({
        type: "error",
        message: getErrorMessage(error, "Calendar sync failed"),
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

        } catch (error: unknown) {
      addNotification({
        type: "error",
        message:
          getErrorMessage(error, "Failed to disconnect calendar account"),
      });
    } finally {
      setBusyKey(null);
    }
  };

  return (
    <SettingsCard
      id="integrations-calendars"
      title="Calendars"
      description="Connect a Google or Outlook account, approve access on its own sign-in page, then choose which calendars Nojoin shows you."
    >
      {connectFailure && (
        <SettingsBlock>
          <div role="alert">
            <SettingsCallout tone="error" title={connectFailure.title}>
              <p>{connectFailure.message}</p>
              {connectFailure.hint && <p className="mt-2">{connectFailure.hint}</p>}
              {connectFailure.reasonText && (
                <p className="mt-2 text-xs">{connectFailure.reasonText}</p>
              )}
              <ButtonBase
                type="button"
                onClick={() => setConnectFailure(null)}
                className="mt-2 font-semibold underline underline-offset-2"
              >
                Dismiss
              </ButtonBase>
            </SettingsCallout>
          </div>
        </SettingsBlock>
      )}

      {loading ? (
        <SettingsBlock>
          <div className="flex items-center gap-2 text-sm contrast-helper">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            Loading calendar connections...
          </div>
        </SettingsBlock>
      ) : (
        <>
          {overview?.providers.map((provider) => {
            const isConnecting = busyKey === `connect:${provider.provider}`;
            return (
              <SettingsRow
                key={provider.provider}
                label={CONNECT_LABELS[provider.provider]}
                description={
                  provider.configured
                    ? `You will be redirected to ${PROVIDER_LABELS[provider.provider]} to sign in and approve access.`
                    : isAdmin
                      ? "Needs this installation's app credentials, under Advanced."
                      : "Not set up on this installation yet."
                }
                controlClassName={SETTINGS_CONTROL_END}
              >
                <ButtonBase
                  chrome="action"
                  type="button"
                  {...loadingButtonProps(isConnecting, !provider.configured, () => handleConnect(provider.provider))}
                  className={SETTINGS_BUTTON_PRIMARY}
                >
                  {isConnecting ? (
                    <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                  ) : (
                    <ExternalLink className="h-4 w-4" aria-hidden="true" />
                  )}
                  Connect
                </ButtonBase>
              </SettingsRow>
            );
          })}

          {configuredProviders.length === 0 && (
            <SettingsBlock>
              {isAdmin ? (
                <SettingsCallout tone="warning">
                  <p>
                    Calendar sign-in needs a Google or Microsoft app registered
                    for this installation. Once its credentials are saved,
                    everyone can connect their own calendar.
                  </p>
                  <a
                    href="#integrations-calendar-providers"
                    className="mt-2 inline-flex font-semibold underline underline-offset-2"
                  >
                    Add app credentials
                  </a>
                </SettingsCallout>
              ) : (
                <SettingsCallout
                  tone="info"
                  message="Calendar sign-in is not set up on this installation yet. Ask an administrator to add it."
                />
              )}
            </SettingsBlock>
          )}

          {overview?.connections.length
            ? overview.connections.map((connection) => {
                const isBusy = busyKey?.endsWith(`:${connection.id}`) ?? false;
                return (
                  <SettingsBlock
                    key={connection.id}
                    label={
                      <span className="flex flex-wrap items-center gap-2">
                        {connection.email || connection.display_name || "Connected account"}
                        <SettingsStatusBadge tone="neutral">
                          {PROVIDER_LABELS[connection.provider]}
                        </SettingsStatusBadge>
                        {connection.push_active && (
                          <SettingsStatusBadge tone="success">
                            <Zap className="mr-1 h-3 w-3" aria-hidden="true" />
                            Live sync
                          </SettingsStatusBadge>
                        )}
                      </span>
                    }
                    description={`${STATUS_LABELS[connection.sync_status]}. ${connection.selected_calendar_count} calendar${connection.selected_calendar_count === 1 ? "" : "s"} selected.`}
                    aside={
                      <div className="flex flex-wrap gap-2">
                        <ButtonBase
                          chrome="action"
                          type="button"
                          {...loadingButtonProps(busyKey === `sync:${connection.id}`, isBusy, () => handleManualSync(connection.id))}
                          className={SETTINGS_BUTTON_SECONDARY}
                        >
                          {busyKey === `sync:${connection.id}` ? (
                            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                          ) : (
                            <RefreshCw className="h-4 w-4" aria-hidden="true" />
                          )}
                          Sync now
                        </ButtonBase>
                        <ButtonBase
                          chrome="action"
                          type="button"
                          {...loadingButtonProps(busyKey === `disconnect:${connection.id}`, isBusy, () => handleDisconnect(connection.id))}
                          className={SETTINGS_BUTTON_DANGER}
                        >
                          {busyKey === `disconnect:${connection.id}` ? (
                            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                          ) : (
                            <Trash2 className="h-4 w-4" aria-hidden="true" />
                          )}
                          Disconnect
                        </ButtonBase>
                      </div>
                    }
                  >
                    {connection.sync_error && (
                      <SettingsCallout tone="warning" className="mb-3">
                        <span className="flex items-start gap-2">
                          <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
                          <span>{connection.sync_error}</span>
                        </span>
                      </SettingsCallout>
                    )}

                    <ul className="divide-y divide-surface-divider border-y settings-divider">
                      {connection.calendars.map((calendar) => {
                        const labelId = `calendar-${calendar.id}-label`;
                        return (
                          <li
                            key={calendar.id}
                            className="flex flex-col gap-3 py-3 @min-[30rem]:flex-row @min-[30rem]:items-center @min-[30rem]:justify-between"
                          >
                            <div className="min-w-0">
                              <p className="flex flex-wrap items-center gap-2 text-sm text-foreground">
                                <span id={labelId} className="font-medium">
                                  {calendar.name}
                                </span>
                                {calendar.is_primary && (
                                  <SettingsStatusBadge tone="neutral">
                                    <CheckCircle2 className="mr-1 h-3 w-3" aria-hidden="true" />
                                    Primary
                                  </SettingsStatusBadge>
                                )}
                              </p>
                              {calendar.description && (
                                <p className="mt-1 line-clamp-2 text-xs contrast-helper">
                                  {calendar.description}
                                </p>
                              )}
                            </div>

                            <div className="flex flex-wrap items-center gap-2 @min-[30rem]:shrink-0">
                              {busyKey === `colour:${calendar.id}` ? (
                                <span className="inline-flex items-center gap-2 text-xs contrast-helper">
                                  <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                                  Saving
                                </span>
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
                                      // A button, so the picker the wrapper opens
                                      // on click is reachable from the keyboard.
                                      <ButtonBase
                                        chrome="action"
                                        type="button"
                                        className={cn(SETTINGS_BUTTON_SECONDARY, "px-3")}
                                        aria-label={`Colour for ${calendar.name}`}
                                      >
                                        <span
                                          aria-hidden="true"
                                          className={`h-3 w-3 rounded-full ${indicator.className}`}
                                          style={indicator.style}
                                        />
                                        {calendar.custom_colour ? "Custom colour" : "Calendar colour"}
                                      </ButtonBase>
                                    );
                                  })()}
                                />
                              )}

                              {calendar.custom_colour && busyKey !== `colour:${calendar.id}` && (
                                <ButtonBase
                                  chrome="action"
                                  type="button"
                                  onClick={() =>
                                    void handleCalendarColourChange(
                                      connection.id,
                                      calendar.id,
                                      null,
                                    )
                                  }
                                  className={SETTINGS_BUTTON_SECONDARY}
                                >
                                  Reset colour
                                </ButtonBase>
                              )}

                              <Switch
                                checked={calendar.is_selected}
                                aria-labelledby={labelId}
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
                          </li>
                        );
                      })}
                    </ul>
                  </SettingsBlock>
                );
              })
            : configuredProviders.length > 0 && (
            <SettingsBlock>
              <p className="text-sm contrast-helper">
                No calendar accounts are connected yet.
              </p>
            </SettingsBlock>
            )}
        </>
      )}
    </SettingsCard>
  );
}
