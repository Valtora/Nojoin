import { DEFAULT_MEETING_EDGE_CONTEXT_LEVEL } from "@/lib/meetingEdgeContext";
import { DEFAULT_CAPTURE_SETTINGS } from "@/lib/capture/shared";
import { getMatchScore } from "@/lib/searchUtils";

import type { SettingsAccess, SettingsCategoryId } from "./settingsCategories";

/**
 * The settings manifest.
 *
 * One entry per user-facing setting or panel. This is the single source of
 * truth for three things that used to be decided ad hoc in each component:
 *
 *  - search: entries are matched by label, description, and keywords, so a
 *    search reaches an individual setting rather than only its category;
 *  - the Advanced gate: an entry is gated if, and only if, it names a
 *    criterion below;
 *  - the "N changed" badge: an entry with a declared default can be compared
 *    against the live value.
 *
 * Entries that write a field on the `Settings` object list it in
 * `settingsKeys`. Panels and actions that own no persisted field (the users
 * table, a backup run, a guided tour) list none. `settingsRegistry.test.ts`
 * asserts that every field declared on the `Settings` interface is covered by
 * exactly one entry, or is named in UNSURFACED_SETTINGS_KEYS below.
 */

/**
 * Why a setting sits behind the Advanced gate. Every gated entry names one, so
 * the gate stays a rule rather than a series of individual judgements.
 */
export type AdvancedCriterion =
  /** Ships with a default that suits almost everyone; touched only when something is wrong. */
  | "safe-default"
  /** Setting it wrong silently degrades transcripts or notes rather than raising an error. */
  | "can-degrade"
  /** Unusable without something obtained outside Nojoin: a token, a URL, OAuth credentials. */
  | "external-credentials"
  /** Irrelevant on a single-user installation. */
  | "scale-only";

export const ADVANCED_CRITERION_LABELS: Record<AdvancedCriterion, string> = {
  "safe-default": "Safe default, rarely changed",
  "can-degrade": "Can degrade output if set wrong",
  "external-credentials": "Needs external credentials or knowledge",
  "scale-only": "Only matters on a multi-user installation",
};

export interface SettingsRegistryEntry {
  /** Stable id, also used as the in-page anchor a search result scrolls to. */
  id: string;
  label: string;
  description?: string;
  category: SettingsCategoryId;
  /** "admin" hides the entry from non-admins, independently of the Advanced gate. */
  access: SettingsAccess;
  /**
   * Present means gated. Absent means always visible. A setting that is the
   * primary purpose of its page is left ungated even where a criterion
   * matches, because hiding the reason someone opened a page is how features
   * stop being used.
   */
  advanced?: AdvancedCriterion;
  /** Fields on the `Settings` object this entry owns. */
  settingsKeys?: string[];
  /**
   * The shipped default, where it is known and comparable. Only entries that
   * declare one contribute to the "N changed" count, so an unknown default is
   * silently ignored rather than counted wrongly.
   */
  defaultValue?: unknown;
  keywords: string[];
}

/**
 * Fields on the `Settings` interface with no control anywhere in the UI. Listed
 * explicitly so the parity test still passes while making the omission visible:
 * a key arriving here should be a decision, not an oversight.
 *
 *  - theme is written by ThemeProvider into local storage, not through settings;
 *  - the rest are read by the backend and have never had a frontend control.
 */
export const UNSURFACED_SETTINGS_KEYS = [
  "theme",
  "enable_auto_voiceprints",
  "enable_live_transcription",
  "processing_device",
  "worker_url",
] as const;

export const SETTINGS_REGISTRY: SettingsRegistryEntry[] = [
  // ---------------------------------------------------------------- profile
  {
    id: "profile-username",
    label: "Username",
    description: "The name you sign in with.",
    category: "profile",
    access: "all",
    keywords: ["username", "profile", "account", "name", "sign in", "login"],
  },
  {
    id: "profile-password",
    label: "Password",
    description: "Change the password used to sign in to this account.",
    category: "profile",
    access: "all",
    keywords: ["password", "security", "change password", "credentials"],
  },

  // ------------------------------------------------------------------ users
  {
    id: "users-accounts",
    label: "Users",
    description: "Create, edit, and review account access across the installation.",
    category: "users",
    access: "admin",
    keywords: ["users", "accounts", "roles", "permissions", "access", "superuser", "owner", "admin"],
  },
  {
    id: "users-invitations",
    label: "Invitations",
    description: "Issue and revoke invitation links for new sign-ups.",
    category: "users",
    access: "admin",
    keywords: ["invite", "invitation", "link", "join", "registration", "sign up", "revoke"],
  },

  // ------------------------------------------------------------- appearance
  {
    id: "appearance-theme",
    label: "Theme",
    description: "Choose how Nojoin looks in your browser.",
    category: "appearance",
    access: "all",
    keywords: ["theme", "appearance", "dark", "light", "colour", "color", "mode", "system default"],
  },
  {
    id: "appearance-timezone",
    label: "Timezone",
    description: "The timezone used across the dashboard, calendars, and task deadlines.",
    category: "appearance",
    access: "all",
    settingsKeys: ["timezone"],
    keywords: ["timezone", "time zone", "date", "time", "clock", "utc", "gmt", "bst", "locale"],
  },
  {
    id: "appearance-spellcheck",
    label: "Spellcheck",
    description: "The dictionary used when you edit notes and tasks.",
    category: "appearance",
    access: "all",
    settingsKeys: ["spellcheck_language"],
    defaultValue: "en-GB",
    keywords: ["spellcheck", "spelling", "dictionary", "language", "british", "american"],
  },

  // ----------------------------------------------------------- integrations
  {
    id: "integrations-calendars",
    label: "Calendar connections",
    description: "Connect Gmail or Outlook calendars and choose which ones Nojoin syncs.",
    category: "integrations",
    access: "all",
    keywords: ["calendar", "calendars", "gmail", "google", "outlook", "microsoft", "agenda", "events", "sync"],
  },
  {
    id: "integrations-connected-apps",
    label: "Connected apps",
    description: "AI assistants connected to Nojoin through MCP, and their access scopes.",
    category: "integrations",
    access: "all",
    keywords: ["connected apps", "mcp", "connector", "claude", "assistant", "integration", "revoke", "scope"],
  },
  {
    id: "integrations-calendar-providers",
    label: "Calendar provider credentials",
    description: "Installation-wide Google and Microsoft OAuth client credentials.",
    category: "integrations",
    access: "admin",
    advanced: "external-credentials",
    keywords: ["oauth", "client id", "client secret", "calendar provider", "google", "microsoft", "credentials", "live sync"],
  },

  // -------------------------------------------------------------- recording
  {
    id: "recording-microphone",
    label: "Microphone",
    description: "The input mixed with shared tab or system audio during capture.",
    category: "recording",
    access: "all",
    defaultValue: DEFAULT_CAPTURE_SETTINGS.microphoneDeviceId,
    keywords: ["microphone", "mic", "device", "input", "capture", "browser capture"],
  },
  {
    id: "recording-microphone-gain",
    label: "Microphone gain",
    description: "The local microphone level mixed into the recording.",
    category: "recording",
    access: "all",
    defaultValue: DEFAULT_CAPTURE_SETTINGS.microphoneGain,
    keywords: ["microphone gain", "mic gain", "level", "volume", "louder", "quieter"],
  },
  {
    id: "recording-shared-audio-gain",
    label: "Shared-audio gain",
    description: "The shared tab or system audio level relative to your microphone.",
    category: "recording",
    access: "all",
    defaultValue: DEFAULT_CAPTURE_SETTINGS.systemGain,
    keywords: ["shared audio", "system audio", "tab audio", "gain", "level", "volume"],
  },
  {
    id: "recording-automatic-levels",
    label: "Automatic levels",
    description: "Nojoin balances system and microphone levels while recording.",
    category: "recording",
    access: "all",
    keywords: ["automatic levels", "automatic gain", "balance", "levels", "mix"],
  },
  {
    id: "recording-input-test",
    label: "Live microphone input test",
    description: "Preview your microphone locally and check it lands well in the meter.",
    category: "recording",
    access: "all",
    keywords: ["input test", "test microphone", "meter", "preview", "waveform", "check mic"],
  },
  {
    id: "recording-echo-cancellation",
    label: "Echo cancellation",
    description: "Reduces loopback and speaker bleed for headset and speakerphone use.",
    category: "recording",
    access: "all",
    advanced: "safe-default",
    defaultValue: DEFAULT_CAPTURE_SETTINGS.echoCancellation,
    keywords: ["echo cancellation", "echo", "loopback", "speaker bleed", "feedback"],
  },
  {
    id: "recording-noise-suppression",
    label: "Noise suppression",
    description: "Reduces steady background noise before the mic is mixed into the recording.",
    category: "recording",
    access: "all",
    advanced: "safe-default",
    defaultValue: DEFAULT_CAPTURE_SETTINGS.noiseSuppression,
    keywords: ["noise suppression", "noise", "background", "hiss", "fan"],
  },
  {
    id: "recording-browser-auto-gain",
    label: "Browser auto gain",
    description: "Lets the browser lift a quiet microphone before Nojoin applies its own balancing.",
    category: "recording",
    access: "all",
    advanced: "safe-default",
    defaultValue: DEFAULT_CAPTURE_SETTINGS.autoGainControl,
    keywords: ["auto gain", "automatic gain control", "agc", "quiet microphone", "browser"],
  },
  {
    id: "recording-quiet-reminders",
    label: "Quiet-audio reminders",
    description: "Warnings shown when a recording captures little or no sound.",
    category: "recording",
    access: "all",
    advanced: "safe-default",
    keywords: ["quiet", "silence", "warning", "warnings", "reminder", "dismiss", "reset warnings"],
  },
  {
    id: "recording-vad",
    label: "Voice activity detection",
    description:
      "Filters silence and background noise before transcription. Disabling it can help if quiet speech is being cut off.",
    category: "recording",
    access: "all",
    advanced: "safe-default",
    settingsKeys: ["enable_vad"],
    defaultValue: true,
    keywords: ["vad", "voice activity detection", "silence", "filter", "processing"],
  },
  {
    id: "recording-diarization",
    label: "Speaker diarization",
    description:
      "Distinguishes between speakers. Disable it for single-speaker recordings to speed up processing.",
    category: "recording",
    access: "all",
    advanced: "can-degrade",
    settingsKeys: ["enable_diarization"],
    defaultValue: true,
    keywords: ["diarization", "speakers", "speaker separation", "who spoke", "pyannote", "processing"],
  },

  // ---------------------------------------------------------- transcription
  {
    id: "transcription-engine",
    label: "Transcription engine",
    description: "The speech-to-text engine used for every recording on this installation.",
    category: "transcription",
    access: "admin",
    settingsKeys: ["transcription_backend"],
    keywords: ["engine", "transcription", "speech to text", "asr", "whisper", "parakeet", "canary", "backend"],
  },
  {
    id: "transcription-model",
    label: "Transcription model",
    description: "The model size or variant the selected engine runs.",
    category: "transcription",
    access: "admin",
    advanced: "can-degrade",
    settingsKeys: ["whisper_model_size", "parakeet_model", "canary_model"],
    keywords: ["whisper model", "model size", "tiny", "base", "small", "medium", "large", "turbo", "parakeet", "canary", "accuracy"],
  },
  {
    id: "transcription-language",
    label: "Language",
    description:
      "The language spoken in your recordings, and the language notes and titles are written in.",
    category: "transcription",
    access: "all",
    settingsKeys: [
      "transcription_language",
      "notes_language",
      "notes_language_custom_instruction",
    ],
    keywords: ["language", "transcription language", "notes language", "british english", "american english", "localisation", "translation", "detect"],
  },
  {
    id: "transcription-glossary",
    label: "Glossary",
    description:
      "Project names, acronyms, and corrections for words the AI commonly mishears, one per line.",
    category: "transcription",
    access: "all",
    settingsKeys: ["glossary_terms", "install_glossary_terms"],
    keywords: ["glossary", "terms", "acronyms", "vocabulary", "jargon", "spelling", "corrections", "names"],
  },

  // ------------------------------------------------------------------ notes
  {
    id: "notes-structure",
    label: "Notes structure",
    description: "The sections and headings the AI writes meeting notes into.",
    category: "notes",
    access: "all",
    settingsKeys: ["notes_template_id", "install_notes_template_id"],
    keywords: ["notes structure", "notes template", "template", "templates", "sections", "headings", "action items", "decisions", "summary", "prompt"],
  },
  {
    id: "notes-short-titles",
    label: "Short meeting titles",
    description: "Prefer concise generated titles over descriptive ones.",
    category: "notes",
    access: "all",
    advanced: "safe-default",
    settingsKeys: ["prefer_short_titles"],
    defaultValue: true,
    keywords: ["short titles", "title", "titles", "automatic enhancement", "meeting intelligence", "naming"],
  },
  {
    id: "notes-meeting-edge",
    label: "Meeting Edge",
    description: "Live assistance during a meeting: questions, missed points, and concept help.",
    category: "notes",
    access: "all",
    settingsKeys: ["enable_meeting_edge"],
    keywords: ["meeting edge", "live", "assistant", "live assistance", "during meeting", "real time"],
  },
  {
    id: "notes-meeting-edge-context",
    label: "Technical context level",
    description: "How strict or detailed Meeting Edge concept explanations are.",
    category: "notes",
    access: "all",
    advanced: "safe-default",
    settingsKeys: ["meeting_edge_context_level"],
    defaultValue: DEFAULT_MEETING_EDGE_CONTEXT_LEVEL,
    keywords: ["technical context", "context level", "verbosity", "threshold", "jargon", "detail", "meeting edge"],
  },
  {
    id: "notes-meeting-edge-subscription-model",
    label: "Meeting Edge model (your subscription)",
    description: "The model your connected subscription uses for live assistance.",
    category: "notes",
    access: "all",
    advanced: "safe-default",
    settingsKeys: ["cli_live_model", "codex_live_model"],
    keywords: ["meeting edge model", "live model", "claude", "chatgpt", "codex", "subscription"],
  },
  {
    id: "notes-meeting-edge-install-model",
    label: "Meeting Edge model",
    description:
      "A separate model for live assistance. Left empty, Nojoin reuses the main model.",
    category: "notes",
    access: "admin",
    advanced: "safe-default",
    settingsKeys: [
      "gemini_live_model",
      "openai_live_model",
      "anthropic_live_model",
      "ollama_live_model",
    ],
    keywords: ["meeting edge model", "live model", "gemini", "openai", "anthropic", "ollama"],
  },

  // ---------------------------------------------------------------- your-ai
  {
    id: "your-ai-routing",
    label: "AI routing",
    description:
      "Whether your meetings use this installation's AI or your own connected subscription.",
    category: "your-ai",
    access: "all",
    settingsKeys: ["usage_model"],
    keywords: ["ai routing", "routing", "usage model", "server default", "my own subscription", "byok"],
  },
  {
    id: "your-ai-subscription",
    label: "Connected AI subscription",
    description: "Connect a Claude or ChatGPT subscription to run your own inference.",
    category: "your-ai",
    access: "all",
    settingsKeys: ["cli_provider"],
    keywords: ["subscription", "claude", "chatgpt", "codex", "cli oauth", "connect", "sign in", "pro", "max", "plus"],
  },
  {
    id: "your-ai-model",
    label: "Preferred model",
    description: "The model your connected subscription uses for notes and chat.",
    category: "your-ai",
    access: "all",
    advanced: "safe-default",
    settingsKeys: ["cli_model", "codex_model"],
    keywords: ["model", "claude model", "codex model", "opus", "sonnet", "gpt", "preferred"],
  },

  // ---------------------------------------------------------- ai-providers
  {
    id: "ai-providers-primary",
    label: "Primary provider and model",
    description: "The provider and model used for everyone who has not connected their own.",
    category: "ai-providers",
    access: "admin",
    settingsKeys: [
      "llm_provider",
      "gemini_model",
      "openai_model",
      "anthropic_model",
      "ollama_model",
      "gemini_api_key",
      "openai_api_key",
      "anthropic_api_key",
    ],
    keywords: ["provider", "llm", "gemini", "openai", "anthropic", "ollama", "model", "api key", "primary", "server default"],
  },
  {
    id: "ai-providers-ollama",
    label: "Ollama connection",
    description: "The Ollama endpoint and context window used when Ollama is the provider.",
    category: "ai-providers",
    access: "admin",
    advanced: "external-credentials",
    settingsKeys: ["ollama_api_url", "ollama_context_window"],
    keywords: ["ollama", "api url", "endpoint", "context window", "local model", "self hosted"],
  },
  {
    id: "ai-providers-fallback",
    label: "Fallback provider",
    description:
      "A secondary provider used when the primary one fails. Leave the provider empty to disable fallback.",
    category: "ai-providers",
    access: "admin",
    advanced: "can-degrade",
    settingsKeys: [
      "secondary_llm_provider",
      "secondary_gemini_model",
      "secondary_gemini_live_model",
      "secondary_openai_model",
      "secondary_openai_live_model",
      "secondary_anthropic_model",
      "secondary_anthropic_live_model",
      "secondary_ollama_model",
      "secondary_ollama_live_model",
      "secondary_ollama_api_url",
      "secondary_ollama_context_window",
      "secondary_gemini_api_key",
      "secondary_openai_api_key",
      "secondary_anthropic_api_key",
    ],
    keywords: ["fallback", "secondary", "backup provider", "failover", "resilience"],
  },
  {
    id: "ai-providers-hf-token",
    label: "Hugging Face token",
    description: "Required to download the speaker diarization models.",
    category: "ai-providers",
    access: "admin",
    advanced: "external-credentials",
    settingsKeys: ["hf_token"],
    keywords: ["hugging face", "hf token", "token", "diarization", "pyannote", "download", "gated model"],
  },
  {
    id: "ai-providers-model-assets",
    label: "Model assets",
    description: "Local model files, their download status, and disk usage.",
    category: "ai-providers",
    access: "admin",
    advanced: "safe-default",
    keywords: ["model assets", "dependencies", "download", "disk", "delete model", "prepare", "status"],
  },
  {
    id: "ai-providers-usage",
    label: "Usage and quota",
    description:
      "Per-user subscription token usage and rate-limit status, across Claude and ChatGPT.",
    category: "ai-providers",
    access: "admin",
    keywords: ["usage", "quota", "tokens", "rate limit", "consumption", "cli usage", "subscription"],
  },

  // ----------------------------------------------------------------- backup
  {
    id: "backup-export",
    label: "Backup",
    description: "Export application data as a restorable archive.",
    category: "backup",
    access: "admin",
    keywords: ["backup", "export", "archive", "download", "snapshot", "save"],
  },
  {
    id: "backup-restore",
    label: "Restore",
    description: "Recover application data from an archive, transactionally.",
    category: "backup",
    access: "admin",
    keywords: ["restore", "import", "recovery", "recover", "rollback", "upload"],
  },

  // ---------------------------------------------------------------- privacy
  {
    id: "privacy-telemetry",
    label: "Anonymous usage data",
    description:
      "A six-hourly anonymous ping describing this installation. It contains no meeting content.",
    category: "privacy",
    access: "admin",
    keywords: ["telemetry", "anonymous", "usage data", "analytics", "privacy", "opt out", "phone home", "tracking", "statistics"],
  },

  // ----------------------------------------------------------------- system
  {
    id: "system-logs",
    label: "Live logs",
    description: "Operational output from the Nojoin services.",
    category: "system",
    access: "admin",
    keywords: ["logs", "system", "operations", "docker", "worker", "redis", "infrastructure", "debug", "errors"],
  },

  // ---------------------------------------------------------------- updates
  {
    id: "updates-overview",
    label: "Release overview",
    description: "The version running here and whether a newer one is published.",
    category: "updates",
    access: "all",
    keywords: ["version", "update", "updates", "upgrade", "latest", "installed", "release"],
  },
  {
    id: "updates-latest",
    label: "Latest release",
    description: "What changed in the most recent published release.",
    category: "updates",
    access: "all",
    keywords: ["release notes", "changelog", "latest release", "what's new", "published"],
  },
  {
    id: "updates-history",
    label: "Release history",
    description: "Previous releases and their notes.",
    category: "updates",
    access: "all",
    keywords: ["history", "releases", "previous versions", "changelog", "archive"],
  },

  // ------------------------------------------------------------------- help
  {
    id: "help-tours",
    label: "Tours and demos",
    description: "Guided walkthroughs and demo content for trying Nojoin out.",
    category: "help",
    access: "all",
    keywords: ["help", "tour", "tours", "demo", "tutorial", "walkthrough", "onboarding", "guide"],
  },
  {
    id: "help-report-bug",
    label: "Report a bug",
    description: "Send a problem report with the details needed to investigate it.",
    category: "help",
    access: "all",
    keywords: ["bug", "report", "issue", "problem", "support", "feedback", "broken"],
  },
];

const REGISTRY_BY_CATEGORY = SETTINGS_REGISTRY.reduce<
  Partial<Record<SettingsCategoryId, SettingsRegistryEntry[]>>
>((accumulator, entry) => {
  (accumulator[entry.category] ??= []).push(entry);
  return accumulator;
}, {});

export function getRegistryEntriesForCategory(
  category: SettingsCategoryId,
  { isAdmin }: { isAdmin: boolean },
): SettingsRegistryEntry[] {
  const entries = REGISTRY_BY_CATEGORY[category] ?? [];
  return entries.filter((entry) => entry.access === "all" || isAdmin);
}

export function getRegistryEntry(id: string): SettingsRegistryEntry | undefined {
  return SETTINGS_REGISTRY.find((entry) => entry.id === id);
}

/**
 * The floor rule: a page never hides everything it has. If gating would leave a
 * category with no visible entry, that category shows everything and renders no
 * Advanced block.
 */
export function categoryUsesAdvancedGate(
  category: SettingsCategoryId,
  { isAdmin }: { isAdmin: boolean },
): boolean {
  const entries = getRegistryEntriesForCategory(category, { isAdmin });
  const gated = entries.filter((entry) => entry.advanced);

  return gated.length > 0 && gated.length < entries.length;
}

export function partitionByAdvanced(
  category: SettingsCategoryId,
  { isAdmin }: { isAdmin: boolean },
): { standard: SettingsRegistryEntry[]; advanced: SettingsRegistryEntry[] } {
  const entries = getRegistryEntriesForCategory(category, { isAdmin });

  if (!categoryUsesAdvancedGate(category, { isAdmin })) {
    return { standard: entries, advanced: [] };
  }

  return {
    standard: entries.filter((entry) => !entry.advanced),
    advanced: entries.filter((entry) => entry.advanced),
  };
}

/**
 * How many gated settings in a category differ from their shipped default, so a
 * collapsed Advanced block can advertise that something inside it was changed.
 * Entries without a declared default are skipped rather than guessed at.
 */
export function countChangedAdvanced(
  category: SettingsCategoryId,
  values: Record<string, unknown>,
  { isAdmin }: { isAdmin: boolean },
): number {
  const { advanced } = partitionByAdvanced(category, { isAdmin });

  return advanced.filter((entry) => {
    if (entry.defaultValue === undefined || !entry.settingsKeys?.length) {
      return false;
    }

    return entry.settingsKeys.some((key) => {
      const value = values[key];
      return value !== undefined && value !== entry.defaultValue;
    });
  }).length;
}

export interface SettingsSearchResult {
  entry: SettingsRegistryEntry;
  /** Lower is better; see getMatchScore. */
  score: number;
}

/** Worse than this and a result is noise rather than a match. */
const SEARCH_SCORE_CEILING = 0.6;

/**
 * Cross-category search over individual settings. Returns entries rather than
 * categories, so a result can name the setting and route straight to it, and so
 * a match inside a collapsed Advanced block is still reachable.
 */
export function searchSettingsRegistry(
  query: string,
  { isAdmin }: { isAdmin: boolean },
  limit = 12,
): SettingsSearchResult[] {
  const trimmed = query.trim();
  if (!trimmed) {
    return [];
  }

  return SETTINGS_REGISTRY.filter(
    (entry) => entry.access === "all" || isAdmin,
  )
    .map((entry) => ({
      entry,
      score: getMatchScore(trimmed, [
        entry.label,
        ...entry.keywords,
        ...(entry.description ? [entry.description] : []),
      ]),
    }))
    .filter((result) => result.score < SEARCH_SCORE_CEILING)
    .sort((a, b) => a.score - b.score || a.entry.label.localeCompare(b.entry.label))
    .slice(0, limit);
}

/** Category ids that a query matches, for highlighting in the navigation. */
export function getMatchingSettingsCategories(
  query: string,
  options: { isAdmin: boolean },
): Set<SettingsCategoryId> {
  return new Set(
    searchSettingsRegistry(query, options, SETTINGS_REGISTRY.length).map(
      (result) => result.entry.category,
    ),
  );
}
