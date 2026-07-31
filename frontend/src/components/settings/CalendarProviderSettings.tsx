"use client";

import { useCallback, useEffect, useState } from "react";
import {
  CalendarProvider,
  CalendarProviderConfigUpdate,
  CalendarProviderStatus,
} from "@/types";
import {
  getCalendarProviderStatuses,
  updateCalendarProviderConfiguration,
} from "@/lib/api";
import { getErrorMessage } from "@/lib/errors";
import { useNotificationStore } from "@/lib/notificationStore";
import { Loader2, Save } from "lucide-react";
import SettingsBlock from "./SettingsBlock";
import SettingsRow from "./SettingsRow";
import SettingsCard from "./SettingsCard";


interface ProviderFormState {
  client_id: string;
  client_secret: string;
  tenant_id: string;
  enabled: boolean;
  clear_client_secret: boolean;
  push_enabled: boolean;
}


const EMPTY_FORM: ProviderFormState = {
  client_id: "",
  client_secret: "",
  tenant_id: "common",
  enabled: true,
  clear_client_secret: false,
  push_enabled: false,
};


function buildInitialForms(
  providers: CalendarProviderStatus[],
): Record<CalendarProvider, ProviderFormState> {
  const forms: Record<CalendarProvider, ProviderFormState> = {
    google: { ...EMPTY_FORM, tenant_id: "" },
    microsoft: { ...EMPTY_FORM },
  };

  providers.forEach((provider) => {
    forms[provider.provider] = {
      client_id: provider.client_id || "",
      client_secret: "",
      tenant_id:
        provider.provider === "microsoft"
          ? provider.tenant_id || "common"
          : "",
      enabled: provider.enabled,
      clear_client_secret: false,
      push_enabled: provider.push_enabled,
    };
  });

  return forms;
}


export default function CalendarProviderSettings() {
  const [providers, setProviders] = useState<CalendarProviderStatus[]>([]);
  const [forms, setForms] = useState<Record<CalendarProvider, ProviderFormState>>({
    google: { ...EMPTY_FORM, tenant_id: "" },
    microsoft: { ...EMPTY_FORM },
  });
  const [loading, setLoading] = useState(true);
  const [savingProvider, setSavingProvider] = useState<CalendarProvider | null>(
    null,
  );
  const { addNotification } = useNotificationStore();

  const loadProviders = useCallback(async () => {
    setLoading(true);
    try {
      const providerStatuses = await getCalendarProviderStatuses();
      setProviders(providerStatuses);
      setForms(buildInitialForms(providerStatuses));

        } catch (error: unknown) {
      addNotification({
        type: "error",
        message:
          getErrorMessage(error, "Failed to load calendar provider configuration"),
      });
    } finally {
      setLoading(false);
    }
  }, [addNotification]);

  useEffect(() => {
    void loadProviders();
  }, [loadProviders]);

  const updateForm = (
    provider: CalendarProvider,
    updater: Partial<ProviderFormState>,
  ) => {
    setForms((currentForms) => ({
      ...currentForms,
      [provider]: {
        ...currentForms[provider],
        ...updater,
      },
    }));
  };

  const handleSave = async (provider: CalendarProvider) => {
    setSavingProvider(provider);
    const form = forms[provider];
    const payload: CalendarProviderConfigUpdate = {
      client_id: form.client_id,
      enabled: form.enabled,
      clear_client_secret: form.clear_client_secret,
      push_enabled: form.push_enabled,
    };

    if (provider === "microsoft") {
      payload.tenant_id = form.tenant_id || "common";
    }

    if (form.client_secret) {
      payload.client_secret = form.client_secret;
    }

    try {
      const updatedProvider = await updateCalendarProviderConfiguration(
        provider,
        payload,
      );
      setProviders((currentProviders) =>
        currentProviders.map((currentProvider) =>
          currentProvider.provider === provider ? updatedProvider : currentProvider,
        ),
      );
      setForms((currentForms) => ({
        ...currentForms,
        [provider]: {
          ...currentForms[provider],
          client_id: updatedProvider.client_id || "",
          client_secret: "",
          tenant_id:
            provider === "microsoft"
              ? updatedProvider.tenant_id || "common"
              : "",
          enabled: updatedProvider.enabled,
          clear_client_secret: false,
          push_enabled: updatedProvider.push_enabled,
        },
      }));
      addNotification({
        type: "success",
        message: `${updatedProvider.display_name} provider settings saved`,
      });

        } catch (error: unknown) {
      addNotification({
        type: "error",
        message:
          getErrorMessage(error, `Failed to save ${provider} provider settings`),
      });
    } finally {
      setSavingProvider(null);
    }
  };

  return (
    <SettingsCard
      id="integrations-calendar-providers"
      title="Calendar provider credentials"
      description="The installation's OAuth app credentials for Google and Microsoft. Nobody else pastes these values; everyone else only clicks Connect and completes the provider's own sign-in."
    >
      {loading ? (
        <SettingsBlock>
          <div className="flex items-center gap-2 text-sm contrast-helper">
            <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />
            Loading provider configuration...
          </div>
        </SettingsBlock>
      ) : (
        <SettingsBlock contentClassName="grid gap-4 lg:grid-cols-2">
          {providers.map((provider) => {
            const form = forms[provider.provider];
            const isSaving = savingProvider === provider.provider;
            return (
              <div
                key={provider.provider}
                className="settings-inset space-y-4 rounded-xl p-4"
              >
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="text-base font-semibold text-foreground">
                      {provider.display_name}
                    </div>
                    <div className="mt-1 text-xs contrast-helper">
                      {provider.configured
                        ? `Configured via ${provider.source}`
                        : "Missing OAuth credentials"}
                    </div>
                    {provider.redirect_uri && (
                      <div className="settings-inset rounded-xl p-4 mt-3 space-y-1 text-xs contrast-helper">
                        <div>
                          Register redirect URI:
                        </div>
                        <div className="break-all rounded bg-surface-inset px-2 py-1 font-mono text-[11px] text-contrast-muted">
                          {provider.redirect_uri}
                        </div>
                        <div>
                          {provider.provider === "google"
                            ? "Google app type: Web application"
                            : "Microsoft account types: if Tenant ID is common, the app must allow personal Microsoft accounts and work/school accounts"}
                        </div>
                      </div>
                    )}
                  </div>
                  <label className="flex items-center gap-2 text-xs font-medium text-contrast-helper">
                    <input
                      type="checkbox"
                      checked={form.enabled}
                      onChange={(event) =>
                        updateForm(provider.provider, {
                          enabled: event.target.checked,
                        })
                      }
                      className="h-4 w-4 rounded border-control-border text-action-text focus:ring-action"
                    />
                    Enabled
                  </label>
                </div>

                <SettingsRow
                  label={
                    provider.provider === "microsoft"
                      ? "Application (client) ID"
                      : "OAuth Client ID"
                  }
                >
                  <input
                    type="text"
                    value={form.client_id}
                    onChange={(event) =>
                      updateForm(provider.provider, {
                        client_id: event.target.value,
                      })
                    }
                    className="w-full bg-surface-card border border-control-border rounded px-3 py-2 focus:outline-none focus:border-action text-foreground"
                    placeholder={provider.provider === "microsoft"
                      ? "Paste the Application (client) ID"
                      : "Paste the OAuth client ID"}
                  />
                </SettingsRow>

                {provider.provider === "microsoft" && (
                  <SettingsRow
                    label="Tenant ID or common"
                    description="Use common for both Outlook.com and Microsoft 365 accounts. Use a specific tenant ID only for a single-tenant app or to restrict sign-in to one directory."
                  >
                    <input
                      type="text"
                      value={form.tenant_id}
                      onChange={(event) =>
                        updateForm(provider.provider, {
                          tenant_id: event.target.value,
                        })
                      }
                      className="w-full bg-surface-card border border-control-border rounded px-3 py-2 focus:outline-none focus:border-action text-foreground"
                      placeholder="common"
                    />
                  </SettingsRow>
                )}

                <SettingsRow
                  label={
                    provider.provider === "microsoft"
                      ? "Client Secret Value"
                      : "OAuth Client Secret"
                  }
                >
                  <input
                    type="password"
                    value={form.client_secret}
                    onChange={(event) =>
                      updateForm(provider.provider, {
                        client_secret: event.target.value,
                        clear_client_secret: false,
                      })
                    }
                    className="w-full bg-surface-card border border-control-border rounded px-3 py-2 focus:outline-none focus:border-action text-foreground"
                    placeholder={provider.provider === "microsoft"
                      ? provider.has_client_secret
                        ? "Stored. Enter a new value to replace it."
                        : "Paste the client secret value"
                      : provider.has_client_secret
                        ? "Stored. Enter a new value to replace it."
                        : "Paste the provider secret"}
                  />
                </SettingsRow>

                <label className="flex items-center gap-2 text-sm contrast-helper">
                  <input
                    type="checkbox"
                    checked={form.clear_client_secret}
                    onChange={(event) =>
                      updateForm(provider.provider, {
                        clear_client_secret: event.target.checked,
                        client_secret: event.target.checked ? "" : form.client_secret,
                      })
                    }
                    className="h-4 w-4 rounded border-control-border text-action-text focus:ring-action"
                  />
                  Clear saved secret on next save
                </label>

                <div className="settings-inset space-y-2 rounded-xl p-4 text-xs contrast-helper">
                  <label className="flex items-center gap-2 text-sm contrast-helper">
                    <input
                      type="checkbox"
                      checked={form.push_enabled}
                      onChange={(event) =>
                        updateForm(provider.provider, {
                          push_enabled: event.target.checked,
                        })
                      }
                      className="h-4 w-4 rounded border-control-border text-action-text focus:ring-action"
                    />
                    Enable live sync (push notifications)
                  </label>
                  <div>
                    Requires this Nojoin instance to be reachable over public
                    HTTPS. When live sync cannot be established, connected
                    calendars still refresh every 15 minutes.
                  </div>
                  {provider.push_notification_url && (
                    <>
                      <div>
                        {provider.provider === "google"
                          ? "Verify this notification URL's domain in Google Cloud, then Nojoin registers the watch channels:"
                          : "Nojoin registers this notification URL automatically:"}
                      </div>
                      <div className="break-all rounded bg-surface-inset px-2 py-1 font-mono text-[11px] text-contrast-muted">
                        {provider.push_notification_url}
                      </div>
                    </>
                  )}
                </div>

                <div className="flex justify-end">
                  <button
                    type="button"
                    onClick={() => handleSave(provider.provider)}
                    disabled={isSaving}
                    className="inline-flex items-center gap-2 rounded-xl bg-action px-4 py-2 text-sm font-medium text-foreground transition-colors hover:bg-action disabled:opacity-50"
                  >
                    {isSaving ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                      <Save className="w-4 h-4" />
                    )}
                    Save provider
                  </button>
                </div>
              </div>
            );
          })}
        </SettingsBlock>
      )}
    </SettingsCard>
  );
}
