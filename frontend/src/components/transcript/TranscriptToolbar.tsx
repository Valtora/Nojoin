"use client";

import { InputBase } from '@/components/ui/nativeBases';
import { useEffect, useLayoutEffect, useRef } from "react";
import {
  ArrowRightLeft,
  ChevronDown,
  ChevronUp,
  Download,
  Redo2,
  Search,
  Undo2,
} from "lucide-react";

import Button from "../ui/Button";
import IconButton from "../ui/IconButton";
import { TOUCH_TARGET_HEIGHT, TOUCH_TARGET_SQUARE } from "../ui/touchTarget";

import type { TranscriptSearch } from "./_hooks/useTranscriptSearch";
import SearchSettingsMenu from "./SearchSettingsMenu";

/** A toggle that is on keeps the action tint, hovered or not. */
const PRESSED = "bg-action-tint text-action-text hover:bg-action-tint hover:text-action-text";

/** The find and replace fields, with the Input primitive's focus ring. */
const FIELD =
  "w-full min-w-0 py-1.5 pl-8 text-sm rounded-md border border-control-border bg-surface-inset focus-visible:outline-2 focus-visible:outline-offset-0 focus-visible:outline-focus-ring";

/** Moves focus to `field` when `target` is a button that has been disabled. */
const focusIfDisabled = (target: EventTarget | null, field: HTMLInputElement | null) => {
  if (target instanceof HTMLButtonElement && target.disabled) field?.focus();
};

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
  const {
    findText,
    setFindText,
    replaceText,
    setReplaceText,
    matches,
    currentMatchIndex,
    nextMatch,
    prevMatch,
  } = search;
  const hasMatches = matches.length > 0;
  const searchRowRef = useRef<HTMLDivElement>(null);
  const findRef = useRef<HTMLInputElement>(null);
  const findReplaceToggleRef = useRef<HTMLButtonElement>(null);
  const replaceWasOpenRef = useRef(showReplace);
  // Whether focus is in the find/replace row, as its focus events last said.
  const rowHasFocusRef = useRef(false);

  // When Find & Replace closes while it holds focus, as Replace All does when
  // it finishes, focus lands on the page. Send it to the toggle instead. If
  // the user had moved on, by clicking the page say, focus stays put.
  useLayoutEffect(() => {
    const closed = replaceWasOpenRef.current && !showReplace;
    replaceWasOpenRef.current = showReplace;
    if (!closed) return;
    const rowHadFocus = rowHasFocusRef.current;
    rowHasFocusRef.current = false;
    if (rowHadFocus && document.activeElement === document.body) {
      findReplaceToggleRef.current?.focus();
    }
  }, [showReplace]);

  // A button that is disabled while it has focus would leave focus on the
  // page: the arrows, Find Next and Replace once no match is left, Replace
  // and Replace All while they save. Focus goes to the find field instead.
  // Chrome blurs the button as it is disabled, during React's commit, when
  // React's own onBlur is switched off; a native listener still hears it.
  const rowShown = showSearch || showReplace;
  useEffect(() => {
    const row = searchRowRef.current;
    if (!row) return;
    const onFocusIn = () => {
      rowHasFocusRef.current = true;
    };
    const onFocusOut = (event: FocusEvent) => {
      const next = event.relatedTarget;
      if (next instanceof Node) {
        rowHasFocusRef.current = row.contains(next);
        return;
      }
      focusIfDisabled(event.target, findRef.current);
      // Focus went to the page, or the row is being removed: Chrome blurs a
      // focused element just before removing it. Settle it after the render,
      // when a removed row has already handed focus to the toggle.
      queueMicrotask(() => {
        if (row.isConnected) rowHasFocusRef.current = row.contains(document.activeElement);
      });
    };
    row.addEventListener("focusin", onFocusIn);
    row.addEventListener("focusout", onFocusOut);
    return () => {
      row.removeEventListener("focusin", onFocusIn);
      row.removeEventListener("focusout", onFocusOut);
    };
  }, [rowShown]);
  // A browser that keeps focus on the disabled button is caught after the
  // render, once the matches have run out.
  useLayoutEffect(() => {
    if (hasMatches) return;
    const active = document.activeElement;
    if (searchRowRef.current?.contains(active)) focusIfDisabled(active, findRef.current);
  });

  const exportTitle = exportDisabled
    ? "Finish the current transcript edit before exporting"
    : "Export";

  return (
    <div data-ui="surface" data-ui-surface="pane" className="z-10 flex flex-col border-b-2 border-surface-border bg-surface-inset shadow-card">
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
            className={TOUCH_TARGET_SQUARE}
            icon={<Undo2 aria-hidden="true" />}
          />
          <IconButton
            size="sm"
            aria-label="Redo"
            title="Redo"
            onClick={onRedo}
            disabled={!canRedo}
            className={TOUCH_TARGET_SQUARE}
            icon={<Redo2 aria-hidden="true" />}
          />
          <div className="mx-0.5 h-4 w-px bg-surface-card sm:mx-1" />
          <IconButton
            size="sm"
            aria-label={exportDisabled ? "Export transcript disabled" : "Export transcript"}
            title={exportTitle}
            onClick={onExport}
            disabled={exportDisabled}
            className={TOUCH_TARGET_SQUARE}
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
            className={`${TOUCH_TARGET_SQUARE} ${showSearch ? PRESSED : ""}`}
            icon={<Search aria-hidden="true" />}
          />
          <IconButton
            ref={findReplaceToggleRef}
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
            className={`${TOUCH_TARGET_SQUARE} ${showReplace ? PRESSED : ""}`}
            icon={<ArrowRightLeft aria-hidden="true" />}
          />
        </div>
      </div>

      {/* Row 2: Search & Replace Controls */}
      {(showSearch || showReplace) && (
        <div
          ref={searchRowRef}
          className="animate-in slide-in-from-top-2 flex flex-wrap items-center gap-1.5 border-t border-control-border px-2 pb-2 pt-2 duration-200 sm:gap-2 sm:px-4 sm:pb-3 sm:pt-3 md:px-5"
        >
          {/* 16rem keeps room for the query beside the arrows: on a phone
              the replace field wraps under it rather than squeezing it. */}
          <div className="flex min-w-64 flex-[1_1_11rem] items-center gap-0.5 sm:flex-[1_1_14rem]">
            <div className="relative min-w-0 flex-1">
              <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-4 h-4 text-contrast-icon-muted" />
              <InputBase
                ref={findRef}
                placeholder="Find..."
                value={findText}
                onChange={(e) => setFindText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    if (e.shiftKey) prevMatch();
                    else nextMatch();
                  }
                }}
                className={`${FIELD} pr-20`}
                autoFocus
              />
              {/* Always mounted, so a screen reader hears the count change. */}
              <span
                aria-live="polite"
                aria-atomic="true"
                className={
                  hasMatches
                    ? "absolute right-1 top-1/2 -translate-y-1/2 text-xs text-contrast-helper whitespace-nowrap bg-surface-card rounded px-1"
                    : "sr-only"
                }
              >
                {hasMatches ? `${currentMatchIndex + 1} of ${matches.length}` : ""}
              </span>
            </div>
            {/* Beside the field rather than inside it, so each is a full
                target. They stay while there are no matches, so the row
                doesn't shift as the matches come and go. */}
            <IconButton
              size="sm"
              aria-label="Previous match"
              title="Previous match (Shift+Enter)"
              onClick={prevMatch}
              disabled={!hasMatches}
              className={TOUCH_TARGET_SQUARE}
              icon={<ChevronUp aria-hidden="true" />}
            />
            <IconButton
              size="sm"
              aria-label="Next match"
              title="Next match (Enter)"
              onClick={nextMatch}
              disabled={!hasMatches}
              className={TOUCH_TARGET_SQUARE}
              icon={<ChevronDown aria-hidden="true" />}
            />
          </div>
          {showReplace && (
            <div className="relative min-w-40 flex-[1_1_11rem] sm:min-w-48 sm:flex-[1_1_14rem]">
              <ArrowRightLeft className="absolute left-2 top-1/2 -translate-y-1/2 w-4 h-4 text-contrast-icon-muted" />
              <InputBase
                placeholder="Replace..."
                value={replaceText}
                onChange={(e) => setReplaceText(e.target.value)}
                className={`${FIELD} pr-2`}
              />
            </div>
          )}
          {showReplace && (
            <div className="flex min-w-0 flex-[1_0_auto] flex-wrap items-center justify-end gap-1.5 sm:gap-2">
              <SearchSettingsMenu
                caseSensitive={search.caseSensitive}
                setCaseSensitive={search.setCaseSensitive}
                isFuzzy={search.isFuzzy}
                setIsFuzzy={search.setIsFuzzy}
                useRegex={search.useRegex}
                setUseRegex={search.setUseRegex}
                pressedClassName={PRESSED}
              />

              <div className="mx-0.5 h-4 w-px bg-surface-card sm:mx-1" />

              <Button
                size="sm"
                onClick={nextMatch}
                disabled={!hasMatches}
                className={`sm:text-sm ${TOUCH_TARGET_HEIGHT}`}
              >
                <span className="sm:hidden">Next</span>
                <span className="hidden sm:inline">Find Next</span>
              </Button>
              <Button
                size="sm"
                onClick={onReplaceCurrent}
                disabled={!hasMatches || isSubmitting}
                className={`sm:text-sm ${TOUCH_TARGET_HEIGHT}`}
              >
                Replace
              </Button>
              <Button
                size="sm"
                variant="primary"
                onClick={onReplaceAll}
                disabled={!findText || isSubmitting}
                className={`sm:text-sm ${TOUCH_TARGET_HEIGHT}`}
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
