"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { Search } from "lucide-react";

import { cn } from "@/lib/cn";

import {
  SETTINGS_CATEGORIES,
  settingsCategoryHref,
  type SettingsCategoryId,
} from "./settingsCategories";
import { searchSettingsRegistry } from "./settingsRegistry";

interface SettingsSearchProps {
  isAdmin: boolean;
  /** Lets the shell dot-mark matching categories in the navigation. */
  onQueryChange?: (query: string) => void;
  onNavigate?: () => void;
}

/**
 * Cross-category search over individual settings.
 *
 * It resolves to a setting rather than a category, because "which page holds
 * the microphone gain" is exactly the question a fourteen-category sidebar
 * makes harder. Selecting a result routes to its category and focuses the
 * setting, expanding the Advanced block first when the match lives inside one.
 *
 * Navigation happens on selection, never on keystroke: with real routes,
 * per-character routing would fill the browser history with dead entries.
 */
export default function SettingsSearch({
  isAdmin,
  onQueryChange,
  onNavigate,
}: SettingsSearchProps) {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const containerRef = useRef<HTMLDivElement | null>(null);

  const results = useMemo(
    () => searchSettingsRegistry(query, { isAdmin }),
    [isAdmin, query],
  );

  useEffect(() => {
    onQueryChange?.(query);
  }, [onQueryChange, query]);

  useEffect(() => {
    setActiveIndex(0);
  }, [query]);

  useEffect(() => {
    if (!open) {
      return;
    }

    const handlePointerDown = (event: MouseEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    };

    document.addEventListener("mousedown", handlePointerDown);
    return () => document.removeEventListener("mousedown", handlePointerDown);
  }, [open]);

  const goTo = (category: SettingsCategoryId, entryId: string) => {
    setOpen(false);
    setQuery("");
    onNavigate?.();
    router.push(`${settingsCategoryHref(category)}#${entryId}`);
  };

  const handleKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (!results.length) {
      return;
    }

    if (event.key === "ArrowDown") {
      event.preventDefault();
      setOpen(true);
      setActiveIndex((index) => (index + 1) % results.length);
      return;
    }

    if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveIndex((index) => (index - 1 + results.length) % results.length);
      return;
    }

    if (event.key === "Enter") {
      event.preventDefault();
      const result = results[activeIndex];
      if (result) {
        goTo(result.entry.category, result.entry.id);
      }
      return;
    }

    if (event.key === "Escape") {
      setOpen(false);
    }
  };

  const showResults = open && query.trim().length > 0;

  return (
    <div ref={containerRef} className="relative">
      <Search
        className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 contrast-icon-muted"
        aria-hidden="true"
      />
      <input
        type="search"
        role="combobox"
        aria-expanded={showResults}
        aria-controls="settings-search-results"
        aria-autocomplete="list"
        placeholder="Search settings..."
        value={query}
        onChange={(event) => {
          setQuery(event.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={handleKeyDown}
        className="w-full rounded-lg border border-control-border bg-surface-card py-2 pl-9 pr-3 text-sm text-foreground placeholder:text-contrast-helper focus:border-transparent focus:ring-2 focus:ring-action"
      />

      {showResults && (
        <div
          id="settings-search-results"
          role="listbox"
          className="settings-card absolute left-0 right-0 top-full z-50 mt-2 max-h-80 overflow-y-auto p-1"
        >
          {results.length === 0 ? (
            <p className="px-3 py-4 text-sm contrast-helper">
              No settings match that. Try a broader term.
            </p>
          ) : (
            results.map((result, index) => {
              const category = SETTINGS_CATEGORIES[result.entry.category];

              return (
                <button
                  key={result.entry.id}
                  type="button"
                  role="option"
                  aria-selected={index === activeIndex}
                  onMouseEnter={() => setActiveIndex(index)}
                  onClick={() => goTo(result.entry.category, result.entry.id)}
                  className={cn(
                    "block w-full rounded-lg px-3 py-2 text-left transition-colors",
                    index === activeIndex
                      ? "bg-action-tint"
                      : "hover:bg-surface-inset",
                  )}
                >
                  <span className="block truncate text-sm font-medium text-foreground">
                    {result.entry.label}
                  </span>
                  <span className="mt-0.5 block truncate text-xs contrast-helper">
                    {category.label}
                    {result.entry.advanced ? " · Advanced" : ""}
                  </span>
                </button>
              );
            })
          )}
        </div>
      )}
    </div>
  );
}
