"use client";

import { useState } from "react";
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

import type { TranscriptSearch } from "./_hooks/useTranscriptSearch";

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
      <div className="flex items-center justify-end overflow-x-auto px-2 py-2 sm:px-4 sm:py-2.5 md:px-5 md:py-3">
        <div className="flex items-center gap-0.5 sm:gap-1">
          <button
            onClick={onUndo}
            disabled={!canUndo}
            className="rounded-lg p-1.5 text-contrast-helper transition-colors hover:bg-surface-inset hover:text-contrast-muted disabled:cursor-not-allowed disabled:opacity-30 sm:p-2"
            title="Undo"
          >
            <Undo2 className="w-4 h-4" />
          </button>
          <button
            onClick={onRedo}
            disabled={!canRedo}
            className="rounded-lg p-1.5 text-contrast-helper transition-colors hover:bg-surface-inset hover:text-contrast-muted disabled:cursor-not-allowed disabled:opacity-30 sm:p-2"
            title="Redo"
          >
            <Redo2 className="w-4 h-4" />
          </button>
          <div className="mx-0.5 h-4 w-px bg-surface-card sm:mx-1" />
          <button
            onClick={onExport}
            disabled={exportDisabled}
            aria-label={exportDisabled ? "Export transcript disabled" : "Export transcript"}
            className="rounded-lg p-1.5 text-contrast-helper transition-colors hover:bg-surface-inset hover:text-contrast-muted sm:p-2"
            title={exportTitle}
          >
            <Download className="w-4 h-4" />
          </button>
          <button
            onClick={() => {
              const newState = !showSearch;
              setShowSearch(newState);
              if (!newState) setShowReplace(false);
            }}
            className={`rounded-lg p-1.5 transition-colors sm:p-2 ${showSearch && !showReplace ? "bg-action-tint text-action-text" : "text-contrast-helper hover:bg-surface-inset hover:text-action-text"}`}
            title="Search"
          >
            <Search className="w-4 h-4" />
          </button>
          <button
            onClick={() => {
              if (showReplace) {
                setShowReplace(false);
                setShowSearch(false);
              } else {
                setShowReplace(true);
                setShowSearch(true);
              }
            }}
            className={`rounded-lg p-1.5 transition-colors sm:p-2 ${showReplace ? "bg-action-tint text-action-text" : "text-contrast-helper hover:bg-surface-inset hover:text-action-text"}`}
            title="Find & Replace"
          >
            <ArrowRightLeft className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Row 2: Search & Replace Controls */}
      {(showSearch || showReplace) && (
        <div className="animate-in slide-in-from-top-2 flex flex-wrap items-center gap-1.5 border-t border-control-border px-2 pb-2 pt-2 duration-200 sm:gap-2 sm:px-4 sm:pb-3 sm:pt-3 md:px-5">
          <div className="relative min-w-40 flex-[1_1_11rem] sm:min-w-48 sm:flex-[1_1_14rem]">
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
              className="w-full pl-8 pr-28 py-1.5 text-sm rounded-md border border-control-border bg-surface-inset focus:ring-2 focus:ring-action outline-none min-w-0"
              autoFocus
            />
            {matches.length > 0 && (
              <div className="absolute right-1 top-1/2 -translate-y-1/2 flex items-center gap-1 text-xs text-contrast-helper whitespace-nowrap bg-surface-card rounded px-1">
                <span>
                  {currentMatchIndex + 1} of {matches.length}
                </span>
                <button
                  onClick={prevMatch}
                  className="p-1 hover:bg-surface-inset rounded"
                >
                  <ChevronUp className="w-3 h-3" />
                </button>
                <button
                  onClick={nextMatch}
                  className="p-1 hover:bg-surface-inset rounded"
                >
                  <ChevronDown className="w-3 h-3" />
                </button>
              </div>
            )}
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
              <div className="relative">
                <button
                  onClick={() => setShowSettings(!showSettings)}
                  className={`rounded-md p-1.5 transition-colors ${showSettings ? "bg-action-tint text-action-text" : "text-contrast-helper hover:bg-surface-inset hover:text-contrast-muted"}`}
                  title="Advanced Search Settings"
                >
                  <Settings className="w-4 h-4" />
                </button>

                {/* Settings Dropdown */}
                {showSettings && (
                  <>
                    <div
                      className="fixed inset-0 z-[var(--z-dropdown)]"
                      onClick={() => setShowSettings(false)}
                    />
                    <div className="absolute right-0 top-full mt-2 w-48 bg-surface-card rounded-lg shadow-float border border-surface-border p-2 z-[var(--z-dropdown)] flex flex-col gap-1">
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

              <button
                onClick={nextMatch}
                disabled={matches.length === 0}
                className="whitespace-nowrap rounded-md border border-surface-border bg-surface-inset px-2.5 py-1.5 text-xs text-contrast-muted shadow-card hover:bg-surface-inset disabled:cursor-not-allowed disabled:opacity-50 sm:px-3 sm:text-sm"
              >
                <span className="sm:hidden">Next</span>
                <span className="hidden sm:inline">Find Next</span>
              </button>
              <button
                onClick={onReplaceCurrent}
                disabled={matches.length === 0 || isSubmitting}
                className="whitespace-nowrap rounded-md border border-surface-border bg-surface-inset px-2.5 py-1.5 text-xs text-contrast-muted shadow-card hover:bg-surface-inset disabled:cursor-not-allowed disabled:opacity-50 sm:px-3 sm:text-sm"
              >
                Replace
              </button>
              <button
                onClick={onReplaceAll}
                disabled={!findText || isSubmitting}
                className="whitespace-nowrap rounded-md bg-action px-2.5 py-1.5 text-xs text-action-on shadow-card hover:bg-action-hover disabled:cursor-not-allowed disabled:opacity-50 sm:px-3 sm:text-sm"
              >
                <span className="sm:hidden">All</span>
                <span className="hidden sm:inline">Replace All</span>
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
