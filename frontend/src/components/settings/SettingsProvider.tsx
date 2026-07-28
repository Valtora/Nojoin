"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { getSettings, getUserMe, updateSettings } from "@/lib/api";
import { isValidUrl } from "@/lib/validation";
import { isValidTimeZone, setCachedUserTimeZone } from "@/lib/timezone";
import { Settings, UserRole } from "@/types";

import SettingsAutosaveState, {
  type SettingsAutosaveSnapshot,
} from "./SettingsAutosaveState";
import { mergeAutosaveStates } from "./settingsState";
import useDebouncedAutosave from "./useDebouncedAutosave";

/**
 * Owns everything a settings page needs, at the layout level.
 *
 * This lives above the category routes on purpose. `useDebouncedAutosave`
 * cancels its pending timer on unmount and does not flush, so a hook owned by a
 * route component would silently discard an edit made within a second of
 * navigating to another category. Because the layout does not remount when the
 * route below it changes, one provider here keeps the debounce, the settings
 * object and the save status alive across navigation, and issues exactly one
 * GET /settings per session rather than one per category visited.
 */

interface SettingsContextValue {
  settings: Settings;
  /** Debounced apply for continuous controls: sliders, numbers, text. */
  updateSetting: (next: Settings) => void;
  /** Immediate save for discrete controls: selects, switches, radios. */
  persistNow: (next: Settings) => Promise<void>;
  isAdmin: boolean;
  userId: number | null;
  username: string | null;
  setUsername: (username: string) => void;
  forcePasswordChange: boolean;
  loading: boolean;
  autosave: SettingsAutosaveSnapshot;
  /** Account settings save on their own schedule; their status merges into the footer. */
  setAccountAutosaveState: (snapshot: SettingsAutosaveSnapshot | null) => void;
}

const SettingsContext = createContext<SettingsContextValue | null>(null);

export function useSettingsContext(): SettingsContextValue {
  const context = useContext(SettingsContext);

  if (!context) {
    throw new Error("useSettingsContext must be used within a SettingsProvider");
  }

  return context;
}

function validateSettings(settings: Settings): string | null {
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
    !["gemini", "openai", "anthropic", "ollama"].includes(settings.llm_provider)
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
}

export default function SettingsProvider({ children }: { children: ReactNode }) {
  const [settings, setSettings] = useState<Settings>({});
  const [loading, setLoading] = useState(true);
  const [isAdmin, setIsAdmin] = useState(false);
  const [userId, setUserId] = useState<number | null>(null);
  const [username, setUsername] = useState<string | null>(null);
  const [forcePasswordChange, setForcePasswordChange] = useState(false);
  const [accountAutosaveState, setAccountAutosaveState] =
    useState<SettingsAutosaveSnapshot | null>(null);

  const {
    autosaveState,
    markAsSaved,
    saveNow,
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

  useEffect(() => {
    const load = async () => {
      try {
        const userData = await getUserMe();
        setUsername(userData.username);
        setIsAdmin(
          userData.is_superuser ||
            userData.role === UserRole.OWNER ||
            userData.role === UserRole.ADMIN,
        );
        setUserId(userData.id);

        if (userData.force_password_change) {
          setForcePasswordChange(true);
          markAsSaved({});
          setLoading(false);
          return;
        }

        const settingsData = (await getSettings()) || {};
        setSettings(settingsData);
        setCachedUserTimeZone(settingsData.timezone);
        setForcePasswordChange(false);
        markAsSaved(settingsData);
      } catch (error) {
        console.error("Failed to load settings", error);
        markAsSaved({});
      }

      setLoading(false);
    };

    load();
  }, [markAsSaved]);

  const persistNow = useCallback(
    async (next: Settings) => {
      setSettings(next);
      if (forcePasswordChange) {
        return;
      }
      await saveNow(next);
    },
    [forcePasswordChange, saveNow],
  );

  const value = useMemo<SettingsContextValue>(() => {
    const autosave: SettingsAutosaveSnapshot = forcePasswordChange
      ? { status: "blocked" }
      : mergeAutosaveStates(autosaveState, accountAutosaveState);

    return {
      settings,
      updateSetting: setSettings,
      persistNow,
      isAdmin,
      userId,
      username,
      setUsername,
      forcePasswordChange,
      loading,
      autosave,
      setAccountAutosaveState,
    };
  }, [
    accountAutosaveState,
    autosaveState,
    forcePasswordChange,
    isAdmin,
    loading,
    persistNow,
    settings,
    userId,
    username,
  ]);

  return (
    <SettingsContext.Provider value={value}>
      {children}
    </SettingsContext.Provider>
  );
}

export function SettingsAutosaveFooter() {
  const { autosave } = useSettingsContext();

  return (
    <SettingsAutosaveState status={autosave.status} message={autosave.message} />
  );
}
