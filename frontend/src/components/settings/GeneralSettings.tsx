import { Fragment, useMemo, useState } from "react";
import { Popover, Transition } from "@headlessui/react";
import { useTheme, Theme } from "@/lib/ThemeProvider";
import { fuzzyMatch } from "@/lib/searchUtils";
import { Settings } from "@/types";
import {
  Mic,
  Users,
  SpellCheck,
  Clock3,
  ChevronDown,
  Search,
  Check,
} from "lucide-react";
import { Switch } from "../ui/Switch";
import { SPELLCHECK_LANGUAGES, spellCheckService } from "@/lib/spellCheckService";
import DictionaryModal from "../DictionaryModal";
import {
  DEFAULT_TIME_ZONE,
  getBrowserTimeZone,
  getSupportedTimeZones,
  resolveTimeZone,
  setCachedUserTimeZone,
} from "@/lib/timezone";
import SettingsCallout from "./SettingsCallout";
import SettingsCard from "./SettingsCard";
import SettingsRow from "./SettingsRow";
import {
  SETTINGS_BUTTON_SECONDARY,
  SETTINGS_SELECT_CLASS,
} from "./settingsControls";

/**
 * The sections this component can render. They now live on two different
 * category pages — appearance, date and time, and spellcheck under Appearance;
 * the processing defaults under Recording, next to the capture controls they
 * belong with — so the caller selects which it wants.
 */
export type GeneralSettingsSection =
  | "appearance"
  | "dateTime"
  | "spellcheck"
  | "processing";

const ALL_GENERAL_SECTIONS: GeneralSettingsSection[] = [
  "appearance",
  "dateTime",
  "spellcheck",
  "processing",
];

interface GeneralSettingsProps {
  settings: Settings;
  onUpdate: (newSettings: Settings) => void;
  searchQuery?: string;
  suppressNoMatch?: boolean;
  sections?: GeneralSettingsSection[];
}

function formatTimeZoneDisplay(timeZone: string): string {
  if (timeZone === DEFAULT_TIME_ZONE) {
    return "Coordinated Universal Time";
  }

  return timeZone
    .split("/")
    .map((part) => part.replaceAll("_", " "))
    .join(" / ");
}

export default function GeneralSettings({
  settings,
  onUpdate,
  searchQuery = "",
  suppressNoMatch = false,
  sections = ALL_GENERAL_SECTIONS,
}: GeneralSettingsProps) {
  const { theme, setTheme } = useTheme();
  const [isDictionaryModalOpen, setIsDictionaryModalOpen] = useState(false);
  const [timezoneSearch, setTimezoneSearch] = useState("");
  const browserTimeZone = useMemo(() => getBrowserTimeZone(), []);
  const selectedTimeZone = resolveTimeZone(settings.timezone, DEFAULT_TIME_ZONE);
  const supportedTimeZones = useMemo(() => {
    return Array.from(
      new Set([selectedTimeZone, browserTimeZone, ...getSupportedTimeZones()]),
    ).sort((left, right) => {
      if (left === DEFAULT_TIME_ZONE) {
        return -1;
      }

      if (right === DEFAULT_TIME_ZONE) {
        return 1;
      }

      if (left === browserTimeZone) {
        return -1;
      }

      if (right === browserTimeZone) {
        return 1;
      }

      return left.localeCompare(right);
    });
  }, [browserTimeZone, selectedTimeZone]);
  const filteredTimeZones = useMemo(() => {
    const query = timezoneSearch.trim().toLowerCase();

    if (!query) {
      return supportedTimeZones;
    }

    return supportedTimeZones.filter((timeZone) => {
      const searchTarget = `${timeZone.toLowerCase()} ${formatTimeZoneDisplay(
        timeZone,
      ).toLowerCase()}`;
      return searchTarget.includes(query);
    });
  }, [supportedTimeZones, timezoneSearch]);

  const handleLanguageChange = async (locale: string) => {
    onUpdate({ ...settings, spellcheck_language: locale });
    await spellCheckService.changeLanguage(locale);
  };

  const handleThemeChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    setTheme(e.target.value as Theme);
  };

  const handleTimeZoneSelect = (candidate: string) => {
    const resolvedTimeZone = resolveTimeZone(candidate, DEFAULT_TIME_ZONE);
    if (resolvedTimeZone === selectedTimeZone) {
      return;
    }

    setTimezoneSearch("");
    setCachedUserTimeZone(resolvedTimeZone);
    onUpdate({ ...settings, timezone: resolvedTimeZone });
  };

  const enabled = (section: GeneralSettingsSection) => sections.includes(section);

  const showAppearance =
    enabled("appearance") &&
    fuzzyMatch(searchQuery, [
      "appearance",
      "theme",
      "light",
      "dark",
      "mode",
      "color",
    ]);
  const showDateTime =
    enabled("dateTime") &&
    fuzzyMatch(searchQuery, [
      "timezone",
      "time zone",
      "date",
      "time",
      "clock",
      "utc",
      "gmt",
      "bst",
      "calendar",
      "deadline",
    ]);
  const showProcessing =
    enabled("processing") &&
    fuzzyMatch(searchQuery, [
      "processing",
      "vad",
      "silence",
      "diarization",
      "voice activity detection",
      "speaker separation",
      "speaker diarization",
      "speech",
    ]);

  const showSpellCheck =
    enabled("spellcheck") &&
    fuzzyMatch(searchQuery, [
      "spellcheck",
      "spell",
      "language",
      "dictionary",
    ]);

  if (!showAppearance && !showDateTime && !showProcessing && !showSpellCheck && searchQuery)
    return (
      suppressNoMatch ? null : (
        <SettingsCallout
          tone="neutral"
          title="No matching settings"
          message="Try a broader search term for appearance, timezone, spellcheck, or recording preferences."
        />
      )
    );

  return (
    <>
      {showAppearance && (
        <SettingsCard
          id="appearance-theme"
          title="Appearance"
          description="How Nojoin looks in your browser."
        >
          <SettingsRow label="Theme">
            <select
              value={theme}
              onChange={handleThemeChange}
              className={SETTINGS_SELECT_CLASS}
            >
              <option value="system">System default</option>
              <option value="light">Light</option>
              <option value="dark">Dark</option>
            </select>
          </SettingsRow>
        </SettingsCard>
      )}

      {showDateTime && (
        <SettingsCard
          id="appearance-timezone"
          title="Date and Time"
          description="The timezone used across the dashboard, calendars, and task deadlines."
        >
          <SettingsRow
            label="Timezone"
            description={`Browser detected: ${browserTimeZone}. Times are shown in this timezone and converted to UTC on save, so they stay stable if you travel or change timezone later.`}
            icon={<Clock3 className="h-4 w-4 contrast-icon-muted" aria-hidden="true" />}
          >
                <Popover className="relative block">
                  {({ open, close }) => (
                    <>
                      <Popover.Button className="flex w-full items-center justify-between gap-3 rounded-lg border border-control-border bg-surface-card px-3 py-2 text-left text-foreground transition-colors focus:outline-none focus:ring-2 focus:ring-action focus:border-transparent">
                        <div className="min-w-0">
                          <span className="block truncate text-sm font-medium">
                            {selectedTimeZone}
                          </span>
                          <span className="mt-0.5 block text-xs contrast-helper">
                            Select the timezone used across your dashboard.
                          </span>
                        </div>
                        <ChevronDown
                          className={`h-4 w-4 shrink-0 text-contrast-helper transition-transform ${open ? "rotate-180" : ""}`}
                        />
                      </Popover.Button>

                      <Transition
                        as={Fragment}
                        enter="transition ease-out duration-100"
                        enterFrom="transform opacity-0 scale-95"
                        enterTo="transform opacity-100 scale-100"
                        leave="transition ease-in duration-75"
                        leaveFrom="transform opacity-100 scale-100"
                        leaveTo="transform opacity-0 scale-95"
                      >
                        <Popover.Panel className="absolute left-0 z-20 mt-2 w-full overflow-hidden rounded-xl border border-surface-border bg-surface-card shadow-float">
                          <div className="border-b border-surface-border p-3">
                            <div className="relative">
                              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-contrast-icon-muted" />
                              <input
                                type="text"
                                value={timezoneSearch}
                                onChange={(event) =>
                                  setTimezoneSearch(event.target.value)
                                }
                                placeholder="Filter timezones"
                                autoFocus
                                className="w-full rounded-lg border border-control-border bg-surface-card py-2 pl-9 pr-3 text-sm text-foreground outline-none transition-colors focus:border-transparent focus:ring-2 focus:ring-action"
                              />
                            </div>
                          </div>

                          <div
                            role="radiogroup"
                            aria-label="Timezone"
                            className="max-h-80 space-y-1.5 overflow-y-auto p-2"
                          >
                            {filteredTimeZones.length > 0 ? (
                              filteredTimeZones.map((timeZone) => {
                                const isSelected = timeZone === selectedTimeZone;
                                const isBrowserDetected = timeZone === browserTimeZone;

                                return (
                                  <label
                                    key={timeZone}
                                    className={`flex cursor-pointer items-center gap-3 rounded-lg border px-3 py-2.5 transition-colors ${
                                      isSelected
                                        ? "border-action bg-action-tint"
                                        : "border-transparent hover:bg-surface-inset"
                                    }`}
                                  >
                                    <input
                                      type="radio"
                                      name="general-settings-timezone"
                                      value={timeZone}
                                      checked={isSelected}
                                      onChange={() => {
                                        handleTimeZoneSelect(timeZone);
                                        close();
                                      }}
                                      className="h-4 w-4 shrink-0 accent-action"
                                    />
                                    <div className="min-w-0 flex-1">
                                      <div className="flex flex-wrap items-center gap-2">
                                        <span className="text-sm font-medium text-foreground">
                                          {timeZone}
                                        </span>
                                        {timeZone === DEFAULT_TIME_ZONE && (
                                          <span className="rounded-full bg-surface-inset px-2 py-0.5 text-[11px] font-medium uppercase tracking-wide text-contrast-muted">
                                            UTC
                                          </span>
                                        )}
                                        {isBrowserDetected && (
                                          <span className="rounded-full bg-action-tint px-2 py-0.5 text-[11px] font-medium uppercase tracking-wide text-action-text">
                                            Browser detected
                                          </span>
                                        )}
                                      </div>
                                    </div>
                                    {isSelected && (
                                      <Check className="h-4 w-4 shrink-0 text-action-text" />
                                    )}
                                  </label>
                                );
                              })
                            ) : (
                              <div className="px-3 py-6 text-sm contrast-helper">
                                No timezones match that filter.
                              </div>
                            )}
                          </div>
                        </Popover.Panel>
                      </Transition>
                    </>
                  )}
                </Popover>
          </SettingsRow>
        </SettingsCard>
      )}

      {showSpellCheck && (
        <SettingsCard
          id="appearance-spellcheck"
          title="Spellcheck"
          description="The dictionary used when you edit notes and tasks."
        >
          <SettingsRow
            label="Language"
            icon={<SpellCheck className="h-4 w-4 contrast-icon-muted" aria-hidden="true" />}
          >
            <select
              value={settings.spellcheck_language || "en-GB"}
              onChange={(e) => handleLanguageChange(e.target.value)}
              className={SETTINGS_SELECT_CLASS}
            >
              <option value="disabled">Disabled</option>
              {Object.entries(SPELLCHECK_LANGUAGES).map(([locale, meta]) => (
                <option key={locale} value={locale}>
                  {meta.label}
                </option>
              ))}
            </select>
          </SettingsRow>

          <SettingsRow
            label="Custom words"
            description="Words you have taught Nojoin to accept."
          >
            <button
              type="button"
              onClick={() => setIsDictionaryModalOpen(true)}
              className={SETTINGS_BUTTON_SECONDARY}
            >
              Manage dictionary
            </button>
          </SettingsRow>
        </SettingsCard>
      )}

      <DictionaryModal
        isOpen={isDictionaryModalOpen}
        onClose={() => setIsDictionaryModalOpen(false)}
      />

      {showProcessing && (
        <SettingsCard
          title="Processing Defaults"
          description="How recorded audio is prepared before it is transcribed."
        >
          <SettingsRow
            id="recording-vad"
            label="Voice activity detection"
            description="Filters out silence and background noise before transcription. Disabling it may increase processing time, but can help if quiet speech is being cut off."
            icon={<Mic className="h-4 w-4 contrast-icon-muted" aria-hidden="true" />}
            controlClassName="sm:min-w-0 sm:flex sm:justify-end"
          >
            <Switch
              checked={settings.enable_vad !== false} // Default true
              onCheckedChange={(checked) =>
                onUpdate({ ...settings, enable_vad: checked })
              }
            />
          </SettingsRow>

          <SettingsRow
            id="recording-diarization"
            label="Speaker diarization"
            description="Distinguishes between speakers. Disable it for single-speaker recordings to speed up processing."
            icon={<Users className="h-4 w-4 contrast-icon-muted" aria-hidden="true" />}
            controlClassName="sm:min-w-0 sm:flex sm:justify-end"
          >
            <Switch
              checked={settings.enable_diarization !== false} // Default true
              onCheckedChange={(checked) =>
                onUpdate({ ...settings, enable_diarization: checked })
              }
            />
          </SettingsRow>
        </SettingsCard>
      )}
    </>
  );
}
