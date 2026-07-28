import {
  Archive,
  ArrowUpCircle,
  Bot,
  Captions,
  Mic,
  NotebookPen,
  Palette,
  PlayCircle,
  Plug,
  Server,
  ShieldCheck,
  Sparkles,
  User,
  Users,
  type LucideIcon,
} from "lucide-react";

/**
 * Settings information architecture.
 *
 * Categories are organised by domain rather than by role: an admin-only category
 * lives in the group its subject belongs to, and is simply not rendered for
 * users who cannot see any of its content. Access is declared per category here
 * and per setting in the registry, so a mixed category (Integrations,
 * Transcription) shows every user only the parts that are theirs.
 */

export const SETTINGS_GROUP_IDS = [
  "general",
  "meetings",
  "data",
  "about",
] as const;

export type SettingsGroupId = (typeof SETTINGS_GROUP_IDS)[number];

export const SETTINGS_GROUP_LABELS: Record<SettingsGroupId, string> = {
  general: "General",
  meetings: "Meetings",
  data: "Data",
  about: "About",
};

export const SETTINGS_CATEGORY_IDS = [
  "profile",
  "users",
  "appearance",
  "integrations",
  "recording",
  "transcription",
  "notes",
  "your-ai",
  "ai-providers",
  "backup",
  "privacy",
  "system",
  "updates",
  "help",
] as const;

export type SettingsCategoryId = (typeof SETTINGS_CATEGORY_IDS)[number];

/** Who a category (or a single setting) is for. */
export type SettingsAccess = "all" | "admin";

export interface SettingsCategoryMetadata {
  id: SettingsCategoryId;
  group: SettingsGroupId;
  label: string;
  /** Rendered under the category heading on its own page. */
  description: string;
  icon: LucideIcon;
  /**
   * "admin" hides the category from non-admins entirely. Categories holding a
   * mix declare "all" and rely on per-entry access in the registry.
   */
  access: SettingsAccess;
  /**
   * Data-heavy pages opt out of the reading column and fill the available
   * width. Declared once per page so a page is either a form or a data page,
   * never a ragged mix of both.
   */
  fullBleed?: boolean;
  /** Shown during a forced password change, when everything else is locked. */
  visibleWhenForcePasswordChange?: boolean;
}

export const SETTINGS_CATEGORIES: Record<
  SettingsCategoryId,
  SettingsCategoryMetadata
> = {
  profile: {
    id: "profile",
    group: "general",
    label: "Profile and security",
    description: "Your username and the password used to sign in to this account.",
    icon: User,
    access: "all",
    visibleWhenForcePasswordChange: true,
  },
  users: {
    id: "users",
    group: "general",
    label: "Users and access",
    description:
      "Accounts on this installation, their roles, and the invitations used to add new ones.",
    icon: Users,
    access: "admin",
    fullBleed: true,
  },
  appearance: {
    id: "appearance",
    group: "general",
    label: "Appearance",
    description:
      "How Nojoin looks in your browser, the timezone it displays, and the dictionary it checks your writing against.",
    icon: Palette,
    access: "all",
  },
  integrations: {
    id: "integrations",
    group: "general",
    label: "Integrations",
    description:
      "Calendars, connected AI assistants, and the credentials that let this installation talk to them.",
    icon: Plug,
    access: "all",
  },
  recording: {
    id: "recording",
    group: "meetings",
    label: "Recording",
    description: "How Nojoin captures and processes meeting audio.",
    icon: Mic,
    access: "all",
  },
  transcription: {
    id: "transcription",
    group: "meetings",
    label: "Transcription",
    description: "How recorded audio is turned into text, and in which language.",
    icon: Captions,
    access: "all",
  },
  notes: {
    id: "notes",
    group: "meetings",
    label: "Notes and live assistance",
    description:
      "What the AI produces during a meeting and what it writes up afterwards.",
    icon: NotebookPen,
    access: "all",
  },
  "your-ai": {
    id: "your-ai",
    group: "meetings",
    label: "Your AI",
    description:
      "Whether your meetings run on this installation's AI or on your own Claude or ChatGPT subscription.",
    icon: Sparkles,
    access: "all",
  },
  "ai-providers": {
    id: "ai-providers",
    group: "meetings",
    label: "AI providers",
    description:
      "The AI provider, models, and credentials this installation uses for everyone who has not connected their own.",
    icon: Bot,
    access: "admin",
  },
  backup: {
    id: "backup",
    group: "data",
    label: "Backup and restore",
    description:
      "Export application data, and recover it with explicit, transactional restores.",
    icon: Archive,
    access: "admin",
  },
  privacy: {
    id: "privacy",
    group: "data",
    label: "Privacy",
    description: "What this installation reports back about itself, if anything.",
    icon: ShieldCheck,
    access: "admin",
  },
  system: {
    id: "system",
    group: "data",
    label: "System and logs",
    description: "Live service output and operational configuration.",
    icon: Server,
    access: "admin",
    fullBleed: true,
  },
  updates: {
    id: "updates",
    group: "about",
    label: "Updates",
    description:
      "The version running here, what has been published since, and how to upgrade.",
    icon: ArrowUpCircle,
    access: "all",
    fullBleed: true,
  },
  help: {
    id: "help",
    group: "about",
    label: "Help",
    description: "Guided tours, demo content, and how to report a problem.",
    icon: PlayCircle,
    access: "all",
  },
};

export const SETTINGS_ROOT = "/settings";

export function settingsCategoryHref(id: SettingsCategoryId): string {
  return `${SETTINGS_ROOT}/${id}`;
}

export function isSettingsCategoryId(
  value: string,
): value is SettingsCategoryId {
  return SETTINGS_CATEGORY_IDS.includes(value as SettingsCategoryId);
}

export interface SettingsNavGroup {
  id: SettingsGroupId;
  label: string;
  items: SettingsCategoryMetadata[];
}

export function getVisibleSettingsCategories({
  isAdmin,
  forcePasswordChange = false,
}: {
  isAdmin: boolean;
  forcePasswordChange?: boolean;
}): SettingsCategoryMetadata[] {
  if (forcePasswordChange) {
    return SETTINGS_CATEGORY_IDS.map((id) => SETTINGS_CATEGORIES[id]).filter(
      (category) => category.visibleWhenForcePasswordChange,
    );
  }

  return SETTINGS_CATEGORY_IDS.map((id) => SETTINGS_CATEGORIES[id]).filter(
    (category) => category.access === "all" || isAdmin,
  );
}

/** The sidebar shape: categories bucketed into their groups, empty groups dropped. */
export function getSettingsNavGroups(options: {
  isAdmin: boolean;
  forcePasswordChange?: boolean;
}): SettingsNavGroup[] {
  const visible = getVisibleSettingsCategories(options);

  return SETTINGS_GROUP_IDS.map((groupId) => ({
    id: groupId,
    label: SETTINGS_GROUP_LABELS[groupId],
    items: visible.filter((category) => category.group === groupId),
  })).filter((group) => group.items.length > 0);
}

/**
 * Pre-redesign `?tab=` values, plus the older aliases that already redirected to
 * them. Kept so bookmarks, docs, and in-app deep links survive the move to real
 * routes.
 */
const LEGACY_TAB_ALIASES: Record<string, SettingsCategoryId> = {
  account: "profile",
  general: "profile",
  personal: "profile",
  admin: "users",
  administration: "users",
  audio: "recording",
  capture: "recording",
  companion: "recording",
  ai: "your-ai",
};

export function resolveLegacySettingsTab(
  value: string | null | undefined,
): SettingsCategoryId | null {
  if (!value) {
    return null;
  }

  if (isSettingsCategoryId(value)) {
    return value;
  }

  return LEGACY_TAB_ALIASES[value] ?? null;
}

export const LEGACY_SETTINGS_TAB_VALUES = Object.keys(LEGACY_TAB_ALIASES);
