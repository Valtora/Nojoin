"use client";

import React, {
  useState,
  useEffect,
  useMemo,
  useRef,
  useCallback,
} from "react";
import { getSettings, updateSettings, getUserMe } from "@/lib/api";
import { Settings, CompanionDevices } from "@/types";
import { isValidUrl } from "@/lib/validation";
import {
  Save,
  Loader2,
  Settings as SettingsIcon,
  Mic,
  Search,
  User,
  Shield,
  PlayCircle,
} from "lucide-react";
import { getMatchScore } from "@/lib/searchUtils";
import { TAB_KEYWORDS } from "./keywords";
import VersionTag from "./VersionTag";
import GeneralSettings from "./GeneralSettings";
import AudioSettings from "./AudioSettings";
import AccountSettings from "./AccountSettings";
import AdminSettings from "./AdminSettings";
import HelpSettings from "./HelpSettings";
import { useNotificationStore } from "@/lib/notificationStore";

type Tab = "general" | "audio" | "help" | "account" | "admin";

interface CompanionConfig {
  api_port: number;
  local_port: number;
  min_meeting_length?: number;
}

const COMPANION_URL = "http://127.0.0.1:12345";

export default function SettingsPage() {
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
  const { addNotification } = useNotificationStore();

  const saveTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const isFirstLoad = useRef(true);
  const lastSavedState = useRef<string>("");

  const refreshCompanionConfig = useCallback(async () => {
    try {
      const res = await fetch(`${COMPANION_URL}/config`);
      if (res.ok) {
        const companionData: CompanionConfig = await res.json();
        setCompanionConfig(companionData);
      }

      const devicesRes = await fetch(`${COMPANION_URL}/devices`);
      if (devicesRes.ok) {
        const devicesData: CompanionDevices = await devicesRes.json();
        setCompanionDevices(devicesData);
      }
      return true;
    } catch (e) {
      console.error("Failed to refresh companion config", e);
      return false;
    }
  }, []);

  // Determine which tabs have matches and their scores
  const tabMatches = useMemo(() => {
    if (!searchQuery) return null;

    const matches: Record<Tab, number> = {
      general: getMatchScore(searchQuery, TAB_KEYWORDS.general),
      audio: getMatchScore(searchQuery, TAB_KEYWORDS.audio),
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
    setMounted(true);
    return () => setMounted(false);
  }, []);

  useEffect(() => {
    const load = async () => {
      let currentSettings = {};
      let currentCompanionConfig: CompanionConfig | null = null;
      let currentInputDevice: string | null = null;
      let currentOutputDevice: string | null = null;

      try {
        const [settingsData, userData] = await Promise.all([
          getSettings(),
          getUserMe(),
        ]);

        // Ensure settingsData is an object (API might return null)
        const safeSettings = settingsData || {};
        setSettings(safeSettings);
        currentSettings = safeSettings;
        setIsAdmin(userData.is_superuser);
        setUserId(userData.id);

        // Try to load companion config (always at localhost:12345)
        try {
          const res = await fetch(`${COMPANION_URL}/config`);
          if (res.ok) {
            const companionData: CompanionConfig = await res.json();
            setCompanionConfig(companionData);
            currentCompanionConfig = companionData;
          }

          // Fetch available devices
          const devicesRes = await fetch(`${COMPANION_URL}/devices`);
          if (devicesRes.ok) {
            const devicesData: CompanionDevices = await devicesRes.json();
            setCompanionDevices(devicesData);
            setSelectedInputDevice(devicesData.selected_input);
            setSelectedOutputDevice(devicesData.selected_output);
            currentInputDevice = devicesData.selected_input;
            currentOutputDevice = devicesData.selected_output;
          }
        } catch (e) {
          console.error("Failed to load companion config/devices", e);
          setCompanionDevices(null);
        }
      } catch (e) {
        console.error("Failed to load settings", e);
      }

      // Update last saved state to prevent auto-save on load
      lastSavedState.current = JSON.stringify({
        settings: currentSettings,
        companionApiPort: currentCompanionConfig?.api_port,
        companionMinLength: currentCompanionConfig?.min_meeting_length,
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
    const error = validateSettings(settings, companionConfig);
    if (error) {
      addNotification({ type: "error", message: error });
      return;
    }
    setSaving(true);
    try {
      await updateSettings(settings);

      // Save companion config (api_port) if available
      if (companionConfig) {
        await fetch(`${COMPANION_URL}/config`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            api_port: companionConfig.api_port,
            min_meeting_length: companionConfig.min_meeting_length,
          }),
        });
      }

      // Save device selections if companion is connected
      if (companionDevices) {
        await fetch(`${COMPANION_URL}/config`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            input_device_name: selectedInputDevice,
            output_device_name: selectedOutputDevice,
          }),
        });
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
  ]);

  useEffect(() => {
    if (loading) return;

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
    saveSettings,
  ]);

  const tabs = useMemo(() => {
    const baseTabs = [
      { id: "general", label: "General", icon: SettingsIcon },
      { id: "audio", label: "Audio & Recording", icon: Mic },
      { id: "help", label: "Help", icon: PlayCircle },
      { id: "account", label: "Account", icon: User },
    ] as const;

    const adminTab = { id: "admin", label: "Admin Panel", icon: Shield };

    if (isAdmin) {
      return [...baseTabs, adminTab];
    }
    return baseTabs;
  }, [isAdmin]);

  if (!mounted) return null;

  return (
    <div className="flex flex-col h-full bg-gray-50 dark:bg-gray-900">
      {/* Header */}
      <div className="flex items-center justify-between px-4 pl-14 md:px-8 py-4 md:py-6 bg-white dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700 shrink-0">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white">
            Settings
          </h1>
          <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
            Manage your application preferences and configurations.
          </p>
        </div>
        <VersionTag />
      </div>

      <div className="flex flex-col md:flex-row flex-1 overflow-hidden">
        {/* Sidebar */}
        <div className="w-full md:w-64 bg-gray-200 dark:bg-gray-800 border-b md:border-b-0 md:border-r border-gray-300 dark:border-gray-700 flex flex-col shrink-0">
          <div className="p-4 border-b border-gray-300 dark:border-gray-700">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500" />
              <input
                type="text"
                placeholder="Search settings..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-full pl-9 pr-4 py-2 rounded-lg border border-gray-400 dark:border-gray-700 bg-white dark:bg-gray-900 text-sm focus:ring-2 focus:ring-orange-500 focus:border-transparent"
              />
            </div>
          </div>

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
                    flex shrink-0 items-center justify-between px-3 py-2 rounded-lg text-sm font-medium transition-colors whitespace-nowrap
                    ${
                      isActive
                        ? "bg-orange-100 dark:bg-orange-900/20 text-orange-800 dark:text-orange-400"
                        : "text-gray-700 dark:text-gray-300 hover:bg-gray-300 dark:hover:bg-gray-700/50"
                    }
                  `}
                >
                  <div className="flex items-center gap-3">
                    <Icon
                      className={`w-4 h-4 ${isActive ? "text-orange-700 dark:text-orange-400" : "text-gray-500 dark:text-gray-500"}`}
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

          <div className="p-4 border-t border-gray-300 dark:border-gray-700">
            <div className="flex items-center justify-center text-sm text-gray-500 dark:text-gray-400">
              {saving ? (
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
            <div className="flex items-center justify-center h-full text-gray-500">
              <Loader2 className="w-6 h-6 animate-spin mr-2" />
              Loading settings...
            </div>
          ) : (
            <div className="max-w-4xl mx-auto">
              {activeTab === "general" && (
                <GeneralSettings
                  settings={settings}
                  onUpdate={setSettings}
                  searchQuery={searchQuery}
                />
              )}
              {activeTab === "audio" && (
                <AudioSettings
                  settings={settings}
                  onUpdateSettings={setSettings}
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
              {activeTab === "help" && (
                <HelpSettings userId={userId} searchQuery={searchQuery} />
              )}
              {activeTab === "account" && <AccountSettings />}
              {activeTab === "admin" && isAdmin && (
                <AdminSettings
                  settings={settings}
                  onUpdateSettings={setSettings}
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
