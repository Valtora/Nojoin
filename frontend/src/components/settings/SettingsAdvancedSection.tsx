"use client";

import { useEffect, useState, type ReactNode } from "react";

import SettingsAdvanced from "./SettingsAdvanced";
import { useSettingsContext } from "./SettingsProvider";
import type { SettingsCategoryId } from "./settingsCategories";
import {
  countAttentionAdvanced,
  countChangedAdvanced,
  partitionByAdvanced,
} from "./settingsRegistry";

interface SettingsAdvancedSectionProps {
  categoryId: SettingsCategoryId;
  children: ReactNode;
}

/**
 * The Advanced gate, wired to the registry.
 *
 * Two behaviours here keep the gate from hiding things for real:
 *
 *  - it opens itself when the URL fragment names a gated setting, so a search
 *    result can never point at something the page then refuses to show;
 *  - it counts how many gated settings differ from their shipped default, so a
 *    value someone changed months ago advertises itself rather than lurking.
 *
 * It renders nothing when the category has no gated content, or when gating
 * would leave the page with nothing visible.
 */
export default function SettingsAdvancedSection({
  categoryId,
  children,
}: SettingsAdvancedSectionProps) {
  const { settings, viewer } = useSettingsContext();
  const [openRequest, setOpenRequest] = useState(0);

  const { advanced } = partitionByAdvanced(categoryId, viewer);
  // A string, because the partition is a fresh array on every render.
  const advancedIds = advanced.map((entry) => entry.id).join(" ");

  useEffect(() => {
    const ids = new Set(advancedIds.split(" ").filter(Boolean));
    const pointsInside = () => ids.has(window.location.hash.slice(1));

    if (pointsInside()) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- the hash exists only in the browser; read during render it would hydrate differently from the server
      setOpenRequest((request) => request + 1);
    }

    // An in-page link to a gated setting changes only the hash.
    const handleHashChange = () => {
      if (pointsInside()) {
        setOpenRequest((request) => request + 1);
      }
    };
    window.addEventListener("hashchange", handleHashChange);
    return () => window.removeEventListener("hashchange", handleHashChange);
  }, [advancedIds]);

  if (advanced.length === 0) {
    return null;
  }

  return (
    <SettingsAdvanced
      changedCount={countChangedAdvanced(categoryId, settings, viewer)}
      attentionCount={countAttentionAdvanced(categoryId, settings, viewer)}
      openRequest={openRequest}
    >
      {children}
    </SettingsAdvanced>
  );
}
