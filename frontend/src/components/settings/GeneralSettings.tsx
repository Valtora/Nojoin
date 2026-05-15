import { Fragment, useMemo, useState } from "react";
import { Popover, Transition } from "@headlessui/react";
import { useTheme, Theme } from "@/lib/ThemeProvider";
import { fuzzyMatch } from "@/lib/searchUtils";
import { Settings } from "@/types";
import {
  Mic,
  Activity,
  Users,
  Type,
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

interface GeneralSettingsProps {
  settings: Settings;
  onUpdate: (newSettings: Settings) => void;
  searchQuery?: string;
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

  const showAppearance = fuzzyMatch(searchQuery, [
    "appearance",
    "theme",
    "light",
    "dark",
    "mode",
    "color",
  ]);
  const showDateTime = fuzzyMatch(searchQuery, [
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
  const showProcessing = fuzzyMatch(searchQuery, [
    "processing",
    "vad",
    "silence",
    "diarization",
    "title",
    "inference",
    "speakers",
    "notes",
  ]);

  const showSpellCheck = fuzzyMatch(searchQuery, [
    "spellcheck",
    "spell",
    "language",
    "dictionary",
  ]);

  if (!showAppearance && !showDateTime && !showProcessing && !showSpellCheck && searchQuery)
    return <div className="contrast-helper">No matching settings found.</div>;

  return (
    <div className="space-y-8">
      {showAppearance && (
        <div>
          <h3 className="text-lg font-medium text-gray-900 dark:text-white mb-4">
            Appearance
          </h3>
          <div className="grid grid-cols-1 gap-4 max-w-xl">
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                Theme
              </label>
              <select
                value={theme}
                onChange={handleThemeChange}
                className="w-full p-2 rounded-lg border border-gray-400 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500 focus:border-transparent"
              >
                <option value="system">System Default</option>
                <option value="light">Light</option>
                <option value="dark">Dark</option>
              </select>
              <p className="mt-1 text-xs contrast-helper">
                Choose your preferred visual theme.
              </p>
            </div>
          </div>
        </div>
      )}

      {showDateTime && (
        <div>
          <h3 className="text-lg font-medium text-gray-900 dark:text-white mb-4 flex items-center gap-2">
            <Clock3 className="w-5 h-5 text-orange-500" /> Date & Time
          </h3>
          <div className="grid grid-cols-1 gap-4 max-w-xl">
            <div className="space-y-3">
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                  Timezone
                </label>
                <Popover className="relative block">
                  {({ open, close }) => (
                    <>
                      <Popover.Button className="flex w-full items-center justify-between gap-3 rounded-lg border border-gray-400 bg-white px-3 py-2 text-left text-gray-900 transition-colors focus:outline-none focus:ring-2 focus:ring-orange-500 focus:border-transparent dark:border-gray-700 dark:bg-gray-800 dark:text-white">
                        <div className="min-w-0">
                          <span className="block truncate text-sm font-medium">
                            {selectedTimeZone}
                          </span>
                          <span className="mt-0.5 block text-xs contrast-helper">
                            Select the timezone used across your dashboard.
                          </span>
                        </div>
                        <ChevronDown
                          className={`h-4 w-4 shrink-0 text-gray-500 transition-transform dark:text-gray-400 ${open ? "rotate-180" : ""}`}
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
                        <Popover.Panel className="absolute left-0 z-20 mt-2 w-full overflow-hidden rounded-xl border border-gray-200 bg-white shadow-xl dark:border-gray-700 dark:bg-gray-900">
                          <div className="border-b border-gray-200 p-3 dark:border-gray-800">
                            <div className="relative">
                              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
                              <input
                                type="text"
                                value={timezoneSearch}
                                onChange={(event) =>
                                  setTimezoneSearch(event.target.value)
                                }
                                placeholder="Filter timezones"
                                autoFocus
                                className="w-full rounded-lg border border-gray-300 bg-white py-2 pl-9 pr-3 text-sm text-gray-900 outline-none transition-colors focus:border-transparent focus:ring-2 focus:ring-orange-500 dark:border-gray-700 dark:bg-gray-800 dark:text-white"
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
                                        ? "border-orange-500 bg-orange-50 dark:bg-orange-900/10"
                                        : "border-transparent hover:bg-gray-50 dark:hover:bg-gray-800"
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
                                      className="h-4 w-4 shrink-0 accent-orange-600"
                                    />
                                    <div className="min-w-0 flex-1">
                                      <div className="flex flex-wrap items-center gap-2">
                                        <span className="text-sm font-medium text-gray-900 dark:text-white">
                                          {timeZone}
                                        </span>
                                        {timeZone === DEFAULT_TIME_ZONE && (
                                          <span className="rounded-full bg-gray-200 px-2 py-0.5 text-[11px] font-medium uppercase tracking-wide text-gray-700 dark:bg-gray-800 dark:text-gray-300">
                                            UTC
                                          </span>
                                        )}
                                        {isBrowserDetected && (
                                          <span className="rounded-full bg-orange-100 px-2 py-0.5 text-[11px] font-medium uppercase tracking-wide text-orange-700 dark:bg-orange-900/20 dark:text-orange-200">
                                            Browser detected
                                          </span>
                                        )}
                                      </div>
                                    </div>
                                    {isSelected && (
                                      <Check className="h-4 w-4 shrink-0 text-orange-600 dark:text-orange-300" />
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
                <p className="mt-1 text-xs contrast-helper">
                  Calendar events and task deadlines are shown in this timezone.
                  Task deadlines are converted to UTC when saved so they remain
                  stable if you travel or later change timezone.
                </p>
              </div>

              <p className="text-xs contrast-helper">
                Browser detected: {browserTimeZone}
              </p>
            </div>
          </div>
        </div>
      )}

      {showSpellCheck && (
        <div>
          <h3 className="text-lg font-medium text-gray-900 dark:text-white mb-4 flex items-center gap-2">
            <SpellCheck className="w-5 h-5 text-orange-500" /> Spell Check
          </h3>
          <div className="grid grid-cols-1 gap-4 max-w-xl">
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                Language
              </label>
              <select
                value={settings.spellcheck_language || "en-GB"}
                onChange={(e) => handleLanguageChange(e.target.value)}
                className="w-full p-2 rounded-lg border border-gray-400 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500 focus:border-transparent"
              >
                <option value="disabled">Disabled</option>
                {Object.entries(SPELLCHECK_LANGUAGES).map(([locale, meta]) => (
                  <option key={locale} value={locale}>
                    {meta.label}
                  </option>
                ))}
              </select>
              <p className="mt-1 text-xs contrast-helper">
                Select the language for spell checking in meeting notes.
              </p>
            </div>

            {/* Custom Dictionary Management */}
            <div className="flex items-center gap-2">
              <button
                onClick={() => setIsDictionaryModalOpen(true)}
                className="px-3 py-2 text-sm font-medium text-gray-700 dark:text-gray-200 bg-gray-100 dark:bg-gray-800 hover:bg-gray-200 dark:hover:bg-gray-700 rounded-lg border border-gray-300 dark:border-gray-600 transition-colors"
              >
                Manage Dictionary
              </button>
            </div>
          </div>
        </div>
      )}

      <DictionaryModal
        isOpen={isDictionaryModalOpen}
        onClose={() => setIsDictionaryModalOpen(false)}
      />

      {showProcessing && (
        <div>
          <h3 className="text-lg font-medium text-gray-900 dark:text-white mb-4 flex items-center gap-2">
            <Activity className="w-5 h-5 text-orange-500" /> Processing &
            Intelligence
          </h3>
          <div className="max-w-2xl space-y-4">
            {/* VAD Toggle */}
            <div className="flex items-start gap-3 p-3 bg-gray-100 dark:bg-gray-800/50 rounded-lg border border-gray-300 dark:border-gray-600">
              <div className="mt-1">
                <Mic className="w-5 h-5 text-blue-500" />
              </div>
              <div className="flex-1">
                <div className="flex items-center justify-between">
                  <label className="text-sm font-medium text-gray-900 dark:text-white">
                    Voice Activity Detection (VAD)
                  </label>
                  <Switch
                    checked={settings.enable_vad !== false} // Default true
                    onCheckedChange={(checked) =>
                      onUpdate({ ...settings, enable_vad: checked })
                    }
                  />
                </div>
                <p className="mt-1 text-xs contrast-helper">
                  Filters out silence and background noise before transcription.
                  Disabling this may increase processing time but can help if
                  quiet speech is being cut off.
                </p>
              </div>
            </div>

            {/* Diarization Toggle */}
            <div className="flex items-start gap-3 p-3 bg-gray-100 dark:bg-gray-800/50 rounded-lg border border-gray-300 dark:border-gray-600">
              <div className="mt-1">
                <Users className="w-5 h-5 text-purple-500" />
              </div>
              <div className="flex-1">
                <div className="flex items-center justify-between">
                  <label className="text-sm font-medium text-gray-900 dark:text-white">
                    Speaker Diarization
                  </label>
                  <Switch
                    checked={settings.enable_diarization !== false} // Default true
                    onCheckedChange={(checked) =>
                      onUpdate({ ...settings, enable_diarization: checked })
                    }
                  />
                </div>
                <p className="mt-1 text-xs contrast-helper">
                  Distinguishes between different speakers (e.g., &quot;Speaker
                  1&quot;, &quot;Speaker 2&quot;). Disable this for
                  single-speaker recordings to speed up processing.
                </p>
              </div>
            </div>

            <div className="flex items-start gap-3 p-3 bg-gray-100 dark:bg-gray-800/50 rounded-lg border border-gray-300 dark:border-gray-600">
              <div className="mt-1">
                <Type className="w-5 h-5 text-green-500" />
              </div>
              <div className="flex-1">
                <div className="flex items-center justify-between">
                  <label className="text-sm font-medium text-gray-900 dark:text-white">
                    Prefer Short Titles
                  </label>
                  <Switch
                    checked={settings.prefer_short_titles !== false}
                    onCheckedChange={(checked) =>
                      onUpdate({ ...settings, prefer_short_titles: checked })
                    }
                  />
                </div>
                <p className="mt-1 text-xs contrast-helper">
                  Use concise 3-5 word AI-generated meeting titles instead of
                  longer descriptive ones.
                </p>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Custom Instructions (Moved from AI Settings) */}
    </div>
  );
}
