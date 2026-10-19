"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
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
import { Switch } from "@/components/ui/Switch";
import SettingsBlock from "./SettingsBlock";
import SettingsCard from "./SettingsCard";
import { useSettingsDraft } from "./SettingsProvider";
import SettingsRow from "./SettingsRow";
import SettingsStatusBadge from "./SettingsStatusBadge";
import {
  SETTINGS_BUTTON_PRIMARY,
  SETTINGS_CONTROL_COLUMN_WIDE,
  SETTINGS_CONTROL_END,
  SETTINGS_INPUT_CLASS,
  SETTINGS_VALUE_CLASS,
} from "./settingsControls";


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

  const savedForms = useMemo(() => buildInitialForms(providers), [providers]);
  const isDirty = (provider: CalendarProvider) =>
    JSON.stringify(forms[provider]) !== JSON.stringify(savedForms[provider]);
  useSettingsDraft(
    "calendar-providers",
    !loading && providers.some((provider) => isDirty(provider.provider)),
  );

  if (loading) {
    return (
      <SettingsCard
        id="integrations-calendar-providers"
        title="Calendar sign-in"
        description="The app this installation registers with Google and Microsoft."
      >
        <SettingsBlock>
          <p className="flex items-center gap-2 text-sm contrast-helper">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            Loading provider configuration...
          </p>
        </SettingsBlock>
      </SettingsCard>
    );
  }

  return (
    <div id="integrations-calendar-providers" className="scroll-mt-24 space-y-4">
      {providers.map((provider) => {
        const form = forms[provider.provider];
        const isSaving = savingProvider === provider.provider;
        const isMicrosoft = provider.provider === "microsoft";
        const key = provider.provider;
        const dirty = isDirty(key);

        return (
          <SettingsCard
            key={key}
            id={`integrations-calendar-${key}`}
            scopeId="integrations-calendar-providers"
            title={`${provider.display_name} calendar sign-in`}
            description={`The app this installation registers with ${provider.display_name}. Once it is saved, everyone can connect their own calendar.`}
            badge={
              <SettingsStatusBadge tone={provider.configured ? "success" : "warning"}>
                {provider.configured ? "Ready" : "Needs credentials"}
              </SettingsStatusBadge>
            }
          >
            <SettingsRow
              label="Enabled"
              labelId={`${key}-enabled-label`}
              descriptionId={`${key}-enabled-help`}
              description={
                provider.configured
                  ? `Credentials come from ${provider.source}.`
                  : "Add the app's credentials below, then save."
              }
              controlClassName={SETTINGS_CONTROL_END}
            >
              <Switch
                checked={form.enabled}
                aria-labelledby={`${key}-enabled-label`}
                aria-describedby={`${key}-enabled-help`}
                onCheckedChange={(checked) => updateForm(key, { enabled: checked })}
              />
            </SettingsRow>

            {provider.redirect_uri && (
              <SettingsRow
                label="Redirect URI"
                description={
                  isMicrosoft
                    ? "Register it on the app. With the tenant set to common, the app must allow personal and work or school accounts."
                    : "Register it on a Web application OAuth client."
                }
                controlClassName={SETTINGS_CONTROL_COLUMN_WIDE}
              >
                <p className={`${SETTINGS_VALUE_CLASS} font-mono text-xs break-all`}>
                  {provider.redirect_uri}
                </p>
              </SettingsRow>
            )}

            <SettingsRow
              label={isMicrosoft ? "Application (client) ID" : "Client ID"}
              controlId={`${key}-client-id`}
              controlClassName={SETTINGS_CONTROL_COLUMN_WIDE}
            >
              <input
                id={`${key}-client-id`}
                type="text"
                value={form.client_id}
                onChange={(event) => updateForm(key, { client_id: event.target.value })}
                className={SETTINGS_INPUT_CLASS}
                autoComplete="off"
              />
            </SettingsRow>

            {isMicrosoft && (
              <SettingsRow
                label="Tenant"
                controlId={`${key}-tenant`}
                description="Use common for both Outlook.com and Microsoft 365 accounts, or a tenant ID to restrict sign-in to one directory."
                controlClassName={SETTINGS_CONTROL_COLUMN_WIDE}
              >
                <input
                  id={`${key}-tenant`}
                  aria-describedby={`${key}-tenant-help`}
                  type="text"
                  value={form.tenant_id}
                  onChange={(event) => updateForm(key, { tenant_id: event.target.value })}
                  className={SETTINGS_INPUT_CLASS}
                  placeholder="common"
                  autoComplete="off"
                />
              </SettingsRow>
            )}

            <SettingsRow
              label={isMicrosoft ? "Client secret value" : "Client secret"}
              controlId={`${key}-client-secret`}
              description={
                provider.has_client_secret
                  ? "A secret is saved. Enter a new one to replace it."
                  : "Paste the secret from the provider's console."
              }
              controlClassName={SETTINGS_CONTROL_COLUMN_WIDE}
            >
              <div className="space-y-2">
                <input
                  id={`${key}-client-secret`}
                  aria-describedby={`${key}-client-secret-help`}
                  type="password"
                  value={form.client_secret}
                  onChange={(event) =>
                    updateForm(key, {
                      client_secret: event.target.value,
                      clear_client_secret: false,
                    })
                  }
                  className={SETTINGS_INPUT_CLASS}
                  autoComplete="new-password"
                />
                {provider.has_client_secret && (
                  <label className="flex items-center gap-2 text-sm text-foreground">
                    <input
                      type="checkbox"
                      checked={form.clear_client_secret}
                      onChange={(event) =>
                        updateForm(key, {
                          clear_client_secret: event.target.checked,
                          client_secret: event.target.checked ? "" : form.client_secret,
                        })
                      }
                      className="h-4 w-4 rounded accent-action focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
                    />
                    Remove the saved secret
                  </label>
                )}
              </div>
            </SettingsRow>

            <SettingsRow
              label="Live sync"
              labelId={`${key}-push-label`}
              descriptionId={`${key}-push-help`}
              description={
                <>
                  <span className="block">
                    Pushes calendar changes as they happen. Needs this
                    installation on public HTTPS; without it, connected
                    calendars refresh every 15 minutes.
                  </span>
                  {provider.push_notification_url && (
                    <span className="mt-1 block">
                      {isMicrosoft
                        ? "Nojoin registers this address automatically: "
                        : "Verify this address's domain in Google Cloud first: "}
                      <span className="font-mono break-all">
                        {provider.push_notification_url}
                      </span>
                    </span>
                  )}
                </>
              }
              controlClassName={SETTINGS_CONTROL_END}
            >
              <Switch
                checked={form.push_enabled}
                aria-labelledby={`${key}-push-label`}
                aria-describedby={`${key}-push-help`}
                onCheckedChange={(checked) => updateForm(key, { push_enabled: checked })}
              />
            </SettingsRow>

            <SettingsBlock className="flex flex-wrap items-center justify-end gap-3">
              {dirty && (
                <span className="text-sm text-status-warning-fg">Unsaved changes</span>
              )}
              <button
                type="button"
                onClick={() => handleSave(key)}
                disabled={isSaving || !dirty}
                className={SETTINGS_BUTTON_PRIMARY}
              >
                {isSaving ? (
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                ) : (
                  <Save className="h-4 w-4" aria-hidden="true" />
                )}
                Save {provider.display_name}
              </button>
            </SettingsBlock>
          </SettingsCard>
        );
      })}
    </div>
  );
}
