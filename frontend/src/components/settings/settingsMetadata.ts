import {
  Bot,
  ArrowUpCircle,
  Link2,
  PlayCircle,
  Shield,
  User,
  type LucideIcon,
} from "lucide-react";

import { TAB_KEYWORDS } from "./keywords";

export const SETTINGS_SECTION_IDS = [
  "personal",
  "ai",
  "companion",
  "administration",
  "updates",
  "help",
] as const;

export type SettingsSectionId = (typeof SETTINGS_SECTION_IDS)[number];

export type SettingsSectionRoleRequirement = "all" | "admin";

export interface SettingsSectionMetadata {
  id: SettingsSectionId;
  label: string;
  description: string;
  icon: LucideIcon;
  keywords: string[];
  roleRequirement: SettingsSectionRoleRequirement;
  visibleWhenForcePasswordChange?: boolean;
}

export const SETTINGS_SECTION_METADATA: Record<
  SettingsSectionId,
  SettingsSectionMetadata
> = {
  personal: {
    id: "personal",
    label: "Personal",
    description: "Profile, passwords, appearance, timezone, spellcheck, calendars, and recording preferences.",
    icon: User,
    keywords: TAB_KEYWORDS.personal,
    roleRequirement: "all",
    visibleWhenForcePasswordChange: true,
  },
  ai: {
    id: "ai",
    label: "AI",
    description: "Provider preferences, automatic enhancement behavior, and model configuration.",
    icon: Bot,
    keywords: TAB_KEYWORDS.ai,
    roleRequirement: "all",
  },
  companion: {
    id: "companion",
    label: "Companion",
    description: "Connection and pairing, devices and alerts, and local capture preferences.",
    icon: Link2,
    keywords: TAB_KEYWORDS.companion,
    roleRequirement: "all",
  },
  administration: {
    id: "administration",
    label: "Administration",
    description: "Users, invitations, calendar provider setup, system operations, and backups.",
    icon: Shield,
    keywords: TAB_KEYWORDS.administration,
    roleRequirement: "admin",
  },
  updates: {
    id: "updates",
    label: "Updates",
    description: "Installed version details, release history, and Companion installers.",
    icon: ArrowUpCircle,
    keywords: TAB_KEYWORDS.updates,
    roleRequirement: "all",
  },
  help: {
    id: "help",
    label: "Help",
    description: "Tours, demo content, and issue reporting.",
    icon: PlayCircle,
    keywords: ["help", "tour", "demo", "tutorial"],
    roleRequirement: "all",
  },
};

export function isSettingsSectionId(value: string): value is SettingsSectionId {
  return SETTINGS_SECTION_IDS.includes(value as SettingsSectionId);
}

export function getVisibleSettingsSections({
  isAdmin,
  forcePasswordChange,
}: {
  isAdmin: boolean;
  forcePasswordChange: boolean;
}): SettingsSectionMetadata[] {
  if (forcePasswordChange) {
    return [SETTINGS_SECTION_METADATA.personal];
  }

  return SETTINGS_SECTION_IDS.map((id) => SETTINGS_SECTION_METADATA[id]).filter(
    (section) => section.roleRequirement === "all" || isAdmin,
  );
}

export function resolveLegacySettingsSectionId(
  value: string | null,
): SettingsSectionId | null {
  if (!value) {
    return null;
  }

  if (value === "audio") {
    return "companion";
  }

  if (value === "general" || value === "account" || value === "personal") {
    return "personal";
  }

  if (value === "admin" || value === "administration") {
    return "administration";
  }

  if (value === "ai") {
    return "ai";
  }

  return isSettingsSectionId(value) ? value : null;
}