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
import {
  Save,
  Loader2,
  Settings as SettingsIcon,
  Link2,
  ArrowUpCircle,
  Search,
  User,
  Shield,
  PlayCircle,
} from "lucide-react";
import {
  CompanionLocalRequestError,
  type CompanionLocalAction,
  companionLocalFetch,
} from "@/lib/companionLocalApi";
import { useServiceStatusStore } from "@/lib/serviceStatusStore";
import { getMatchScore } from "@/lib/searchUtils";
import { TAB_KEYWORDS } from "./keywords";
import VersionTag from "./VersionTag";
import GeneralSettings from "./GeneralSettings";
import CompanionAppSettings from "./CompanionAppSettings";
import AccountSettings from "./AccountSettings";
import AdminSettings from "./AdminSettings";
import HelpSettings from "./HelpSettings";
import UpdatesSettings from "./UpdatesSettings";
import { useNotificationStore } from "@/lib/notificationStore";
import { isValidTimeZone, setCachedUserTimeZone } from "@/lib/timezone";

type Tab =
  | "general"
  | "companion"
  | "updates"
  | "help"
  | "account"
  | "admin";

interface CompanionConfig {
  api_port: number;
  local_port: number;
  min_meeting_length?: number;
}

const COMPANION_CONFIG_READ_ACTIONS: CompanionLocalAction[] = [
  "settings:read",
  "devices:read",
];

export default function SettingsPage() {
  const searchParams = useSearchParams();
  const handleCompanionPairingEnded = useServiceStatusStore(
    (state) => state.handleCompanionPairingEnded,
  );
  const [activeTab, setActiveTab] = useState<Tab>("general");
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
  const [saving, setSaving] = useState(false);
  const [mounted, setMounted] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [isAdmin, setIsAdmin] = useState(false);
  const [userId, setUserId] = useState<number | null>(null);
  const [forcePasswordChange, setForcePasswordChange] = useState(false);
  const { addNotification } = useNotificationStore();

  const saveTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const refreshCompanionConfigRequestRef = useRef<Promise<boolean> | null>(
    null,
  );
  const isFirstLoad = useRef(true);
  const lastSavedState = useRef<string>("");

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

  // Determine which tabs have matches and their scores
  const tabMatches = useMemo(() => {
    if (!searchQuery) return null;

    const matches: Record<Tab, number> = {
      general: getMatchScore(searchQuery, TAB_KEYWORDS.general),
      companion: getMatchScore(searchQuery, TAB_KEYWORDS.companion),
      updates: getMatchScore(searchQuery, TAB_KEYWORDS.updates),
      help: getMatchScore(searchQuery, ["help", "tour", "demo", "tutorial"]),
      account: getMatchScore(searchQuery, TAB_KEYWORDS.account),
      admin: isAdmin
        ? getMatchScore(searchQuery, [
            ...TAB_KEYWORDS.admin,
            ...TAB_KEYWORDS.ai,
            ...TAB_KEYWORDS.invites,
            ...TAB_KEYWORDS.system,
            "backup",
            "restore",
          ])
        : 1, // Admin now covers AI, Invites, System, Backup
    };

    return matches;
  }, [searchQuery, isAdmin]);

  // Auto-switch tab if current one has no matches but others do
  useEffect(() => {
    if (!tabMatches) return;

    const currentScore = tabMatches[activeTab];

    // Find best matching tab
    let bestTab: Tab | null = null;
    let bestScore = 1.0;

    (Object.entries(tabMatches) as [Tab, number][]).forEach(([tab, score]) => {
      if (score < bestScore) {
        bestScore = score;
        bestTab = tab;
      }
    });

    if (bestTab && bestTab !== activeTab) {
      // Priority switch: If we found an exact match (score 0) and current is not exact
      if (bestScore === 0 && currentScore > 0) {
        setActiveTab(bestTab);
        return;
      }

      // Fallback switch: If current tab is a bad match, switch to best available
      if (currentScore >= 0.8 && bestScore < 0.6) {
        setActiveTab(bestTab);
      }
    }
  }, [tabMatches, activeTab]);

  useEffect(() => {
    const requestedTab = searchParams.get("tab");
    if (requestedTab === "audio") {
      setActiveTab("companion");
      return;
    }

    if (
      requestedTab === "general" ||
      requestedTab === "companion" ||
      requestedTab === "updates" ||
      requestedTab === "help" ||
      requestedTab === "account" ||
      requestedTab === "admin"
    ) {
      setActiveTab(requestedTab);
    }
  }, [searchParams]);

  useEffect(() => {
    setMounted(true);
    return () => setMounted(false);
  }, []);

  useEffect(() => {
    const load = async () => {
      let currentSettings = {};
      const currentInputDevice: string | null = null;
      const currentOutputDevice: string | null = null;

      try {
        const userData = await getUserMe();
        setIsAdmin(
          userData.is_superuser ||
            userData.role === UserRole.OWNER ||
            userData.role === UserRole.ADMIN,
        );
        setUserId(userData.id);

        if (userData.force_password_change) {
          setForcePasswordChange(true);
          setActiveTab("account");
          lastSavedState.current = JSON.stringify({
            settings: {},
            companionApiPort: undefined,
            companionMinLength: undefined,
            selectedInputDevice: null,
            selectedOutputDevice: null,
          });
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

      } catch (e) {
        console.error("Failed to load settings", e);
      }

      // Update last saved state to prevent auto-save on load
      lastSavedState.current = JSON.stringify({
        settings: currentSettings,
        companionApiPort: undefined,
        companionMinLength: undefined,
        selectedInputDevice: currentInputDevice,
        selectedOutputDevice: currentOutputDevice,
      });

      setLoading(false);
    };
    load();
  }, [activeTab]);

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
    if (companionConfig) {
      if (companionConfig.min_meeting_length !== undefined) {
        if (
          companionConfig.min_meeting_length < 0 ||
          companionConfig.min_meeting_length > 1440
        ) {
          return "Meeting length must be between 0 and 1440 minutes.";
        }
      }
    }
    return null;
  };

  const saveSettings = useCallback(async () => {
    if (forcePasswordChange) {
      return;
    }

    const error = validateSettings(settings, companionConfig);
    if (error) {
      addNotification({ type: "error", message: error });
      return;
    }
    setSaving(true);
    try {
      await updateSettings(settings);
      setCachedUserTimeZone(settings.timezone);

      // Save companion config (api_port) if available
      if (companionConfig) {
        await companionLocalFetch(
          "/config",
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              api_port: companionConfig.api_port,
              min_meeting_length: companionConfig.min_meeting_length,
            }),
          },
          "settings:write",
        );
      }

      // Save device selections if companion is connected
      if (companionDevices) {
        await companionLocalFetch(
          "/config",
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              input_device_name: selectedInputDevice,
              output_device_name: selectedOutputDevice,
            }),
          },
          "settings:write",
        );
      }

      // Update last saved state
      lastSavedState.current = JSON.stringify({
        settings,
        companionApiPort: companionConfig?.api_port,
        companionMinLength: companionConfig?.min_meeting_length,
        selectedInputDevice,
        selectedOutputDevice,
      });


      addNotification({
        type: "success",
        message: "Settings saved successfully",
      });
    } catch (e) {
      console.error("Failed to save settings", e);
      addNotification({ type: "error", message: "Failed to save settings" });
    } finally {
      setSaving(false);
    }
  }, [
    settings,
    companionConfig,
    companionDevices,
    selectedInputDevice,
    selectedOutputDevice,
    addNotification,
    forcePasswordChange,
  ]);

  const persistSettingsNow = useCallback(
    async (nextSettings: Settings) => {
      if (forcePasswordChange) {
        return;
      }

      const error = validateSettings(nextSettings, companionConfig);
      if (error) {
        addNotification({ type: "error", message: error });
        throw new Error(error);
      }

      if (saveTimeoutRef.current) {
        clearTimeout(saveTimeoutRef.current);
        saveTimeoutRef.current = null;
      }

      setSaving(true);
      try {
        await updateSettings(nextSettings);
        setCachedUserTimeZone(nextSettings.timezone);
        lastSavedState.current = JSON.stringify({
          settings: nextSettings,
          companionApiPort: companionConfig?.api_port,
          companionMinLength: companionConfig?.min_meeting_length,
          selectedInputDevice,
          selectedOutputDevice,
        });
      } catch (e) {
        console.error("Failed to save settings", e);
        addNotification({ type: "error", message: "Failed to save settings" });
        throw e;
      } finally {
        setSaving(false);
      }
    },
    [
      addNotification,
      companionConfig,
      forcePasswordChange,
      selectedInputDevice,
      selectedOutputDevice,
    ],
  );

  useEffect(() => {
    if (loading || forcePasswordChange) return;

    if (isFirstLoad.current) {
      isFirstLoad.current = false;
      return;
    }

    const currentState = JSON.stringify({
      settings,
      companionApiPort: companionConfig?.api_port,
      companionMinLength: companionConfig?.min_meeting_length,
      selectedInputDevice,
      selectedOutputDevice,
    });

    if (currentState === lastSavedState.current) {
      return;
    }

    if (saveTimeoutRef.current) {
      clearTimeout(saveTimeoutRef.current);
    }

    saveTimeoutRef.current = setTimeout(() => {
      saveSettings();
    }, 1000);

    return () => {
      if (saveTimeoutRef.current) {
        clearTimeout(saveTimeoutRef.current);
      }
    };
  }, [
    settings,
    companionConfig,
    selectedInputDevice,
    selectedOutputDevice,
    loading,
    forcePasswordChange,
    saveSettings,
  ]);

  const tabs = useMemo(() => {
    if (forcePasswordChange) {
      return [{ id: "account", label: "Account", icon: User }] as const;
    }

    const baseTabs = [
      { id: "general", label: "General", icon: SettingsIcon },
      { id: "companion", label: "Companion App", icon: Link2 },
      { id: "updates", label: "Updates", icon: ArrowUpCircle },
      { id: "help", label: "Help", icon: PlayCircle },
      { id: "account", label: "Account", icon: User },
    ] as const;

    const adminTab = { id: "admin", label: "Admin Panel", icon: Shield };

    if (isAdmin) {
      return [...baseTabs, adminTab];
    }
    return baseTabs;
  }, [forcePasswordChange, isAdmin]);

  if (!mounted) return null;

  return (
    <div className="flex flex-col h-full bg-gray-50 dark:bg-gray-900">
      {/* Header */}
      <div className="flex items-center justify-between px-4 pl-14 md:px-8 py-4 md:py-6 bg-white dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700 shrink-0">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white">
            Settings
          </h1>
          <p className="mt-1 text-sm contrast-helper">
            {forcePasswordChange
              ? "Password change required before other settings become available."
              : "Manage your application preferences and configurations."}
          </p>
        </div>
        <VersionTag />
      </div>

      <div className="flex flex-col md:flex-row flex-1 overflow-hidden">
        {/* Sidebar */}
        <div className="w-full md:w-64 bg-gray-100 dark:bg-gray-900/80 border-b md:border-b-0 md:border-r contrast-border flex flex-col shrink-0">
          {!forcePasswordChange && (
            <div className="p-4 border-b contrast-border">
              <div className="relative">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 contrast-icon-muted" />
                <input
                  type="text"
                  placeholder="Search settings..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="w-full pl-9 pr-4 py-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-950 text-sm text-gray-900 dark:text-gray-100 placeholder:text-gray-500 dark:placeholder:text-gray-400 focus:ring-2 focus:ring-orange-500 focus:border-transparent"
                />
              </div>
            </div>
          )}

          <nav className="p-2 md:p-4 flex md:flex-col overflow-x-auto md:overflow-y-auto space-x-2 md:space-x-0 md:space-y-1 hide-scrollbar">
            {tabs.map((tab) => {
              const Icon = tab.icon;
              const isActive = activeTab === tab.id;
              // @ts-expect-error - tabMatches might be undefined or have different index signature
              const hasMatch = tabMatches && tabMatches[tab.id] < 0.6;

              return (
                <button
                  key={tab.id}
                  // @ts-expect-error - setActiveTab expects specific enum/string
                  onClick={() => setActiveTab(tab.id)}
                  className={`
                    flex shrink-0 items-center justify-between px-3 py-2 rounded-lg border text-sm font-medium transition-colors whitespace-nowrap
                    ${
                      isActive
                        ? "settings-tab-active shadow-sm"
                        : "border-transparent settings-tab-inactive"
                    }
                  `}
                >
                  <div className="flex items-center gap-3">
                    <Icon
                      className={`w-4 h-4 ${isActive ? "text-orange-800 dark:text-orange-200" : "contrast-icon-muted"}`}
                    />
                    {tab.label}
                  </div>
                  {hasMatch && (
                    <span className="w-2 h-2 rounded-full bg-orange-500" />
                  )}
                </button>
              );
            })}
          </nav>

          <div className="p-4 border-t contrast-border">
            <div className="flex items-center justify-center text-sm contrast-helper">
              {forcePasswordChange ? (
                <>
                  <Shield className="w-4 h-4 mr-2" />
                  Password change required
                </>
              ) : saving ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin mr-2" />
                  Saving changes...
                </>
              ) : (
                <>
                  <Save className="w-4 h-4 mr-2" />
                  All changes saved
                </>
              )}
            </div>
          </div>
        </div>

        {/* Content Area */}
        <div className="flex-1 overflow-y-auto p-4 md:p-8 shrink-0 min-h-0">
          {loading ? (
            <div className="flex items-center justify-center h-full text-gray-600 dark:text-gray-300">
              <Loader2 className="w-6 h-6 animate-spin mr-2" />
              Loading settings...
            </div>
          ) : (
            <div className="max-w-4xl mx-auto">
              {forcePasswordChange && (
                <div className="mb-6 rounded-lg border border-orange-300 bg-orange-50 px-4 py-3 text-sm text-orange-900 dark:border-orange-500/40 dark:bg-orange-900/20 dark:text-orange-100">
                  Your account must change its password before Nojoin will allow access to other authenticated features.
                </div>
              )}
              {activeTab === "general" && (
                <GeneralSettings
                  settings={settings}
                  onUpdate={setSettings}
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
              {activeTab === "account" && (
                <AccountSettings forcePasswordChange={forcePasswordChange} />
              )}
              {activeTab === "admin" && isAdmin && (
                <AdminSettings
                  settings={settings}
                  onUpdateSettings={setSettings}
                  onPersistSettings={persistSettingsNow}
                  isAdmin={isAdmin}
                  searchQuery={searchQuery}
                />
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
