"use client";

import React, {
  useState,
  useEffect,
  useMemo,
  useCallback,
} from "react";
import { useSearchParams } from "next/navigation";
import { getSettings, updateSettings, getUserMe } from "@/lib/api";
import { Settings, UserRole } from "@/types";
import { isValidUrl } from "@/lib/validation";
import { Loader2, Search } from "lucide-react";
import VersionTag from "./VersionTag";
import AISettings from "./AISettings";
import CaptureSettings from "./CaptureSettings";
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

export default function SettingsPage() {
  const searchParams = useSearchParams();
  const [activeTab, setActiveTab] = useState<SettingsSectionId>("personal");
  const [settings, setSettings] = useState<Settings>({});
  const [loading, setLoading] = useState(true);
  const [mounted, setMounted] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [isAdmin, setIsAdmin] = useState(false);
  const [userId, setUserId] = useState<number | null>(null);
  const [currentUsername, setCurrentUsername] = useState<string | null>(null);
  const [forcePasswordChange, setForcePasswordChange] = useState(false);
  const [accountAutosaveState, setAccountAutosaveState] =
    useState<SettingsAutosaveSnapshot | null>(null);

  const validateSettings = (settings: Settings): string | null => {
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
    if (
      settings.meeting_edge_context_level !== undefined &&
      (settings.meeting_edge_context_level < 1 ||
        settings.meeting_edge_context_level > 5)
    ) {
      return "Meeting Edge Technical Context must be between 1 and 5.";
    }
    return null;
  };

  const {
    autosaveState: mainAutosaveState,
    markAsSaved: markMainAutosaveSaved,
    saveNow: saveMainAutosaveNow,
  } = useDebouncedAutosave<Settings>({
    value: settings,
    enabled: !loading && !forcePasswordChange,
    serialize: (value) => JSON.stringify(value),
    validate: validateSettings,
    save: async (value) => {
      await updateSettings(value);
      setCachedUserTimeZone(value.timezone);
    },
    pendingMessage: "Changes pending...",
    savingMessage: "Saving changes...",
    savedMessage: "All changes saved",
    fallbackErrorMessage: "Failed to save settings",
  });

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
      const loadedAutosaveValue: Settings = {};

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
        markMainAutosaveSaved(safeSettings);

            } catch (e: unknown) {
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

      await saveMainAutosaveNow(nextSettings);
    },
    [forcePasswordChange, saveMainAutosaveNow],
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
          {activeTab === "capture" && (
            <CaptureSettings searchQuery={searchQuery} />
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
