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
  return (
    <nav className="hide-scrollbar flex overflow-x-auto p-2 md:flex-col md:space-y-1 md:overflow-y-auto md:p-4">
      {items.map((item) => {
        const Icon = item.icon;
        const isActive = activeItemId === item.id;
        const matchScore = matchScores?.[item.id];
        const hasMatch = typeof matchScore === "number" && matchScore < 0.6;

        return (
          <button
            key={item.id}
            type="button"
            onClick={() => onSelect(item.id)}
            className={cn(
              "flex shrink-0 items-center justify-between rounded-lg border px-3 py-2 text-sm font-medium whitespace-nowrap transition-colors md:mb-0",
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
  );
}