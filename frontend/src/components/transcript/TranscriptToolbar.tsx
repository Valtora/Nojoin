"use client";

import { useEffect, useId, useRef, useState } from "react";
import {
  ArrowRightLeft,
  ChevronDown,
  ChevronUp,
  Download,
  Redo2,
  Search,
  Settings,
  Undo2,
} from "lucide-react";

import Button from "../ui/Button";
import IconButton from "../ui/IconButton";

import type { TranscriptSearch } from "./_hooks/useTranscriptSearch";

/** A toggle that is on keeps the action tint, hovered or not. */
const PRESSED = "bg-action-tint text-action-text hover:bg-action-tint hover:text-action-text";

/**
 * Find Next, Replace and Replace All are `Button` at `sm`, 32 px. On a touch
 * screen they take the full 44 px target.
 */
const TOOLBAR_BUTTON_TOUCH = "pointer-coarse:h-[44px]";

interface TranscriptToolbarProps {
  onUndo: () => void;
  onRedo: () => void;
  canUndo: boolean;
  canRedo: boolean;
  onExport: () => void;
  /** An edit is open or saving. */
  exportDisabled: boolean;
  showSearch: boolean;
  setShowSearch: (value: boolean) => void;
  showReplace: boolean;
  setShowReplace: (value: boolean) => void;
  search: TranscriptSearch;
  isSubmitting: boolean;
  onReplaceCurrent: () => void;
  onReplaceAll: () => void;
}

/** The finished transcript's toolbar: undo, redo, export, and find/replace. */
export default function TranscriptToolbar({
  onUndo,
  onRedo,
  canUndo,
  canRedo,
  onExport,
  exportDisabled,
  showSearch,
  setShowSearch,
  showReplace,
  setShowReplace,
  search,
  isSubmitting,
  onReplaceCurrent,
  onReplaceAll,
}: TranscriptToolbarProps) {
  const [showSettings, setShowSettings] = useState(false);
  const settingsRef = useRef<HTMLDivElement>(null);
  const settingsToggleRef = useRef<HTMLButtonElement>(null);
  const settingsPanelId = useId();

  // Escape closes the settings menu. Focus inside it goes back to the toggle,
  // so it isn't lost with the menu.
  useEffect(() => {
    if (!showSettings) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (settingsRef.current?.contains(document.activeElement)) {
        settingsToggleRef.current?.focus();
      }
      setShowSettings(false);
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [showSettings]);

  const {
    findText,
    setFindText,
    replaceText,
    setReplaceText,
    caseSensitive,
    setCaseSensitive,
    isFuzzy,
    setIsFuzzy,
    useRegex,
    setUseRegex,
    matches,
    currentMatchIndex,
    nextMatch,
    prevMatch,
  } = search;
  const exportTitle = exportDisabled
    ? "Finish the current transcript edit before exporting"
    : "Export";

  return (
    <div className="z-10 flex flex-col border-b-2 border-surface-border bg-surface-inset shadow-card">
      {/* Row 1: Header & Global Actions */}
      {/* The 40 px buttons sit in less padding, so the row keeps its height
          from sm up. */}
      <div className="flex items-center justify-end overflow-x-auto px-2 py-1 sm:px-4 sm:py-1.5 md:px-5 md:py-2">
        <div className="flex items-center gap-0.5 sm:gap-1">
          <IconButton
            size="sm"
            aria-label="Undo"
            title="Undo"
            onClick={onUndo}
            disabled={!canUndo}
            icon={<Undo2 aria-hidden="true" />}
          />
          <IconButton
            size="sm"
            aria-label="Redo"
            title="Redo"
            onClick={onRedo}
            disabled={!canRedo}
            icon={<Redo2 aria-hidden="true" />}
          />
          <div className="mx-0.5 h-4 w-px bg-surface-card sm:mx-1" />
          <IconButton
            size="sm"
            aria-label={exportDisabled ? "Export transcript disabled" : "Export transcript"}
            title={exportTitle}
            onClick={onExport}
            disabled={exportDisabled}
            icon={<Download aria-hidden="true" />}
          />
          <IconButton
            size="sm"
            aria-label="Search"
            title="Search"
            aria-pressed={showSearch}
            onClick={() => {
              const newState = !showSearch;
              setShowSearch(newState);
              if (!newState) setShowReplace(false);
            }}
            className={showSearch ? PRESSED : undefined}
            icon={<Search aria-hidden="true" />}
          />
          <IconButton
            size="sm"
            aria-label="Find & Replace"
            title="Find & Replace"
            aria-pressed={showReplace}
            onClick={() => {
              if (showReplace) {
                setShowReplace(false);
                setShowSearch(false);
              } else {
                setShowReplace(true);
                setShowSearch(true);
              }
            }}
            className={showReplace ? PRESSED : undefined}
            icon={<ArrowRightLeft aria-hidden="true" />}
          />
        </div>
      </div>

      {/* Row 2: Search & Replace Controls */}
      {(showSearch || showReplace) && (
        <div className="animate-in slide-in-from-top-2 flex flex-wrap items-center gap-1.5 border-t border-control-border px-2 pb-2 pt-2 duration-200 sm:gap-2 sm:px-4 sm:pb-3 sm:pt-3 md:px-5">
          <div className="flex min-w-40 flex-[1_1_11rem] items-center gap-0.5 sm:min-w-48 sm:flex-[1_1_14rem]">
            <div className="relative min-w-0 flex-1">
              <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-4 h-4 text-contrast-icon-muted" />
              <input
                placeholder="Find..."
                value={findText}
                onChange={(e) => setFindText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    if (e.shiftKey) prevMatch();
                    else nextMatch();
                  }
                }}
                className="w-full pl-8 pr-20 py-1.5 text-sm rounded-md border border-control-border bg-surface-inset focus:ring-2 focus:ring-action outline-none min-w-0"
                autoFocus
              />
              {matches.length > 0 && (
                <span className="absolute right-1 top-1/2 -translate-y-1/2 text-xs text-contrast-helper whitespace-nowrap bg-surface-card rounded px-1">
                  {currentMatchIndex + 1} of {matches.length}
                </span>
              )}
            </div>
            {/* Beside the field rather than inside it, so each is a 40 px
                target. They stay while there are no matches, so the row
                doesn't shift as the matches come and go. */}
            <IconButton
              size="sm"
              aria-label="Previous match"
              title="Previous match (Shift+Enter)"
              onClick={prevMatch}
              disabled={matches.length === 0}
              icon={<ChevronUp aria-hidden="true" />}
            />
            <IconButton
              size="sm"
              aria-label="Next match"
              title="Next match (Enter)"
              onClick={nextMatch}
              disabled={matches.length === 0}
              icon={<ChevronDown aria-hidden="true" />}
            />
          </div>
          {showReplace && (
            <div className="relative min-w-40 flex-[1_1_11rem] sm:min-w-48 sm:flex-[1_1_14rem]">
              <ArrowRightLeft className="absolute left-2 top-1/2 -translate-y-1/2 w-4 h-4 text-contrast-icon-muted" />
              <input
                placeholder="Replace..."
                value={replaceText}
                onChange={(e) => setReplaceText(e.target.value)}
                className="w-full pl-8 pr-2 py-1.5 text-sm rounded-md border border-control-border bg-surface-inset focus:ring-2 focus:ring-action outline-none min-w-0"
              />
            </div>
          )}
          {showReplace && (
            <div className="flex min-w-0 flex-[1_0_auto] flex-wrap items-center justify-end gap-1.5 sm:gap-2">
              {/* Settings Toggle */}
              <div ref={settingsRef} className="relative">
                <IconButton
                  ref={settingsToggleRef}
                  size="sm"
                  aria-label="Advanced Search Settings"
                  title="Advanced Search Settings"
                  aria-expanded={showSettings}
                  aria-controls={showSettings ? settingsPanelId : undefined}
                  onClick={() => setShowSettings(!showSettings)}
                  className={showSettings ? PRESSED : undefined}
                  icon={<Settings aria-hidden="true" />}
                />

                {/* Settings Dropdown */}
                {showSettings && (
                  <>
                    <div
                      className="fixed inset-0 z-[var(--z-dropdown)]"
                      onClick={() => setShowSettings(false)}
                    />
                    <div
                      id={settingsPanelId}
                      className="absolute right-0 top-full mt-2 w-48 bg-surface-card rounded-lg shadow-float border border-surface-border p-2 z-[var(--z-dropdown)] flex flex-col gap-1"
                    >
                      <div className="text-xs font-semibold text-contrast-icon-muted px-2 py-1 mb-1 border-b border-surface-border">
                        Search Options
                      </div>
                      <label className="flex items-center gap-2 px-2 py-1.5 hover:bg-surface-inset rounded cursor-pointer">
                        <input
                          type="checkbox"
                          checked={caseSensitive}
                          onChange={(e) => setCaseSensitive(e.target.checked)}
                          className="rounded border-control-border text-action-text focus:ring-action w-4 h-4"
                        />
                        <span className="text-sm text-contrast-muted">
                          Case Sensitive
                        </span>
                      </label>
                      <label className="flex items-center gap-2 px-2 py-1.5 hover:bg-surface-inset rounded cursor-pointer">
                        <input
                          type="checkbox"
                          checked={isFuzzy}
                          onChange={(e) => {
                            setIsFuzzy(e.target.checked);
                            if (e.target.checked) setUseRegex(false);
                          }}
                          className="rounded border-control-border text-action-text focus:ring-action w-4 h-4"
                        />
                        <span className="text-sm text-contrast-muted">
                          Fuzzy Match
                        </span>
                      </label>
                      <label className="flex items-center gap-2 px-2 py-1.5 hover:bg-surface-inset rounded cursor-pointer">
                        <input
                          type="checkbox"
                          checked={useRegex}
                          onChange={(e) => {
                            setUseRegex(e.target.checked);
                            if (e.target.checked) setIsFuzzy(false);
                          }}
                          className="rounded border-control-border text-action-text focus:ring-action w-4 h-4"
                        />
                        <span className="text-sm text-contrast-muted">
                          Regex
                        </span>
                      </label>
                    </div>
                  </>
                )}
              </div>

              <div className="mx-0.5 h-4 w-px bg-surface-card sm:mx-1" />

              <Button
                size="sm"
                onClick={nextMatch}
                disabled={matches.length === 0}
                className={`sm:text-sm ${TOOLBAR_BUTTON_TOUCH}`}
              >
                <span className="sm:hidden">Next</span>
                <span className="hidden sm:inline">Find Next</span>
              </Button>
              <Button
                size="sm"
                onClick={onReplaceCurrent}
                disabled={matches.length === 0 || isSubmitting}
                className={`sm:text-sm ${TOOLBAR_BUTTON_TOUCH}`}
              >
                Replace
              </Button>
              <Button
                size="sm"
                variant="primary"
                onClick={onReplaceAll}
                disabled={!findText || isSubmitting}
                className={`sm:text-sm ${TOOLBAR_BUTTON_TOUCH}`}
              >
                <span className="sm:hidden">All</span>
                <span className="hidden sm:inline">Replace All</span>
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
