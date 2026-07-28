"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo } from "react";
import { ChevronRight } from "lucide-react";

import { useViewportDensity } from "@/components/ViewportDensityProvider";

import {
  getSettingsNavGroups,
  resolveLegacySettingsTab,
  settingsCategoryHref,
} from "./settingsCategories";
import { useSettingsContext } from "./SettingsProvider";

/**
 * The settings index.
 *
 * On mobile it is the navigation: a grouped list of categories that each open
 * as their own page, with a back link home. On desktop the sidebar already
 * shows every category, so an index page would be a screen that says nothing —
 * it redirects to the first one instead.
 *
 * It is also where pre-redesign `?tab=` links land, so bookmarks and the
 * in-app deep links written before the move to real routes still work.
 */
export default function SettingsIndex() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { isDesktop } = useViewportDensity();
  const { isAdmin, loading, forcePasswordChange } = useSettingsContext();

  const groups = useMemo(
    () => getSettingsNavGroups({ isAdmin, forcePasswordChange }),
    [forcePasswordChange, isAdmin],
  );

  const legacyTab = resolveLegacySettingsTab(searchParams.get("tab"));

  useEffect(() => {
    if (loading) {
      return;
    }

    if (legacyTab) {
      router.replace(settingsCategoryHref(legacyTab));
      return;
    }

    if (isDesktop) {
      const first = groups[0]?.items[0];
      if (first) {
        router.replace(settingsCategoryHref(first.id));
      }
    }
  }, [groups, isDesktop, legacyTab, loading, router]);

  // Desktop is mid-redirect and a legacy link resolves on any viewport, so
  // rendering the list would only flash it away again.
  if (isDesktop || legacyTab) {
    return null;
  }

  return (
    <div className="p-4 md:p-6">
      <div className="space-y-6">
        {groups.map((group) => (
          <div key={group.id}>
            <h2 className="px-1 pb-2 text-[11px] font-semibold uppercase tracking-[0.16em] contrast-icon-muted">
              {group.label}
            </h2>

            <ul className="settings-card settings-card-body overflow-hidden">
              {group.items.map((category) => {
                const Icon = category.icon;

                return (
                  <li key={category.id}>
                    <Link
                      href={settingsCategoryHref(category.id)}
                      className="flex items-center gap-3 px-4 py-3.5 transition-colors hover:bg-gray-50 dark:hover:bg-gray-900/60"
                    >
                      <Icon
                        className="h-5 w-5 shrink-0 contrast-icon-muted"
                        aria-hidden="true"
                      />
                      <span className="min-w-0 flex-1">
                        <span className="block text-sm font-medium text-gray-900 dark:text-white">
                          {category.label}
                        </span>
                        <span className="mt-0.5 block text-xs leading-5 contrast-helper">
                          {category.description}
                        </span>
                      </span>
                      <ChevronRight
                        className="h-4 w-4 shrink-0 contrast-icon-muted"
                        aria-hidden="true"
                      />
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </div>
    </div>
  );
}
