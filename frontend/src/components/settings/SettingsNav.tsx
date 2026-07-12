"use client";

import { useEffect, useRef } from "react";
import { cn } from "@/lib/cn";

import type {
  SettingsSectionId,
  SettingsSectionMetadata,
} from "./settingsMetadata";

interface SettingsNavProps {
  items: readonly SettingsSectionMetadata[];
  activeItemId: SettingsSectionId;
  onSelect: (id: SettingsSectionId) => void;
  matchScores?: Partial<Record<SettingsSectionId, number>>;
}

export default function SettingsNav({
  items,
  activeItemId,
  onSelect,
  matchScores,
}: SettingsNavProps) {
  const activeRef = useRef<HTMLButtonElement | null>(null);

  // Keep the selected tab visible in the horizontal mobile strip so a tab that
  // sits past the right edge (e.g. Admin) is never left clipped and unreachable.
  useEffect(() => {
    activeRef.current?.scrollIntoView({
      inline: "nearest",
      block: "nearest",
    });
  }, [activeItemId]);

  return (
    // The right-edge fade (mobile only) signals the strip scrolls horizontally.
    <div className="relative lg:contents">
      <nav className="hide-scrollbar flex gap-2 overflow-x-auto p-2 pr-6 lg:flex-col lg:gap-0 lg:space-y-1 lg:overflow-y-auto lg:p-4 lg:pr-4">
        {items.map((item) => {
          const Icon = item.icon;
          const isActive = activeItemId === item.id;
          const matchScore = matchScores?.[item.id];
          const hasMatch = typeof matchScore === "number" && matchScore < 0.6;

          return (
            <button
              key={item.id}
              ref={isActive ? activeRef : undefined}
              type="button"
              onClick={() => onSelect(item.id)}
              className={cn(
                "flex shrink-0 items-center justify-between rounded-lg border px-3 py-2 text-sm font-medium whitespace-nowrap transition-colors lg:mb-0",
                isActive
                  ? "settings-tab-active shadow-sm"
                  : "border-transparent settings-tab-inactive",
              )}
              title={item.description}
            >
              <span className="flex items-center gap-3">
                <Icon
                  className={cn(
                    "h-4 w-4",
                    isActive ? "text-orange-800 dark:text-orange-200" : "contrast-icon-muted",
                  )}
                />
                {item.label}
              </span>
              {hasMatch && <span className="h-2 w-2 rounded-full bg-orange-500" />}
            </button>
          );
        })}
      </nav>
      <div className="pointer-events-none absolute inset-y-0 right-0 w-6 bg-gradient-to-l from-[var(--background)] to-transparent lg:hidden" />
    </div>
  );
}
