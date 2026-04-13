"use client";

import { useEffect, useState } from "react";
import {
  CalendarProvider,
  CalendarProviderConfigUpdate,
  CalendarProviderStatus,
} from "@/types";
import {
  getCalendarProviderStatuses,
  updateCalendarProviderConfiguration,
} from "@/lib/api";
import { useNotificationStore } from "@/lib/notificationStore";
import { CalendarRange, Loader2, Save } from "lucide-react";


interface ProviderFormState {
  client_id: string;
  client_secret: string;
  tenant_id: string;
  enabled: boolean;
  clear_client_secret: boolean;
}


const EMPTY_FORM: ProviderFormState = {
  client_id: "",
  client_secret: "",
  tenant_id: "common",
  enabled: true,
  clear_client_secret: false,
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

  const loadProviders = async () => {
    setLoading(true);
    try {
      const providerStatuses = await getCalendarProviderStatuses();
      setProviders(providerStatuses);
      setForms(buildInitialForms(providerStatuses));
    } catch (error: any) {
      addNotification({
        type: "error",
        message:
          error.response?.data?.detail ||
          "Failed to load calendar provider configuration",
      });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadProviders();
  }, []);

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
        },
      }));
      addNotification({
        type: "success",
        message: `${updatedProvider.display_name} provider settings saved`,
      });
    } catch (error: any) {
      addNotification({
        type: "error",
        message:
          error.response?.data?.detail ||
          `Failed to save ${provider} provider settings`,
      });
    } finally {
      setSavingProvider(null);
    }
  };

  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900/60 p-4">
        <div className="flex items-start gap-3">
          <div className="rounded-xl bg-orange-100 p-2 text-orange-700 dark:bg-orange-500/10 dark:text-orange-300">
            <CalendarRange className="w-5 h-5" />
          </div>
          <div>
            <h3 className="text-lg font-medium text-gray-900 dark:text-white">
              Calendar Provider OAuth Configuration
            </h3>
            <p className="mt-1 text-sm text-gray-600 dark:text-gray-300">
              Store the one-time installation OAuth app credentials for Google and Microsoft.
              End users do not paste these values. They only click Connect Gmail Calendar
              or Connect Outlook Calendar and then approve access in the provider&apos;s own
              sign-in and consent screen.
            </p>
          </div>
        </div>
      </div>

      {loading ? (
        <div className="flex items-center gap-2 text-sm text-gray-600 dark:text-gray-300">
          <Loader2 className="w-4 h-4 animate-spin" />
          Loading provider configuration...
        </div>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          {providers.map((provider) => {
            const form = forms[provider.provider];
            const isSaving = savingProvider === provider.provider;
            return (
              <div
                key={provider.provider}
                className="rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900/60 p-4 space-y-4"
              >
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="text-base font-semibold text-gray-900 dark:text-white">
                      {provider.display_name}
                    </div>
                    <div className="mt-1 text-xs contrast-helper">
                      {provider.configured
                        ? `Configured via ${provider.source}`
                        : "Missing OAuth credentials"}
                    </div>
                    {provider.redirect_uri && (
                      <div className="mt-2 space-y-1 text-xs contrast-helper">
                        <div>
                          Register redirect URI:
                        </div>
                        <div className="break-all rounded bg-gray-100 px-2 py-1 font-mono text-[11px] text-gray-700 dark:bg-gray-800 dark:text-gray-300">
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
                  <label className="flex items-center gap-2 text-xs font-medium text-gray-600 dark:text-gray-300">
                    <input
                      type="checkbox"
                      checked={form.enabled}
                      onChange={(event) =>
                        updateForm(provider.provider, {
                          enabled: event.target.checked,
                        })
                      }
                      className="h-4 w-4 rounded border-gray-300 text-orange-600 focus:ring-orange-500"
                    />
                    Enabled
                  </label>
                </div>

                <div>
                  <label className="block text-sm font-medium contrast-muted mb-1">
                    {provider.provider === "microsoft"
                      ? "Application (client) ID"
                      : "OAuth Client ID"}
                  </label>
                  <input
                    type="text"
                    value={form.client_id}
                    onChange={(event) =>
                      updateForm(provider.provider, {
                        client_id: event.target.value,
                      })
                    }
                    className="w-full bg-white dark:bg-gray-900 border border-gray-400 dark:border-gray-700 rounded px-3 py-2 focus:outline-none focus:border-orange-500 text-gray-900 dark:text-white"
                    placeholder={provider.provider === "microsoft"
                      ? "Paste the Application (client) ID"
                      : "Paste the OAuth client ID"}
                  />
                </div>

                {provider.provider === "microsoft" && (
                  <div>
                    <label className="block text-sm font-medium contrast-muted mb-1">
                      Tenant ID or common
                    </label>
                    <input
                      type="text"
                      value={form.tenant_id}
                      onChange={(event) =>
                        updateForm(provider.provider, {
                          tenant_id: event.target.value,
                        })
                      }
                      className="w-full bg-white dark:bg-gray-900 border border-gray-400 dark:border-gray-700 rounded px-3 py-2 focus:outline-none focus:border-orange-500 text-gray-900 dark:text-white"
                      placeholder="common"
                    />
                    <p className="mt-1 text-xs contrast-helper">
                      Use common for both Outlook.com and Microsoft 365 accounts. Use a specific tenant ID only for a single-tenant app or to restrict sign-in to one directory.
                    </p>
                  </div>
                )}

                <div>
                  <label className="block text-sm font-medium contrast-muted mb-1">
                    {provider.provider === "microsoft"
                      ? "Client Secret Value"
                      : "OAuth Client Secret"}
                  </label>
                  <input
                    type="password"
                    value={form.client_secret}
                    onChange={(event) =>
                      updateForm(provider.provider, {
                        client_secret: event.target.value,
                        clear_client_secret: false,
                      })
                    }
                    className="w-full bg-white dark:bg-gray-900 border border-gray-400 dark:border-gray-700 rounded px-3 py-2 focus:outline-none focus:border-orange-500 text-gray-900 dark:text-white"
                    placeholder={provider.provider === "microsoft"
                      ? provider.has_client_secret
                        ? "Stored. Enter a new value to replace it."
                        : "Paste the client secret value"
                      : provider.has_client_secret
                        ? "Stored. Enter a new value to replace it."
                        : "Paste the provider secret"}
                  />
                </div>

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
                    className="h-4 w-4 rounded border-gray-300 text-orange-600 focus:ring-orange-500"
                  />
                  Clear saved secret on next save
                </label>

                <button
                  type="button"
                  onClick={() => handleSave(provider.provider)}
                  disabled={isSaving}
                  className="inline-flex items-center gap-2 rounded-md bg-orange-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-orange-700 disabled:opacity-50"
                >
                  {isSaving ? (
                    <Loader2 className="w-4 h-4 animate-spin" />
                  ) : (
                    <Save className="w-4 h-4" />
                  )}
                  Save provider
                </button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}