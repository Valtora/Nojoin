"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/cn";

import {
  settingsCategoryHref,
  type SettingsCategoryId,
  type SettingsNavGroup,
} from "./settingsCategories";

interface SettingsNavProps {
  groups: SettingsNavGroup[];
  /** Categories a live search matches, shown with a dot. */
  matching?: Set<SettingsCategoryId>;
  /** Called after a category is chosen, so the mobile drawer can close. */
  onNavigate?: () => void;
  className?: string;
}

/**
 * The settings sidebar: one scrolling column, grouped under quiet headers.
 *
 * Grouping is what keeps fourteen entries scannable — four groups of three or
 * four read faster than a flat list of fourteen. Categories are real links, so
 * the browser's back button, middle-click and bookmarking all behave normally.
 */
export default function SettingsNav({
  groups,
  matching,
  onNavigate,
  className,
}: SettingsNavProps) {
  const pathname = usePathname();

  return (
    <nav className={cn("space-y-6 p-4", className)} aria-label="Settings">
      {groups.map((group) => (
        <div key={group.id}>
          <h2 className="px-3 pb-2 text-[11px] font-semibold uppercase tracking-[0.16em] contrast-icon-muted">
            {group.label}
          </h2>

          <ul className="space-y-0.5">
            {group.items.map((category) => {
              const href = settingsCategoryHref(category.id);
              const isActive = pathname === href;
              const Icon = category.icon;

              return (
                <li key={category.id}>
                  <Link
                    href={href}
                    onClick={onNavigate}
                    aria-current={isActive ? "page" : undefined}
                    className={cn(
                      "flex items-center justify-between gap-3 rounded-lg border px-3 py-2 text-sm font-medium transition-colors",
                      isActive
                        ? "settings-tab-active"
                        : "border-transparent settings-tab-inactive",
                    )}
                  >
                    <span className="flex min-w-0 items-center gap-2.5">
                      <Icon
                        className={cn(
                          "h-4 w-4 shrink-0",
                          isActive
                            ? "text-orange-800 dark:text-orange-200"
                            : "contrast-icon-muted",
                        )}
                        aria-hidden="true"
                      />
                      <span className="truncate">{category.label}</span>
                    </span>

                    {matching?.has(category.id) && (
                      <span
                        className="h-1.5 w-1.5 shrink-0 rounded-full bg-orange-500"
                        aria-label="Matches your search"
                      />
                    )}
                  </Link>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </nav>
  );
}
