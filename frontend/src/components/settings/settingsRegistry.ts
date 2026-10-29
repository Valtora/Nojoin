import { getSecondaryProviderGap } from "@/lib/aiAvailability";
import { DEFAULT_MEETING_EDGE_CONTEXT_LEVEL } from "@/lib/meetingEdgeContext";
import { DEFAULT_CAPTURE_SETTINGS } from "@/lib/capture/shared";
import { getMatchScore } from "@/lib/searchUtils";

import type { Settings } from "@/types";

import {
  canViewSettings,
  type SettingsAccess,
  type SettingsCategoryId,
  type SettingsScope,
  type SettingsViewer,
} from "./settingsCategories";

/**
 * The settings manifest.
 *
 * One entry per user-facing setting or panel. This is the single source of
 * truth for three things:
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
  /**
   * "admin" hides the entry from non-admins and "superuser" from everyone but
   * the superuser, independently of the Advanced gate.
   */
  access: SettingsAccess;
  /**
   * Present when the setting is not per account: "everyone" for one value the
   * whole installation shares, "browser" for one kept in this browser's local
   * storage. The card, row or block rendering this id shows it as a badge, and
   * search results show it too; a row inside a card of the same scope leaves
   * it to the card. Entries on admin-only pages leave it out, because every
   * setting there is install-wide and the page description says so once.
   */
  scope?: SettingsScope;
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
  /**
   * True when the setting is in a state that stops something working. A gated
   * entry that needs attention is announced on the collapsed Advanced block, so
   * a warning is never hidden behind it.
   */
  needsAttention?: (settings: Settings) => boolean;
  keywords: string[];
}

/**
 * Fields on the `Settings` interface with no control anywhere in the UI. Listed
 * explicitly so the parity test still passes while making the omission visible:
 * a key arriving here should be a decision, not an oversight.
 *
 *  - theme is written by ThemeProvider into local storage, not through settings;
 *  - hide_recording_consent_notice is changed in the recording notice itself;
 *  - processing_tuning_install is read-only context the API supplies (the
 *    installation's tuning values) for validation, not a value anyone sets;
 *  - live_speaker_labels and speaker_engine are install-wide settings managed
 *    through config.json or the administrator API, without frontend controls;
 *  - the rest are read by the backend and have never had a frontend control.
 */
export const UNSURFACED_SETTINGS_KEYS = [
  "hide_recording_consent_notice",
  "theme",
  "enable_auto_voiceprints",
  "enable_live_transcription",
  "live_speaker_labels",
  "speaker_engine",
  "processing_device",
  "worker_url",
  "processing_tuning_install",
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
  {
    id: "users-ai-analysis-limit",
    label: "Analyses per person per day",
    description: "The daily limit on AI meeting analyses, for everyone without their own.",
    category: "users",
    access: "admin",
    settingsKeys: ["ai_analysis_daily_limit"],
    keywords: ["analysis budget", "daily limit", "quota", "ai analysis", "limit", "cap"],
  },

  // ------------------------------------------------------------- appearance
  {
    id: "appearance-theme",
    label: "Look and feel",
    description: "Theme, colour palette, corner style and density, saved in this browser.",
    category: "appearance",
    access: "all",
    scope: "browser",
    keywords: [
      "look and feel", "theme", "appearance", "dark", "light", "colour", "color", "mode", "system default",
      "palette", "accent", "corners", "rounded", "square", "radius", "density", "compact",
      "comfortable", "dense", "spacing",
    ],
  },
  {
    id: "appearance-style",
    label: "Style",
    description: "Bevels and layered elevation, saved in this browser. Classic looks best with Square corners.",
    category: "appearance",
    access: "all",
    scope: "browser",
    keywords: ["style", "bevel", "elevation", "classic", "depth"],
  },
  {
    id: "appearance-timezone",
    label: "Timezone",
    description: "The timezone of every date and time Nojoin shows.",
    category: "appearance",
    access: "all",
    settingsKeys: ["timezone"],
    keywords: ["timezone", "time zone", "date", "time", "clock", "utc", "gmt", "bst", "locale"],
  },
  {
    id: "appearance-export-branding",
    label: "Nojoin branding on exports",
    description: "Whether exported PDF, Word and text files carry the Nojoin letterhead and footer line.",
    category: "appearance",
    access: "admin",
    scope: "everyone",
    settingsKeys: ["export_branding"],
    defaultValue: true,
    keywords: [
      "export", "exports", "branding", "brand", "letterhead", "logo", "footer", "pdf", "word",
      "docx", "white label", "white-label", "advertising",
    ],
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
    label: "Calendars",
    description: "Connect a Google or Outlook calendar and choose which ones Nojoin shows.",
    category: "integrations",
    access: "all",
    keywords: ["calendar connections", "calendar", "calendars", "gmail", "google", "outlook", "microsoft", "agenda", "events", "sync"],
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
    label: "Calendar sign-in",
    description: "The Google and Microsoft apps this installation registers, so people can connect their calendars.",
    category: "integrations",
    access: "admin",
    scope: "everyone",
    advanced: "external-credentials",
    keywords: ["calendar sign-in", "calendar provider credentials", "oauth", "client id", "client secret", "calendar provider", "google", "microsoft", "credentials", "live sync"],
  },

  // -------------------------------------------------------------- recording
  {
    id: "recording-input",
    label: "Input",
    description: "The microphone added to shared audio, and the levels it is mixed at.",
    category: "recording",
    access: "all",
    scope: "browser",
    keywords: ["input", "audio input", "levels", "refresh devices"],
  },
  {
    id: "recording-microphone",
    label: "Microphone",
    description: "The input mixed with shared tab or system audio during capture.",
    category: "recording",
    access: "all",
    scope: "browser",
    defaultValue: DEFAULT_CAPTURE_SETTINGS.microphoneDeviceId,
    keywords: ["microphone", "mic", "device", "input", "capture", "browser capture"],
  },
  {
    id: "recording-microphone-gain",
    label: "Microphone gain",
    description: "The local microphone level mixed into the recording.",
    category: "recording",
    access: "all",
    scope: "browser",
    defaultValue: DEFAULT_CAPTURE_SETTINGS.microphoneGain,
    keywords: ["microphone gain", "mic gain", "level", "volume", "louder", "quieter"],
  },
  {
    id: "recording-shared-audio-gain",
    label: "Shared-audio gain",
    description: "The shared tab or system audio level relative to your microphone.",
    category: "recording",
    access: "all",
    scope: "browser",
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
    id: "recording-browser-processing",
    label: "Browser processing",
    description: "What the browser does to the microphone before Nojoin receives it.",
    category: "recording",
    access: "all",
    scope: "browser",
    advanced: "safe-default",
    keywords: ["browser processing", "echo", "noise", "auto gain", "agc", "constraints"],
  },
  {
    id: "recording-echo-cancellation",
    label: "Echo cancellation",
    description: "Reduces loopback and speaker bleed for headset and speakerphone use.",
    category: "recording",
    access: "all",
    scope: "browser",
    advanced: "safe-default",
    defaultValue: DEFAULT_CAPTURE_SETTINGS.echoCancellation,
    keywords: ["echo cancellation", "echo", "loopback", "speaker bleed", "feedback"],
  },
  {
    id: "recording-noise-suppression",
    label: "Noise suppression",
    description: "Reduces steady background noise before the mic is mixed into the recording. Off by default, because it makes speaker labels less accurate in most rooms.",
    category: "recording",
    access: "all",
    scope: "browser",
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
    scope: "browser",
    advanced: "safe-default",
    defaultValue: DEFAULT_CAPTURE_SETTINGS.autoGainControl,
    keywords: ["auto gain", "automatic gain control", "agc", "quiet microphone", "browser"],
  },
  {
    id: "recording-quiet-reminders",
    label: "Quiet audio warnings",
    description: "Warnings shown when a recording picks up little or no sound.",
    category: "recording",
    access: "all",
    scope: "browser",
    advanced: "safe-default",
    keywords: ["quiet audio warnings", "quiet-audio reminders", "quiet", "silence", "warning", "warnings", "reminder", "dismiss", "reset warnings"],
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
    id: "recording-vad-mute-transcription",
    label: "Mute non-speech for transcription",
    description:
      "On mutes only audio that neither voice activity detection nor unmuted speaker separation heard as speech. Off lets transcription hear everything: it can recover distant speech but can add words from noise.",
    category: "recording",
    access: "all",
    advanced: "safe-default",
    settingsKeys: ["vad_mute_transcription"],
    defaultValue: true,
    keywords: ["vad", "mute", "unmuted", "silence", "transcription", "distant", "far-field"],
  },
  {
    id: "recording-vad-mute-diarization",
    label: "Mute non-speech for speaker separation",
    description:
      "On limits speaker separation to the filtered speech: faster on long recordings, but worse at telling speakers apart.",
    category: "recording",
    access: "all",
    advanced: "safe-default",
    settingsKeys: ["vad_mute_diarization"],
    defaultValue: false,
    keywords: ["vad", "mute", "unmuted", "silence", "diarization", "speaker separation", "short replies"],
  },
  {
    id: "recording-remeasure-delivery",
    label: "Re-measure delivery after edits",
    description:
      "Opening the Analytics tab re-measures delivery figures that a transcript edit left stale. Off keeps the figures until you press Measure delivery.",
    category: "recording",
    access: "all",
    advanced: "safe-default",
    settingsKeys: ["auto_remeasure_delivery"],
    defaultValue: true,
    keywords: ["analytics", "delivery", "re-measure", "remeasure", "stale", "edit", "pace", "pitch"],
  },
  {
    id: "recording-diarization",
    label: "Speaker separation",
    description: "Tells the speakers apart. Turn it off for single-speaker recordings to speed up processing.",
    category: "recording",
    access: "all",
    advanced: "can-degrade",
    settingsKeys: ["enable_diarization"],
    defaultValue: true,
    keywords: ["diarisation", "speaker diarization", "diarization", "speakers", "speaker separation", "who spoke", "pyannote", "processing"],
  },
  // Processing tuning. defaultValue null: unset means inherit, so only a value
  // the user actually set counts as changed.
  {
    id: "recording-vad-threshold",
    label: "Speech detection threshold",
    description:
      "How confident voice activity detection must be before audio counts as speech. Lower keeps quiet speech; higher drops more noise.",
    category: "recording",
    access: "all",
    advanced: "can-degrade",
    settingsKeys: ["vad_threshold"],
    defaultValue: null,
    keywords: ["vad threshold", "speech detection", "quiet speech", "soft speech", "silero", "large meeting", "tuning"],
  },
  {
    id: "recording-word-length-cap",
    label: "Word length cap",
    description:
      "The longest a Parakeet word lasts, which decides whether a short word reaches a speaker turn.",
    category: "recording",
    access: "all",
    advanced: "can-degrade",
    settingsKeys: ["asr_word_length_cap_s"],
    defaultValue: null,
    keywords: ["word padding", "word end", "word length", "parakeet", "timing", "brief speaker", "tuning"],
  },
  {
    id: "recording-phantom-filter",
    label: "Phantom speaker filter",
    description:
      "Which brief speakers count as noise to reassign, or as an existing speaker to merge, rather than a real person.",
    category: "recording",
    access: "all",
    advanced: "can-degrade",
    settingsKeys: [
      "phantom_max_duration_s",
      "phantom_max_segments",
      "phantom_embedding_floor",
      "phantom_merge_threshold",
    ],
    defaultValue: null,
    keywords: ["phantom speaker", "brief speaker", "short speaker", "noise", "chime", "large meeting", "tuning"],
  },
  {
    id: "recording-word-flip",
    label: "Single-word flip smoothing",
    description:
      "Gives a very short word inside one speaker's run back to that speaker. Loosening it can erase brief interjections.",
    category: "recording",
    access: "all",
    advanced: "can-degrade",
    settingsKeys: ["word_flip_max_duration_s", "word_flip_max_gap_s"],
    defaultValue: null,
    keywords: ["word flip", "smoothing", "interjection", "brief speaker", "speaker change", "tuning"],
  },

  // ---------------------------------------------------------- transcription
  {
    id: "transcription-line-break-pause",
    label: "Line break pause",
    description: "Start a new line after a longer pause in live settling lines and diarised final transcripts when speakers are detected.",
    category: "transcription",
    access: "all",
    settingsKeys: ["line_break_pause_s"],
    defaultValue: null,
    keywords: ["line", "break", "pause", "gap", "silence", "transcript", "live", "settling"],
  },
  {
    id: "transcription-live-settling",
    label: "Live word settling",
    description: "Revise recent words and lock unchanged wording on a GPU, or on a CPU too.",
    category: "transcription",
    access: "admin",
    scope: "everyone",
    advanced: "can-degrade",
    settingsKeys: ["live_text_settling"],
    defaultValue: "auto",
    keywords: ["live", "settling", "word", "GPU", "CPU"],
  },
  {
    id: "transcription-live-settling-k",
    label: "Unchanged comparisons",
    description: "Successful unchanged comparisons before live words lock.",
    category: "transcription",
    access: "admin",
    scope: "everyone",
    advanced: "can-degrade",
    settingsKeys: ["live_text_settling_k"],
    defaultValue: 3,
    keywords: ["live", "settling", "comparisons", "agreement"],
  },
  {
    id: "transcription-live-settling-window",
    label: "Settling window",
    description: "Total audio cap per live settling pass, including lead-up.",
    category: "transcription",
    access: "admin",
    scope: "everyone",
    advanced: "can-degrade",
    settingsKeys: ["live_text_settling_window_s"],
    defaultValue: 10,
    keywords: ["live", "settling", "window", "seconds"],
  },
  {
    id: "transcription-engine",
    label: "Transcription engine",
    description: "The speech-to-text engine used for every recording on this installation.",
    category: "transcription",
    access: "admin",
    scope: "everyone",
    settingsKeys: ["transcription_backend"],
    keywords: ["engine", "transcription", "speech to text", "asr", "whisper", "parakeet", "canary", "backend"],
  },
  {
    id: "transcription-model",
    label: "Transcription model",
    description: "The model size or variant the selected engine runs.",
    category: "transcription",
    access: "admin",
    scope: "everyone",
    advanced: "can-degrade",
    settingsKeys: ["whisper_model_size", "parakeet_model", "canary_model"],
    keywords: ["whisper model", "model size", "tiny", "base", "small", "medium", "large", "turbo", "parakeet", "canary", "accuracy"],
  },
  {
    id: "transcription-language",
    label: "Transcription language",
    description: "The language spoken in your recordings, or detect it automatically.",
    category: "transcription",
    access: "all",
    settingsKeys: ["transcription_language"],
    keywords: ["language", "transcription language", "spoken language", "detect", "auto-detect", "forced language"],
  },
  {
    id: "transcription-glossary",
    label: "Glossary",
    description:
      "Project names, acronyms, and corrections for words the AI commonly mishears, one per line.",
    category: "transcription",
    access: "all",
    settingsKeys: ["glossary_terms"],
    keywords: ["glossary", "terms", "acronyms", "vocabulary", "jargon", "spelling", "corrections", "names"],
  },
  {
    id: "transcription-shared-glossary",
    label: "Shared glossary",
    description:
      "Terms everyone on this installation shares, added to each person's own glossary. Only an administrator can change them.",
    category: "transcription",
    access: "all",
    scope: "everyone",
    settingsKeys: ["install_glossary_terms"],
    keywords: ["shared glossary", "install glossary", "organisation glossary", "glossary", "shared terms"],
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
    id: "notes-language",
    label: "Notes language",
    description: "The language and style generated titles and notes are written in.",
    category: "notes",
    access: "all",
    settingsKeys: ["notes_language", "notes_language_custom_instruction"],
    keywords: ["language", "notes language", "british english", "american english", "localisation", "translation", "style"],
  },
  {
    id: "notes-short-titles",
    label: "Short titles",
    description: "Prefer concise generated titles over descriptive ones.",
    category: "notes",
    access: "all",
    settingsKeys: ["prefer_short_titles"],
    defaultValue: true,
    keywords: ["short meeting titles", "short titles", "title", "titles", "automatic enhancement", "meeting intelligence", "naming"],
  },
  {
    id: "notes-auto-analyse",
    label: "Analyse meetings automatically",
    description:
      "Run the AI analysis of each meeting when it finishes processing, within the daily limit. Uses your AI provider quota.",
    category: "notes",
    access: "all",
    settingsKeys: ["auto_analyse_meetings"],
    defaultValue: false,
    keywords: ["analytics", "analyse", "analysis", "automatic", "budget", "quota", "topics", "decisions"],
  },
  {
    id: "notes-confirm-suggested-names",
    label: "Confirm suggested names automatically",
    description:
      "Take the speaker names the AI, a self-introduction or a voiceprint suggests as given, instead of confirming each one, for meetings processed from now on.",
    category: "notes",
    access: "all",
    settingsKeys: ["confirm_suggested_names"],
    defaultValue: false,
    keywords: ["speaker names", "suggested names", "confirm names", "speakers", "voiceprint", "introduction", "identify", "automatic"],
  },
  {
    id: "notes-voiceprint-learning",
    label: "Learn voiceprints from unconfirmed names",
    description:
      "Let a voice match or a name taken as given update the person's voiceprint before you confirm it.",
    category: "notes",
    access: "all",
    settingsKeys: ["voiceprint_learning"],
    defaultValue: "confirmed",
    keywords: ["voiceprint", "learning", "voice match", "people", "speakers", "confirm", "automatic"],
  },
  {
    id: "notes-meeting-edge-context",
    label: "Technical context",
    description: "How strict or detailed Meeting Edge explanations of terms are.",
    category: "notes",
    access: "all",
    settingsKeys: ["meeting_edge_context_level"],
    defaultValue: DEFAULT_MEETING_EDGE_CONTEXT_LEVEL,
    keywords: ["technical context level", "technical context", "context level", "verbosity", "threshold", "jargon", "detail", "meeting edge"],
  },

  // ---------------------------------------------------------------- your-ai
  {
    id: "your-ai-routing",
    label: "How AI runs for you",
    description:
      "Whether your meetings use this installation's AI or your own connected subscription.",
    category: "your-ai",
    access: "all",
    settingsKeys: ["usage_model"],
    keywords: ["how ai runs for you", "ai routing", "routing", "usage model", "server default", "my own subscription", "byok"],
  },
  {
    id: "your-ai-subscription",
    label: "Your subscriptions",
    description: "Connect a Claude or ChatGPT subscription to run your meetings on your own plan.",
    category: "your-ai",
    access: "all",
    settingsKeys: ["cli_provider"],
    keywords: ["your subscriptions", "connected ai subscription", "subscription", "claude", "chatgpt", "codex", "cli oauth", "connect", "sign in", "pro", "max", "plus"],
  },
  {
    id: "your-ai-model",
    label: "Preferred model",
    description: "The model your connected subscription uses for notes and chat.",
    category: "your-ai",
    access: "all",
    settingsKeys: ["cli_model", "codex_model"],
    keywords: ["model", "claude model", "codex model", "opus", "sonnet", "gpt", "preferred"],
  },
  {
    id: "your-ai-live-model",
    label: "Meeting Edge model (your subscription)",
    description: "The model your connected subscription uses for live guidance.",
    category: "your-ai",
    access: "all",
    settingsKeys: ["cli_live_model", "codex_live_model"],
    keywords: ["meeting edge model", "live model", "claude", "chatgpt", "codex", "subscription"],
  },

  // ---------------------------------------------------------- ai-providers
  {
    id: "ai-providers-primary",
    label: "Default provider",
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
    keywords: ["default provider", "default model", "provider", "llm", "gemini", "openai", "anthropic", "ollama", "model", "api key", "primary", "server default", "default provider"],
  },
  {
    id: "ai-providers-ollama",
    label: "Ollama address and context window",
    description: "The Ollama endpoint and context window used when Ollama is the provider.",
    category: "ai-providers",
    access: "admin",
    settingsKeys: ["ollama_api_url", "ollama_context_window"],
    keywords: ["ollama", "api url", "endpoint", "context window", "local model", "self hosted"],
  },
  {
    id: "ai-providers-meeting-edge",
    label: "Meeting Edge",
    description: "Live guidance during a recording, for everyone on this installation.",
    category: "ai-providers",
    access: "admin",
    settingsKeys: ["enable_meeting_edge"],
    keywords: ["meeting edge", "live", "assistant", "live assistance", "during meeting", "real time"],
  },
  {
    id: "ai-providers-meeting-edge-model",
    label: "Meeting Edge model",
    description:
      "A separate model for live guidance. Left empty, Nojoin uses the default model.",
    category: "ai-providers",
    access: "admin",
    settingsKeys: [
      "gemini_live_model",
      "openai_live_model",
      "anthropic_live_model",
      "ollama_live_model",
    ],
    keywords: ["meeting edge model", "live model", "gemini", "openai", "anthropic", "ollama"],
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
    needsAttention: (settings) => getSecondaryProviderGap(settings) !== null,
    keywords: ["fallback", "secondary", "backup provider", "failover", "resilience"],
  },
  {
    id: "ai-providers-hf-token",
    label: "Hugging Face access token",
    description: "Needed to download the speaker separation models.",
    category: "ai-providers",
    access: "admin",
    advanced: "external-credentials",
    settingsKeys: ["hf_token"],
    keywords: ["access token", "speaker separation", "hugging face", "hf token", "token", "diarization", "pyannote", "download", "gated model"],
  },
  {
    id: "ai-providers-model-assets",
    label: "Model downloads",
    description: "Local model files, their download status, and disk usage.",
    category: "ai-providers",
    access: "admin",
    advanced: "safe-default",
    keywords: ["model downloads", "model assets", "dependencies", "download", "disk", "delete model", "prepare", "status"],
  },
  {
    id: "ai-providers-usage",
    label: "Subscription usage",
    description:
      "Tokens each person sent through their own Claude or ChatGPT subscription, and whether it is rate-limited.",
    category: "ai-providers",
    access: "admin",
    advanced: "scale-only",
    keywords: ["usage", "quota", "tokens", "rate limit", "consumption", "cli usage", "subscription"],
  },

  // ----------------------------------------------------------------- backup
  {
    id: "backup-export",
    label: "Back up",
    description: "Download a zip of everything on this installation, to restore later.",
    category: "backup",
    access: "superuser",
    keywords: ["back up", "back up now", "backup", "export", "archive", "download", "snapshot", "save"],
  },
  {
    id: "backup-restore",
    label: "Restore",
    description: "Bring this installation back from a backup zip.",
    category: "backup",
    access: "superuser",
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
    id: "system-health",
    label: "Service health",
    description: "Whether the database, queue, worker, models and AI are ready.",
    category: "system",
    access: "admin",
    keywords: ["health", "status", "worker", "queue", "database", "readiness", "pipeline", "gpu"],
  },
  {
    id: "system-logs",
    label: "Logs",
    description: "Live output from the Nojoin services.",
    category: "system",
    // The log download and stream require the superuser; health does not.
    access: "superuser",
    keywords: ["live logs", "logs", "system", "operations", "docker", "worker", "redis", "infrastructure", "debug", "errors"],
  },

  // ---------------------------------------------------------------- updates
  {
    id: "updates-overview",
    label: "Version",
    description: "The version running here and whether a newer one is published.",
    category: "updates",
    access: "all",
    keywords: ["version", "update", "updates", "upgrade", "latest", "installed", "release", "check for updates"],
  },
  {
    id: "updates-upgrade",
    label: "How to upgrade",
    description: "The commands that pull a new release and recreate the stack.",
    category: "updates",
    access: "admin",
    keywords: ["upgrade", "docker", "compose", "pull", "deployment", "install update"],
  },
  {
    id: "updates-release-notes",
    label: "Release notes",
    description: "What changed in each published release.",
    category: "updates",
    access: "all",
    keywords: ["release notes", "changelog", "latest release", "what's new", "history", "releases", "previous versions"],
  },

  // ------------------------------------------------------------------- help
  {
    id: "help-tours",
    label: "Tours and demo",
    description: "Guided walkthroughs and demo content for trying Nojoin out.",
    category: "help",
    access: "all",
    keywords: ["tours and demos", "help", "tour", "tours", "demo", "tutorial", "walkthrough", "onboarding", "guide"],
  },
  {
    id: "help-report-bug",
    label: "Report a problem",
    description: "Send a problem report with the details needed to investigate it.",
    category: "help",
    access: "all",
    keywords: ["report a bug", "bug", "report", "issue", "problem", "support", "feedback", "broken"],
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
  viewer: SettingsViewer,
): SettingsRegistryEntry[] {
  const entries = REGISTRY_BY_CATEGORY[category] ?? [];
  return entries.filter((entry) => canViewSettings(entry.access, viewer));
}

const REGISTRY_BY_ID = new Map(SETTINGS_REGISTRY.map((entry) => [entry.id, entry]));

export function getRegistryEntry(id: string): SettingsRegistryEntry | undefined {
  return REGISTRY_BY_ID.get(id);
}

/** The scope a card, row or block with this id carries, if it has one. */
export function getSettingsScope(id: string | undefined): SettingsScope | undefined {
  return id ? REGISTRY_BY_ID.get(id)?.scope : undefined;
}

/**
 * The floor rule: a page never hides everything it has. If gating would leave a
 * category with no visible entry, that category shows everything and renders no
 * Advanced block.
 */
export function categoryUsesAdvancedGate(
  category: SettingsCategoryId,
  viewer: SettingsViewer,
): boolean {
  const entries = getRegistryEntriesForCategory(category, viewer);
  const gated = entries.filter((entry) => entry.advanced);

  return gated.length > 0 && gated.length < entries.length;
}

export function partitionByAdvanced(
  category: SettingsCategoryId,
  viewer: SettingsViewer,
): { standard: SettingsRegistryEntry[]; advanced: SettingsRegistryEntry[] } {
  const entries = getRegistryEntriesForCategory(category, viewer);

  if (!categoryUsesAdvancedGate(category, viewer)) {
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
  viewer: SettingsViewer,
): number {
  const { advanced } = partitionByAdvanced(category, viewer);

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

/** How many gated settings in a category need attention; see `needsAttention`. */
export function countAttentionAdvanced(
  category: SettingsCategoryId,
  settings: Settings,
  viewer: SettingsViewer,
): number {
  const { advanced } = partitionByAdvanced(category, viewer);
  return advanced.filter((entry) => entry.needsAttention?.(settings)).length;
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
  viewer: SettingsViewer,
  limit = 12,
): SettingsSearchResult[] {
  const trimmed = query.trim();
  if (!trimmed) {
    return [];
  }

  return SETTINGS_REGISTRY.filter((entry) =>
    canViewSettings(entry.access, viewer),
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
  viewer: SettingsViewer,
): Set<SettingsCategoryId> {
  return new Set(
    searchSettingsRegistry(query, viewer, SETTINGS_REGISTRY.length).map(
      (result) => result.entry.category,
    ),
  );
}
