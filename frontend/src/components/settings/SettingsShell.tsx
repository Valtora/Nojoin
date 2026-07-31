"use client";

import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { Loader2 } from "lucide-react";

import {
  SETTINGS_ROOT,
  getSettingsNavGroups,
  settingsCategoryHref,
  type SettingsCategoryId,
} from "./settingsCategories";
import { getMatchingSettingsCategories } from "./settingsRegistry";
import SettingsNav from "./SettingsNav";
import SettingsSearch from "./SettingsSearch";
import { SettingsAutosaveFooter, useSettingsContext } from "./SettingsProvider";
import VersionTag from "./VersionTag";

/**
 * The settings frame: header, sidebar, and the column category pages render
 * into.
 *
 * Below `lg` the sidebar is not rendered at all. Mobile navigates by drill-in
 * instead — `/settings` lists the categories and each opens as its own page —
 * because fourteen entries across four groups cannot be expressed as the
 * horizontal chip strip the six-tab version used.
 */
export default function SettingsShell({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const { isAdmin, loading, forcePasswordChange } = useSettingsContext();
  const [searchQuery, setSearchQuery] = useState("");

  const groups = useMemo(
    () => getSettingsNavGroups({ isAdmin, forcePasswordChange }),
    [forcePasswordChange, isAdmin],
  );

  const matching = useMemo<Set<SettingsCategoryId>>(
    () =>
      searchQuery
        ? getMatchingSettingsCategories(searchQuery, { isAdmin })
        : new Set<SettingsCategoryId>(),
    [isAdmin, searchQuery],
  );

  // A forced password change locks the whole area to Profile. Enforced here
  // rather than per page so no route, and no stale bookmark, can slip past it.
  useEffect(() => {
    if (loading || !forcePasswordChange) {
      return;
    }

    const profile = settingsCategoryHref("profile");
    if (pathname !== profile) {
      router.replace(profile);
    }
  }, [forcePasswordChange, loading, pathname, router]);

  const handleQueryChange = useCallback((query: string) => {
    setSearchQuery(query);
  }, []);

  const isIndex = pathname === SETTINGS_ROOT;

  return (
    <div className="settings-page flex h-full flex-col">
      <header className="flex shrink-0 items-center justify-between gap-4 border-b px-4 py-3 contrast-border md:px-6">
        <h1 className="text-lg font-semibold text-foreground">
          Settings
        </h1>
        <VersionTag />
      </header>

      <div className="flex min-h-0 flex-1 lg:flex-row">
        <aside className="hidden w-64 shrink-0 flex-col border-r contrast-border lg:flex">
          {!forcePasswordChange && (
            <div className="border-b p-4 contrast-border">
              <SettingsSearch
                isAdmin={isAdmin}
                onQueryChange={handleQueryChange}
              />
            </div>
          )}

          <div className="min-h-0 flex-1 overflow-y-auto">
            <SettingsNav groups={groups} matching={matching} />
          </div>

          <div className="border-t p-4 contrast-border">
            <SettingsAutosaveFooter />
          </div>
        </aside>

        <div className="min-h-0 flex-1 overflow-y-auto">
          {loading ? (
            <div className="flex h-full items-center justify-center py-16 contrast-helper">
              <Loader2 className="mr-2 h-5 w-5 animate-spin" aria-hidden="true" />
              Loading settings...
            </div>
          ) : (
            <>
              {/* Mobile keeps the search and the save state visible on the
                  index, where the sidebar that normally holds them is absent. */}
              {isIndex && !forcePasswordChange && (
                <div className="space-y-3 border-b p-4 contrast-border lg:hidden">
                  <SettingsSearch
                    isAdmin={isAdmin}
                    onQueryChange={handleQueryChange}
                  />
                  <SettingsAutosaveFooter />
                </div>
              )}
              {children}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
