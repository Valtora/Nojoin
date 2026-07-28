"use client";

import { useEffect, useState, type ReactNode } from "react";

import SettingsAdvanced from "./SettingsAdvanced";
import { useSettingsContext } from "./SettingsProvider";
import type { SettingsCategoryId } from "./settingsCategories";
import { countChangedAdvanced, partitionByAdvanced } from "./settingsRegistry";

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
  const { settings, isAdmin } = useSettingsContext();
  const [forceOpen, setForceOpen] = useState(false);

  const { advanced } = partitionByAdvanced(categoryId, { isAdmin });

  useEffect(() => {
    const hash = window.location.hash.slice(1);
    if (hash && advanced.some((entry) => entry.id === hash)) {
      setForceOpen(true);
    }
  }, [advanced]);

  if (advanced.length === 0) {
    return null;
  }

  return (
    <SettingsAdvanced
      changedCount={countChangedAdvanced(categoryId, settings, { isAdmin })}
      forceOpen={forceOpen}
    >
      {children}
    </SettingsAdvanced>
  );
}
