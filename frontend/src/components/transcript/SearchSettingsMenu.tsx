"use client";

import { InputBase } from '@/components/ui/nativeBases';
import { SurfaceBase } from '@/components/ui/SurfaceBase';
import { useEffect, useId, useRef, useState } from "react";
import { Settings } from "lucide-react";

import IconButton from "../ui/IconButton";
import { TOUCH_TARGET_SQUARE } from "../ui/touchTarget";

import type { TranscriptSearch } from "./_hooks/useTranscriptSearch";

type SearchOptions = Pick<
  TranscriptSearch,
  "caseSensitive" | "setCaseSensitive" | "isFuzzy" | "setIsFuzzy" | "useRegex" | "setUseRegex"
>;

interface SearchSettingsMenuProps extends SearchOptions {
  /** The tint a toolbar toggle keeps while it is on. */
  pressedClassName: string;
}

const CHECKBOX =
  "h-4 w-4 rounded border-control-border text-action-text focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring";

/**
 * Find & Replace's Search Options: a toggle and the menu it opens. It lives
 * only while Find & Replace is open, so the menu starts closed each time.
 */
export default function SearchSettingsMenu({
  caseSensitive,
  setCaseSensitive,
  isFuzzy,
  setIsFuzzy,
  useRegex,
  setUseRegex,
  pressedClassName,
}: SearchSettingsMenuProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const panelId = useId();

  // Escape closes the menu. Focus inside it goes back to the toggle, so it
  // isn't lost with the menu. An Escape that ends an IME composition, or that
  // something else already handled, is left alone.
  useEffect(() => {
    if (!open) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.isComposing || event.defaultPrevented) return;
      if (rootRef.current?.contains(document.activeElement)) {
        toggleRef.current?.focus();
      }
      setOpen(false);
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [open]);

  return (
    <div ref={rootRef} className="relative">
      <IconButton
        data-ui-fill={open ? "action-tint" : undefined}
        data-ui-hover={open ? "action-tint" : "inset"}
        ref={toggleRef}
        size="sm"
        aria-label="Advanced Search Settings"
        title="Advanced Search Settings"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => setOpen(!open)}
        className={`${TOUCH_TARGET_SQUARE} ${open ? pressedClassName : ""}`}
        icon={<Settings aria-hidden="true" />}
      />

      {open && (
        <>
          <div
            className="fixed inset-0 z-[var(--z-dropdown)]"
            onClick={() => setOpen(false)}
          />
          <SurfaceBase
            data-ui-fill="card"
            data-ui-border="full" kind="float"
            id={panelId}
            className="absolute right-0 top-full mt-2 w-48 bg-surface-card rounded-lg shadow-float border border-surface-border p-2 z-[var(--z-dropdown)] flex flex-col gap-1"
          >
            <div className="text-xs font-semibold text-contrast-icon-muted px-2 py-1 mb-1 border-b border-surface-border">
              Search Options
            </div>
            <label className="flex items-center gap-2 px-2 py-1.5 hover:bg-surface-inset rounded cursor-pointer">
              <InputBase
                type="checkbox"
                checked={caseSensitive}
                onChange={(e) => setCaseSensitive(e.target.checked)}
                className={CHECKBOX}
              />
              <span className="text-sm text-contrast-muted">Case Sensitive</span>
            </label>
            <label className="flex items-center gap-2 px-2 py-1.5 hover:bg-surface-inset rounded cursor-pointer">
              <InputBase
                type="checkbox"
                checked={isFuzzy}
                onChange={(e) => {
                  setIsFuzzy(e.target.checked);
                  if (e.target.checked) setUseRegex(false);
                }}
                className={CHECKBOX}
              />
              <span className="text-sm text-contrast-muted">Fuzzy Match</span>
            </label>
            <label className="flex items-center gap-2 px-2 py-1.5 hover:bg-surface-inset rounded cursor-pointer">
              <InputBase
                type="checkbox"
                checked={useRegex}
                onChange={(e) => {
                  setUseRegex(e.target.checked);
                  if (e.target.checked) setIsFuzzy(false);
                }}
                className={CHECKBOX}
              />
              <span className="text-sm text-contrast-muted">Regex</span>
            </label>
          </SurfaceBase>
        </>
      )}
    </div>
  );
}
