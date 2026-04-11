"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

interface SpellCheckContextMenuProps {
  x: number;
  y: number;
  suggestions: string[];
  onCorrect: (replacement: string) => void;
  onAddToDictionary: () => void;
  onIgnore: () => void;
  onClose: () => void;
}

export default function SpellCheckContextMenu({
  x,
  y,
  suggestions,
  onCorrect,
  onAddToDictionary,
  onIgnore,
  onClose,
}: SpellCheckContextMenuProps) {
  const menuRef = useRef<HTMLDivElement>(null);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const suggestionsTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        onClose();
      }
    };

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };

    document.addEventListener("mousedown", handleClickOutside);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
      document.removeEventListener("keydown", handleKeyDown);
      if (suggestionsTimeoutRef.current) clearTimeout(suggestionsTimeoutRef.current);
    };
  }, [onClose]);

  const menuWidth = 192;
  const finalX = x - menuWidth > 0 ? x - menuWidth : x;

  const style = {
    top: y,
    left: finalX,
  };

  const handleSuggestionsEnter = () => {
    if (suggestionsTimeoutRef.current) clearTimeout(suggestionsTimeoutRef.current);
    setShowSuggestions(true);
  };

  const handleSuggestionsLeave = () => {
    suggestionsTimeoutRef.current = setTimeout(() => {
      setShowSuggestions(false);
    }, 200);
  };

  const hasSuggestions = suggestions.length > 0;

  return createPortal(
    <div
      ref={menuRef}
      className="fixed z-999999 w-48 bg-white dark:bg-gray-800 rounded-lg shadow-xl border border-gray-200 dark:border-gray-700 py-1 overflow-visible"
      style={style}
    >
      <div
        className="relative"
        onMouseEnter={handleSuggestionsEnter}
        onMouseLeave={handleSuggestionsLeave}
      >
        <button
          onClick={() => setShowSuggestions(!showSuggestions)}
          disabled={!hasSuggestions}
          className={`w-full text-left px-4 py-2.5 text-sm transition-colors flex items-center justify-between border-b border-gray-100 dark:border-gray-700 ${
            hasSuggestions
              ? "hover:bg-gray-100 dark:hover:bg-gray-700 active:bg-gray-200 dark:active:bg-gray-600 text-gray-700 dark:text-gray-200"
              : "text-gray-400 dark:text-gray-500 cursor-not-allowed"
          }`}
        >
          Suggestions
          {hasSuggestions && (
            <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
            </svg>
          )}
        </button>

        {/* Suggestion submenu flyout */}
        {showSuggestions && hasSuggestions && (
          <div
            className="absolute left-full top-0 ml-1 w-44 bg-white dark:bg-gray-800 rounded-lg shadow-xl border border-gray-200 dark:border-gray-700 py-1 z-999999"
            onMouseEnter={handleSuggestionsEnter}
            onMouseLeave={handleSuggestionsLeave}
          >
            {suggestions.map((suggestion, index) => (
              <button
                key={index}
                onClick={() => {
                  onCorrect(suggestion);
                  onClose();
                }}
                className={`w-full text-left px-4 py-2 text-sm text-gray-700 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700 active:bg-gray-200 dark:active:bg-gray-600 transition-colors ${
                  index !== suggestions.length - 1
                    ? "border-b border-gray-100 dark:border-gray-700"
                    : ""
                }`}
              >
                {suggestion}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Add to Dictionary */}
      <button
        onClick={() => {
          onAddToDictionary();
          onClose();
        }}
        className="w-full text-left px-4 py-2.5 text-sm text-gray-700 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700 active:bg-gray-200 dark:active:bg-gray-600 transition-colors border-b border-gray-100 dark:border-gray-700"
      >
        Add to Dictionary
      </button>

      {/* Ignore */}
      <button
        onClick={() => {
          onIgnore();
          onClose();
        }}
        className="w-full text-left px-4 py-2.5 text-sm text-gray-700 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700 active:bg-gray-200 dark:active:bg-gray-600 transition-colors"
      >
        Ignore
      </button>
    </div>,
    document.body,
  );
}
