"use client";

import React, {
  useState,
  useEffect,
  useMemo,
  useRef,
  useCallback,
} from "react";
import { useSearchParams } from "next/navigation";
import { getSettings, updateSettings, getUserMe } from "@/lib/api";
import { Settings, CompanionDevices, UserRole } from "@/types";
import { isValidUrl } from "@/lib/validation";
import { Loader2, Search } from "lucide-react";
import {
  CompanionLocalRequestError,
  type CompanionLocalAction,
  companionLocalFetch,
} from "@/lib/companionLocalApi";
import { useServiceStatusStore } from "@/lib/serviceStatusStore";
import { TAB_KEYWORDS } from "./keywords";
import VersionTag from "./VersionTag";
import AISettings from "./AISettings";
import CompanionAppSettings from "./CompanionAppSettings";
import AdminSettings from "./AdminSettings";
import HelpSettings from "./HelpSettings";
import PersonalSettings from "./PersonalSettings";
import UpdatesSettings from "./UpdatesSettings";
import { isValidTimeZone, setCachedUserTimeZone } from "@/lib/timezone";
import SettingsAutosaveState, {
  type SettingsAutosaveSnapshot,
} from "./SettingsAutosaveState";
import SettingsLayout from "./SettingsLayout";
import SettingsNav from "./SettingsNav";
import {
  getVisibleSettingsSections,
  resolveLegacySettingsSectionId,
  type SettingsSectionId,
} from "./settingsMetadata";
import {
  getPreferredSettingsSectionForSearch,
  getSettingsSectionMatchScores,
  mergeAutosaveStates,
} from "./settingsState";
import useDebouncedAutosave from "./useDebouncedAutosave";

interface CompanionConfig {
  api_port: number;
  local_port: number;
  min_meeting_length?: number;
}

interface MainAutosaveValue {
  settings: Settings;
  companionConfig: CompanionConfig | null;
  selectedInputDevice: string | null;
  selectedOutputDevice: string | null;
}

const COMPANION_CONFIG_READ_ACTIONS: CompanionLocalAction[] = [
  "settings:read",
  "devices:read",
];

export default function SettingsPage() {
  const searchParams = useSearchParams();
  const companionAuthenticated = useServiceStatusStore(
    (state) => state.companionAuthenticated,
  );
  const handleCompanionPairingEnded = useServiceStatusStore(
    (state) => state.handleCompanionPairingEnded,
  );
  const [activeTab, setActiveTab] = useState<SettingsSectionId>("personal");
  const [settings, setSettings] = useState<Settings>({});
  const [companionConfig, setCompanionConfig] =
    useState<CompanionConfig | null>(null);
  const [companionDevices, setCompanionDevices] =
    useState<CompanionDevices | null>(null);
  const [selectedInputDevice, setSelectedInputDevice] = useState<string | null>(
    null,
  );
  const [selectedOutputDevice, setSelectedOutputDevice] = useState<
    string | null
  >(null);
  const [loading, setLoading] = useState(true);
  const [mounted, setMounted] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [isAdmin, setIsAdmin] = useState(false);
  const [userId, setUserId] = useState<number | null>(null);
  const [currentUsername, setCurrentUsername] = useState<string | null>(null);
  const [forcePasswordChange, setForcePasswordChange] = useState(false);
  const [accountAutosaveState, setAccountAutosaveState] =
    useState<SettingsAutosaveSnapshot | null>(null);

  const refreshCompanionConfigRequestRef = useRef<Promise<boolean> | null>(
    null,
  );
  const pendingCompanionHydrationRef = useRef(false);

  const validateSettings = (
    settings: Settings,
    companionConfig: CompanionConfig | null,
  ): string | null => {
    if (
      settings.whisper_model_size &&
      !["tiny", "base", "small", "medium", "large", "turbo"].includes(
        settings.whisper_model_size,
      )
    ) {
      return "Invalid Whisper model size.";
    }
    if (settings.theme && !["dark", "light"].includes(settings.theme)) {
      return "Invalid theme.";
    }
    if (
      settings.llm_provider &&
      !["gemini", "openai", "anthropic", "ollama"].includes(
        settings.llm_provider,
      )
    ) {
      return "Invalid LLM provider.";
    }
    if (settings.ollama_api_url && !isValidUrl(settings.ollama_api_url)) {
      return "Invalid Ollama API URL.";
    }
    if (settings.timezone && !isValidTimeZone(settings.timezone)) {
      return "Invalid timezone. Use a valid IANA timezone such as Europe/London.";
    }
    if (companionConfig?.min_meeting_length !== undefined) {
      if (
        companionConfig.min_meeting_length < 0 ||
        companionConfig.min_meeting_length > 1440
      ) {
        return "Meeting length must be between 0 and 1440 minutes.";
      }
    }
    return null;
  };

  const mainAutosaveValue = useMemo<MainAutosaveValue>(
    () => ({
      settings,
      companionConfig,
      selectedInputDevice,
      selectedOutputDevice,
    }),
    [settings, companionConfig, selectedInputDevice, selectedOutputDevice],
  );

  const {
    autosaveState: mainAutosaveState,
    markAsSaved: markMainAutosaveSaved,
    saveNow: saveMainAutosaveNow,
  } = useDebouncedAutosave<MainAutosaveValue>({
    value: mainAutosaveValue,
    enabled: !loading && !forcePasswordChange,
    serialize: (value) =>
      JSON.stringify({
        settings: value.settings,
        companionApiPort: value.companionConfig?.api_port,
        companionMinLength: value.companionConfig?.min_meeting_length,
        selectedInputDevice: value.selectedInputDevice,
        selectedOutputDevice: value.selectedOutputDevice,
      }),
    validate: (value) => validateSettings(value.settings, value.companionConfig),
    save: async (value) => {
      await updateSettings(value.settings);
      setCachedUserTimeZone(value.settings.timezone);

      if (value.companionConfig) {
        await companionLocalFetch(
          "/config",
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              api_port: value.companionConfig.api_port,
              min_meeting_length: value.companionConfig.min_meeting_length,
            }),
          },
          "settings:write",
        );
      }

      if (companionDevices) {
        await companionLocalFetch(
          "/config",
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              input_device_name: value.selectedInputDevice,
              output_device_name: value.selectedOutputDevice,
            }),
          },
          "settings:write",
        );
      }
    },
    pendingMessage: "Changes pending...",
    savingMessage: "Saving changes...",
    savedMessage: "All changes saved",
    fallbackErrorMessage: "Failed to save settings",
  });

  const refreshCompanionConfig = useCallback(async () => {
    const companionAuthenticated =
      useServiceStatusStore.getState().companionAuthenticated;

    if (!companionAuthenticated) {
      setCompanionConfig(null);
      setCompanionDevices(null);
      setSelectedInputDevice(null);
      setSelectedOutputDevice(null);
      return false;
    }

    if (refreshCompanionConfigRequestRef.current) {
      return refreshCompanionConfigRequestRef.current;
    }

    const request = (async () => {
      try {
        const res = await companionLocalFetch(
          "/config",
          { method: "GET" },
          COMPANION_CONFIG_READ_ACTIONS,
        );
        if (!res.ok) {
          if (res.status === 403 || res.status === 409) {
            handleCompanionPairingEnded();
          }
          setCompanionConfig(null);
          setCompanionDevices(null);
          setSelectedInputDevice(null);
          setSelectedOutputDevice(null);
          return false;
        }

        const companionData: CompanionConfig = await res.json();
        setCompanionConfig(companionData);

        const devicesRes = await companionLocalFetch(
          "/devices",
          { method: "GET" },
          COMPANION_CONFIG_READ_ACTIONS,
        );
        if (!devicesRes.ok) {
          if (devicesRes.status === 403 || devicesRes.status === 409) {
            handleCompanionPairingEnded();
          }
          setCompanionDevices(null);
          setSelectedInputDevice(null);
          setSelectedOutputDevice(null);
          return false;
        }

        const devicesData: CompanionDevices = await devicesRes.json();
        setCompanionDevices(devicesData);
        setSelectedInputDevice(devicesData.selected_input);
        setSelectedOutputDevice(devicesData.selected_output);
        pendingCompanionHydrationRef.current = true;
        return true;
      } catch (e) {
        console.error("Failed to refresh companion config", e);
        if (
          e instanceof CompanionLocalRequestError &&
          (e.status === 403 || e.status === 409)
        ) {
          handleCompanionPairingEnded();
        }
        setCompanionConfig(null);
        setCompanionDevices(null);
        setSelectedInputDevice(null);
        setSelectedOutputDevice(null);
        return false;
      }
    })();

    refreshCompanionConfigRequestRef.current = request.finally(() => {
      refreshCompanionConfigRequestRef.current = null;
    });

    return refreshCompanionConfigRequestRef.current;
  }, [handleCompanionPairingEnded]);

  useEffect(() => {
    let isCancelled = false;

    const syncCompanionConfigAfterAuthChange = async () => {
      if (!companionAuthenticated) {
        setCompanionConfig(null);
        setCompanionDevices(null);
        setSelectedInputDevice(null);
        setSelectedOutputDevice(null);
        return;
      }

      for (let attempt = 0; attempt < 8; attempt += 1) {
        const refreshed = await refreshCompanionConfig();
        if (isCancelled || refreshed) {
          return;
        }

        await new Promise<void>((resolve) => {
          window.setTimeout(resolve, 400);
        });
      }
    };

    void syncCompanionConfigAfterAuthChange();

    return () => {
      isCancelled = true;
    };
  }, [companionAuthenticated, refreshCompanionConfig]);

  useEffect(() => {
    if (!loading && pendingCompanionHydrationRef.current) {
      markMainAutosaveSaved(mainAutosaveValue);
      pendingCompanionHydrationRef.current = false;
    }
  }, [loading, mainAutosaveValue, markMainAutosaveSaved]);

  // Determine which tabs have matches and their scores
  const tabMatches = useMemo(() => {
    return getSettingsSectionMatchScores({ searchQuery, isAdmin });
  }, [searchQuery, isAdmin]);

  // Auto-switch tab if current one has no matches but others do
  useEffect(() => {
    const preferredTab = getPreferredSettingsSectionForSearch({
      activeSectionId: activeTab,
      matchScores: tabMatches,
    });

    if (preferredTab) {
      setActiveTab(preferredTab);
    }
  }, [tabMatches, activeTab]);

  useEffect(() => {
    const requestedTab = searchParams.get("tab");
    const resolvedTab = resolveLegacySettingsSectionId(requestedTab);

    if (resolvedTab) {
      setActiveTab(resolvedTab);
    }
  }, [searchParams]);

  useEffect(() => {
    setMounted(true);
    return () => setMounted(false);
  }, []);

  useEffect(() => {
    const load = async () => {
      let currentSettings = {};
      const loadedAutosaveValue: MainAutosaveValue = {
        settings: {},
        companionConfig: null,
        selectedInputDevice: null,
        selectedOutputDevice: null,
      };

      try {
        const userData = await getUserMe();
        setCurrentUsername(userData.username);
        setIsAdmin(
          userData.is_superuser ||
            userData.role === UserRole.OWNER ||
            userData.role === UserRole.ADMIN,
        );
        setUserId(userData.id);

        if (userData.force_password_change) {
          setForcePasswordChange(true);
          setActiveTab("personal");
          markMainAutosaveSaved(loadedAutosaveValue);
          setLoading(false);
          return;
        }

        const settingsData = await getSettings();

        // Ensure settingsData is an object (API might return null)
        const safeSettings = settingsData || {};
        setSettings(safeSettings);
        setCachedUserTimeZone(safeSettings.timezone);
        currentSettings = safeSettings;
        setForcePasswordChange(false);
        markMainAutosaveSaved({
          ...loadedAutosaveValue,
          settings: safeSettings,
        });
      } catch (e) {
        console.error("Failed to load settings", e);
      }

      if (Object.keys(currentSettings).length === 0) {
        markMainAutosaveSaved(loadedAutosaveValue);
      }

      setLoading(false);
    };
    load();
  }, [markMainAutosaveSaved]);

  const persistSettingsNow = useCallback(
    async (nextSettings: Settings) => {
      if (forcePasswordChange) {
        return;
      }

      await saveMainAutosaveNow({
        settings: nextSettings,
        companionConfig,
        selectedInputDevice,
        selectedOutputDevice,
      });
    },
    [
      companionConfig,
      forcePasswordChange,
      saveMainAutosaveNow,
      selectedInputDevice,
      selectedOutputDevice,
    ],
  );

  const tabs = useMemo(
    () => getVisibleSettingsSections({ isAdmin, forcePasswordChange }),
    [forcePasswordChange, isAdmin],
  );

  useEffect(() => {
    if (!tabs.some((tab) => tab.id === activeTab)) {
      setActiveTab(tabs[0].id);
    }
  }, [activeTab, tabs]);

  if (!mounted) return null;

  const footerAutosaveState: SettingsAutosaveSnapshot =
    activeTab === "personal"
      ? mergeAutosaveStates(mainAutosaveState, accountAutosaveState)
      : forcePasswordChange
        ? { status: "blocked" }
        : mainAutosaveState;

  return (
    <SettingsLayout
      title="Settings"
      description={
        forcePasswordChange
          ? "Password change required before other settings become available."
          : "Manage your application preferences and configurations."
      }
      headerAccessory={<VersionTag />}
      sidebarHeader={
        forcePasswordChange ? null : (
          <div className="relative">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 contrast-icon-muted" />
            <input
              type="text"
              placeholder="Search settings..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full rounded-lg border border-gray-300 bg-white py-2 pl-9 pr-4 text-sm text-gray-900 placeholder:text-gray-500 focus:border-transparent focus:ring-2 focus:ring-orange-500 dark:border-gray-700 dark:bg-gray-950 dark:text-gray-100 dark:placeholder:text-gray-400"
            />
          </div>
        )
      }
      navigation={
        <SettingsNav
          items={tabs}
          activeItemId={activeTab}
          onSelect={setActiveTab}
          matchScores={tabMatches || undefined}
        />
      }
      sidebarFooter={
        <SettingsAutosaveState
          status={footerAutosaveState.status}
          message={footerAutosaveState.message}
        />
      }
    >
      {loading ? (
        <div className="flex h-full items-center justify-center text-gray-600 dark:text-gray-300">
          <Loader2 className="mr-2 h-6 w-6 animate-spin" />
          Loading settings...
        </div>
      ) : (
        <div className="mx-auto max-w-4xl">
          {forcePasswordChange && (
            <div className="mb-6 rounded-lg border border-orange-300 bg-orange-50 px-4 py-3 text-sm text-orange-900 dark:border-orange-500/40 dark:bg-orange-900/20 dark:text-orange-100">
              Your account must change its password before Nojoin will allow access to other authenticated features.
            </div>
          )}
          {activeTab === "personal" && (
            <PersonalSettings
              settings={settings}
              onUpdate={setSettings}
              searchQuery={searchQuery}
              forcePasswordChange={forcePasswordChange}
              initialUsername={currentUsername}
              onUsernameSaved={setCurrentUsername}
              onAutosaveStateChange={setAccountAutosaveState}
            />
          )}
          {activeTab === "ai" && (
            <AISettings
              settings={settings}
              onUpdate={setSettings}
              onPersist={persistSettingsNow}
              isAdmin={isAdmin}
              searchQuery={searchQuery}
            />
          )}
          {activeTab === "companion" && (
            <CompanionAppSettings
              companionConfig={companionConfig}
              onUpdateCompanionConfig={(config) =>
                setCompanionConfig((prev) =>
                  prev ? { ...prev, ...config } : null,
                )
              }
              onRefreshCompanionConfig={refreshCompanionConfig}
              companionDevices={companionDevices}
              selectedInputDevice={selectedInputDevice}
              onSelectInputDevice={setSelectedInputDevice}
              selectedOutputDevice={selectedOutputDevice}
              onSelectOutputDevice={setSelectedOutputDevice}
              searchQuery={searchQuery}
            />
          )}
          {activeTab === "updates" && (
            <UpdatesSettings searchQuery={searchQuery} />
          )}
          {activeTab === "help" && (
            <HelpSettings userId={userId} searchQuery={searchQuery} />
          )}
          {activeTab === "administration" && isAdmin && (
            <AdminSettings
              isAdmin={isAdmin}
              searchQuery={searchQuery}
            />
          )}
        </div>
      )}
    </SettingsLayout>
  );
}
