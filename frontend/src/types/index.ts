export enum RecordingStatus {
  UPLOADING = "UPLOADING",
  PAUSED = "PAUSED",
  RECORDED = "RECORDED",
  QUEUED = "QUEUED",
  PROCESSING = "PROCESSING",
  PROCESSED = "PROCESSED",
  ERROR = "ERROR",
  CANCELLED = "CANCELLED",
}

export enum ClientStatus {
  RECORDING = "RECORDING",
  PAUSED = "PAUSED",
  UPLOADING = "UPLOADING",
  IDLE = "IDLE",
}

export enum UserRole {
  OWNER = "owner",
  ADMIN = "admin",
  USER = "user",
}

export interface User {
  id: number;
  username: string;
  is_active: boolean;
  is_superuser: boolean;
  role: UserRole;
  force_password_change: boolean;
}

export interface Invitation {
  id: number;
  code: string;
  role: UserRole;
  expires_at?: string;
  max_uses?: number;
  used_count: number;
  is_revoked: boolean;
  created_by_id: number;
  link: string;
  users: string[];
}

export interface BaseDBModel {
  id: number;
  created_at: string;
  updated_at: string;
}

export type RecordingId = string;

export interface PeopleTag {
  id: number;
  name: string;
  color?: string;
  user_id?: number | null;
  parent_id?: number | null;
  created_at: string;
  updated_at: string;
}

export interface GlobalSpeaker extends BaseDBModel {
  name: string;
  has_voiceprint?: boolean;
  is_voiceprint_locked?: boolean;
  recording_count?: number; // Number of recordings this speaker is associated with
  color?: string;
  // CRM Fields
  title?: string | null;
  company?: string | null;
  email?: string | null;
  phone_number?: string | null;
  notes?: string | null;
  tags?: PeopleTag[];
}

export interface RecordingSpeaker extends BaseDBModel {
  recording_id: RecordingId;
  global_speaker_id?: number;
  diarization_label: string;
  local_name?: string; // Name local to this recording only
  name?: string; // Deprecated: kept for backward compatibility
  snippet_start?: number;
  snippet_end?: number;
  voice_snippet_path?: string;
  has_voiceprint?: boolean;
  global_speaker?: GlobalSpeaker;
  color?: string;
  merged_into_id?: number | null;
}

export type SpeakerNameSuggestionStatus =
  | "pending"
  | "accepted"
  | "rejected"
  | "superseded";

export interface SpeakerNameSuggestionEvidence {
  quote: string;
  reason: string;
  start_seconds?: number | null;
  end_seconds?: number | null;
}

export interface SpeakerNameSuggestion {
  id: string;
  diarization_label: string;
  recording_speaker_id?: number | null;
  suggested_name: string;
  suggested_global_speaker_id?: number | null;
  confidence: number;
  status: SpeakerNameSuggestionStatus;
  origin: string;
  source: string;
  provider?: string | null;
  rationale?: string | null;
  evidence_spans: SpeakerNameSuggestionEvidence[];
  signals: string[];
  created_at: string;
  updated_at: string;
  resolved_at?: string | null;
  resolution_reason?: string | null;
  resolution_actor_user_id?: number | null;
}

export type SpeakerCorrectionScope =
  | "utterance_only"
  | "speaker_everywhere_in_recording"
  | "from_this_utterance_forward"
  | "merge_into_speaker";

export interface TranscriptSpeakerAssignment {
  name: string;
  globalSpeakerId?: number;
  diarizationLabel?: string;
  scope: SpeakerCorrectionScope;
}

export type ExportContentType = "transcript" | "notes" | "both" | "audio";

/** A browser capture channel: the local microphone or the shared system audio. */
export type CaptureSourceChannel = "microphone" | "system";

export interface TranscriptUtterance {
  id: string;
  start: number;
  end: number;
  start_ms?: number;
  end_ms?: number;
  text: string;
  speaker: string;
  recording_speaker_id?: number;
  state?: string;
  revision: number;
  speaker_state?: "provisional" | "stable" | "manual_override" | string;
  overlapping_speakers?: string[];
  provisional?: boolean;
  segment_source?: "live" | string;
  speaker_manually_edited?: boolean;
  text_manually_edited?: boolean;
  speaker_confidence?: number | null;
  /**
   * Which capture channel carried this audio, when the live lane could tell.
   * Audio provenance only: a capture with no shared tab audio still carries
   * every voice in the room on the microphone channel, so this must never be
   * presented as a speaker identity. Null when the sources overlapped or
   * neither dominated.
   */
  source_channel?: CaptureSourceChannel | null;
  text_confidence?: number | null;
  speaker_assignment_source?: string;
  speaker_assignment_authority?: "provisional" | "finalized" | "manual" | string;
  updated_at?: string | null;
  speaker_state_source?: string;
  live_source_speaker?: string | null;
  live_source_speakers?: string[];
  source_public_ids?: string[];
  live_reuse_alignment?: Record<string, unknown>;
}

export interface TranscriptUtteranceList {
  recording_id: RecordingId;
  revision: number;
  utterances: TranscriptUtterance[];
  tombstones: string[];
  speakers: RecordingSpeaker[];
}

export interface TranscriptSegment {
  id?: string;
  start: number;
  end: number;
  text: string;
  speaker: string;
  recording_speaker_id?: number;
  state?: string;
  revision?: number;
  speaker_state?: "provisional" | "stable" | "manual_override" | string;
  overlapping_speakers?: string[];
  provisional?: boolean;
  segment_source?: "live" | string;
  speaker_manually_edited?: boolean;
  text_manually_edited?: boolean;
  speaker_confidence?: number | null;
  /**
   * Which capture channel carried this audio, when the live lane could tell.
   * Audio provenance only: a capture with no shared tab audio still carries
   * every voice in the room on the microphone channel, so this must never be
   * presented as a speaker identity. Null when the sources overlapped or
   * neither dominated.
   */
  source_channel?: CaptureSourceChannel | null;
  text_confidence?: number | null;
  speaker_assignment_source?: string;
  speaker_assignment_authority?: "provisional" | "finalized" | "manual" | string;
  updated_at?: string | null;
  speaker_state_source?: string;
  live_source_speaker?: string | null;
  live_source_speakers?: string[];
  source_public_ids?: string[];
  live_reuse_alignment?: Record<string, unknown>;
}

export interface MeetingEdgeConcept {
  term: string;
  explanation: string;
}

export interface MeetingEdgePayload {
  summary: string;
  rolling_summary?: string | null;
  questions: string[];
  points: string[];
  concepts: MeetingEdgeConcept[];
  concept_history?: MeetingEdgeConcept[];
  context_level?: number;
  generated_at?: string;
  source_segment_count?: number;
  source_word_count?: number;
  source_last_end?: number;
}

export interface Transcript extends BaseDBModel {
  recording_id: RecordingId;
  text?: string;
  segments: TranscriptSegment[];
  notes?: string;
  user_notes?: string | null;
  meeting_edge_focus?: string | null;
  meeting_edge_payload?: MeetingEdgePayload | null;
  meeting_edge_status?: string;
  meeting_edge_error_message?: string | null;
  speaker_name_suggestions?: SpeakerNameSuggestion[];
  notes_status?: string; // pending, generating, completed, error
  transcript_status?: string; // pending, processing, completed, error
  error_message?: string;
}

export interface Tag extends BaseDBModel {
  name: string;
  color?: string;
  parent_id?: number;
  children?: Tag[];
}

export interface UserTask extends BaseDBModel {
  title: string;
  body?: string | null;
  due_at?: string | null;
  completed_at?: string | null;
  archived_at?: string | null;
  tags?: Tag[];
  linked_recordings?: UserTaskRecordingLink[];
}

export interface UserTaskRecordingLink {
  id: RecordingId;
  name: string;
  created_at: string;
  duration_seconds?: number | null;
  status: RecordingStatus;
  is_archived: boolean;
  is_deleted: boolean;
}

export interface CalendarEventLink {
  id: number;
  title: string;
  starts_at: string | null;
  ends_at: string | null;
}

export interface Recording extends Omit<BaseDBModel, "id"> {
  id: RecordingId;
  name: string;
  meeting_uid: string;
  audio_path: string;
  has_proxy?: boolean;
  duration_seconds?: number;
  file_size_bytes?: number;
  status: RecordingStatus;
  client_status?: ClientStatus;
  upload_progress?: number;
  processing_progress?: number;
  processing_step?: string;
  processing_eta_seconds?: number | null;
  processing_eta_learning?: boolean;
  processing_eta_sample_size?: number;
  /** Optional upper bound on diarized speakers. null means auto-detect. */
  max_speakers?: number | null;
  is_archived: boolean;
  is_deleted: boolean;
  transcript?: Transcript;
  speakers?: RecordingSpeaker[];
  tags?: Tag[];
  calendar_event?: CalendarEventLink | null;
}

export interface RecordingInitResponse {
  id: RecordingId;
  name: string;
  upload_token?: string | null;
}

export interface RecordingCaptureLifecycleResponse {
  recording_id: RecordingId;
  status: RecordingStatus;
  last_sequence: number;
}

export interface ActiveRecordingConflictDetail {
  code: "active_recording_exists";
  message: string;
  recording_id: RecordingId;
  status: RecordingStatus;
}

export interface Settings {
  whisper_model_size?: string;
  transcription_backend?: string;
  parakeet_model?: string;
  canary_model?: string;
  transcription_language?: string;
  notes_language?: string;
  notes_language_custom_instruction?: string;
  enable_live_transcription?: boolean;
  enable_meeting_edge?: boolean;
  meeting_edge_context_level?: number;
  processing_device?: string;
  theme?: string;
  timezone?: string;
  llm_provider?: string;
  // Per-user AI routing. "cli_oauth" routes through the user's own subscription
  // (Claude or ChatGPT, selected by cli_provider); the legacy "ollama"/"byok"
  // values are no-ops kept only for back-compat (they resolve via the
  // install-wide llm_provider unchanged).
  usage_model?: "ollama" | "byok" | "cli_oauth" | null;
  cli_provider?: CliProvider | null;
  cli_model?: string | null; // Claude CLI model (async tasks)
  cli_live_model?: string | null; // Claude CLI Meeting Edge model
  codex_model?: string | null; // Codex CLI model (async tasks)
  codex_live_model?: string | null; // Codex CLI Meeting Edge model
  gemini_api_key?: string;
  openai_api_key?: string;
  anthropic_api_key?: string;
  gemini_model?: string;
  gemini_live_model?: string | null;
  openai_model?: string;
  openai_live_model?: string | null;
  anthropic_model?: string;
  anthropic_live_model?: string | null;
  ollama_model?: string;
  ollama_live_model?: string | null;
  ollama_api_url?: string;
  ollama_context_window?: number;
  secondary_llm_provider?: string | null;
  secondary_gemini_model?: string | null;
  secondary_gemini_live_model?: string | null;
  secondary_openai_model?: string | null;
  secondary_openai_live_model?: string | null;
  secondary_anthropic_model?: string | null;
  secondary_anthropic_live_model?: string | null;
  secondary_ollama_model?: string | null;
  secondary_ollama_live_model?: string | null;
  secondary_ollama_api_url?: string;
  secondary_ollama_context_window?: number;
  secondary_gemini_api_key?: string;
  secondary_openai_api_key?: string;
  secondary_anthropic_api_key?: string;
  hf_token?: string;
  worker_url?: string;
  enable_auto_voiceprints?: boolean;
  prefer_short_titles?: boolean;
  // Notes structure and vocabulary. The install_* keys are admin-managed and are
  // read-only for everyone else; the API drops writes to them from non-admins.
  notes_template_id?: number | null;
  install_notes_template_id?: number | null;
  glossary_terms?: string;
  install_glossary_terms?: string;
  enable_vad?: boolean;
  enable_diarization?: boolean;
  spellcheck_language?: string;

    [key: string]: unknown;
}

/** Subscription-CLI providers a user can route inference through. */
export type CliProvider = "claude_code" | "codex";

export interface CliOAuthProviderStatus {
  provider: CliProvider;
  connected: boolean;
  status: string;
  token_expires_at?: string | null;
  connected_at?: string | null;
  usage_limited_until?: string | null;
  // This user's recorded token usage for THIS provider (input + output).
  tokens_7d?: number | null;
  tokens_total?: number | null;
}

export interface CliOAuthStatus {
  // One entry per supported provider (connected or not), in display order.
  providers: CliOAuthProviderStatus[];
  // This user's own recorded CLI token usage across providers (input + output).
  tokens_7d?: number | null;
  tokens_total?: number | null;
}

/** Result of POST /cli-oauth/start — either a paste-code URL (Claude) or a
 * device grant (Codex). Which fields are set depends on `kind`. */
export interface CliOAuthStart {
  provider: CliProvider;
  kind: "paste_code" | "device";
  authorize_url?: string | null;
  verification_uri?: string | null;
  verification_uri_complete?: string | null;
  user_code?: string | null;
  interval?: number | null;
  expires_in?: number | null;
}

/** Result of POST /cli-oauth/poll — the device-flow progress signal (Codex). The
 * URL + code arrive here (not from /start) once the worker has them. */
export interface CliOAuthPoll {
  provider: CliProvider;
  status: "pending" | "connected" | "expired";
  verification_uri?: string | null;
  user_code?: string | null;
}

/** One user's CLI usage + quota status for the admin overview table. */
export interface CliUsageRow {
  user_id: number;
  username: string;
  connected: boolean;
  tokens_total: number;
  tokens_7d: number;
  tokens_30d: number;
  requests_total: number;
  last_used_on?: string | null;
  rate_limit_status?: string | null;
  rate_limit_type?: string | null;
  utilization?: number | null;
  usage_limited_until?: string | null;
}

export interface CliUsageOverview {
  items: CliUsageRow[];
  total: number;
}

export interface LanguageChoice {
  code: string;
  label: string;
  forced_engines?: string[];
}

export interface TranscriptionEngineLanguageCapability {
  forced_language: boolean;
  guidance: string;
}

export interface LanguageRegistry {
  transcription_languages: LanguageChoice[];
  notes_languages: LanguageChoice[];
  custom_instruction_max_length: number;
  engine_capabilities: Record<
    "whisper" | "canary" | "parakeet",
    TranscriptionEngineLanguageCapability
  >;
}

export type CalendarProvider = "google" | "microsoft";

export type CalendarSyncStatus =
  | "idle"
  | "syncing"
  | "success"
  | "error"
  | "reauthorisation_required";

export type CalendarDashboardState =
  | "ready"
  | "provider_not_configured"
  | "no_accounts"
  | "no_selected_calendars"
  | "sync_in_progress"
  | "no_events";

export interface CalendarProviderStatus {
  provider: CalendarProvider;
  display_name: string;
  configured: boolean;
  source: string;
  enabled: boolean;
  redirect_uri: string;
  client_id?: string | null;
  tenant_id?: string | null;
  has_client_secret: boolean;
  push_enabled: boolean;
  push_notification_url?: string | null;
}

export interface CalendarProviderAvailability {
  provider: CalendarProvider;
  display_name: string;
  configured: boolean;
}

export interface CalendarSource {
  id: number;
  provider_calendar_id: string;
  name: string;
  description?: string | null;
  time_zone?: string | null;
  colour?: string | null;
  provider_colour?: string | null;
  custom_colour?: string | null;
  is_primary: boolean;
  is_read_only: boolean;
  is_selected: boolean;
  last_synced_at?: string | null;
}

export interface CalendarConnection {
  id: number;
  provider: CalendarProvider;
  email?: string | null;
  display_name?: string | null;
  sync_status: CalendarSyncStatus;
  sync_error?: string | null;
  last_sync_started_at?: string | null;
  last_sync_completed_at?: string | null;
  last_synced_at?: string | null;
  selected_calendar_count: number;
  push_active: boolean;
  calendars: CalendarSource[];
}

export interface CalendarOverview {
  providers: CalendarProviderAvailability[];
  connections: CalendarConnection[];
}

export interface CalendarProviderConfigUpdate {
  client_id?: string | null;
  client_secret?: string | null;
  tenant_id?: string | null;
  enabled?: boolean;
  clear_client_secret?: boolean;
  push_enabled?: boolean;
}

export interface CalendarDashboardDayCount {
  date: string;
  count: number;
}

export interface CalendarDashboardTag {
  id: number;
  name: string;
  color?: string | null;
}

export interface CalendarDashboardRecording {
  id: RecordingId;
  name: string;
  starts_at: string;
  ends_at?: string | null;
  duration_seconds?: number | null;
  status: RecordingStatus;
  speaker_names: string[];
  tags: CalendarDashboardTag[];
}

export interface CalendarDashboardEvent {
  id: number;
  title: string;
  provider: CalendarProvider;
  calendar_id: number;
  calendar_name: string;
  calendar_colour?: string | null;
  account_label?: string | null;
  location?: string | null;
  meeting_url?: string | null;
  meeting_url_trusted: boolean;
  meeting_url_host?: string | null;
  is_all_day: boolean;
  starts_at?: string | null;
  ends_at?: string | null;
  start_date?: string | null;
  end_date?: string | null;
  linked_recordings: CalendarDashboardRecording[];
}

export interface CalendarDashboardSummary {
  month: string;
  timezone: string;
  state: CalendarDashboardState;
  provider_configured: boolean;
  is_syncing: boolean;
  connection_count: number;
  selected_calendar_count: number;
  last_synced_at?: string | null;
  day_counts: CalendarDashboardDayCount[];
  agenda_items: CalendarDashboardEvent[];
  recording_items: CalendarDashboardRecording[];
  next_event?: CalendarDashboardEvent | null;
}

export interface RecordingsCalendar {
  month: string;
  timezone: string;
  day_counts: CalendarDashboardDayCount[];
}

export interface ChatMessage extends BaseDBModel {
  recording_id: RecordingId;
  user_id: number;
  role: "user" | "assistant";
  content: string;
}

// Voiceprint-related types
export interface VoiceprintMatchInfo {
  id: number;
  name: string;
  similarity_score: number;
  is_strong_match: boolean;
}

export interface VoiceprintExtractResult {
  embedding_extracted: boolean;
  matched_speaker: VoiceprintMatchInfo | null;
  all_global_speakers: Array<{
    id: number;
    name: string;
    has_voiceprint: boolean;
  }>;
  speaker_id: number;
  diarization_label: string;
}

export interface VoiceprintApplyResult {
  success: boolean;
  has_voiceprint: boolean;
  matched_speaker: { id: number; name: string } | null;
  message: string | null;
}

export interface BatchVoiceprintResult {
  diarization_label: string;
  speaker_name: string;
  speaker_id?: number;
  success: boolean;
  error?: string;
  matched_speaker?: VoiceprintMatchInfo | null;
}

export interface BatchVoiceprintResponse {
  speakers_processed: number;
  results: BatchVoiceprintResult[];
  all_global_speakers: Array<{
    id: number;
    name: string;
    has_voiceprint: boolean;
  }>;
}

export interface AudioDevice {
  name: string;
  is_default: boolean;
}

export interface ModelStatusInfo {
  downloaded: boolean;
  path: string | null;
  checked_paths: string[];
  source?: string;
}

export interface SystemModelStatus {
  whisper: ModelStatusInfo;
  pyannote: ModelStatusInfo;
  embedding: ModelStatusInfo;
  [key: string]: ModelStatusInfo;
}

/** Which local model assets a preparation request should fetch. `active` covers
 * whatever the saved transcription settings need. */
export type ModelPreparationTarget = "active" | "core" | "parakeet" | "canary";

export interface ModelPreparationResponse {
  task_id: string;
  target: ModelPreparationTarget;
  status: string;
}

export interface DownloadProgress {
  progress: number;
  message: string;
  speed?: string | null;
  eta?: string | null;
  status: "idle" | "downloading" | "complete" | "error";
  stage?: string | null;
  in_progress: boolean;
}

export type AdminHealthCheckStatus =
  | "ok"
  | "warning"
  | "error"
  | "disabled"
  | "info"
  | "unknown";

export interface AdminHealthCheck {
  status: AdminHealthCheckStatus;
  label: string;
  detail: string;
  action?: string | null;
  [key: string]: unknown;
}

export interface DeploymentWarning {
  code: string;
  key: string;
  title: string;
  message: string;
}

/** Admin view of anonymous telemetry state. See docs/TELEMETRY.md. */
export interface TelemetryStatus {
  enabled: boolean;
  /** Pinned by NOJOIN_TELEMETRY_ENABLED; the UI toggle is read-only when true. */
  managed_by_env: boolean;
  notice_acknowledged: boolean;
  notice_pending: boolean;
  notice_first_shown_at: string | null;
  consent_granted: boolean;
  install_id: string;
  endpoint: string;
  last_sent_at: string | null;
  grace_period_days: number;
}

export interface AdminHealthSummary {
  pipeline_status: "ready" | "degraded" | "blocked";
  message: string;
  blocking_reasons: string[];
  degraded_reasons: string[];
}

export interface AdminHealthStatus {
  status: "ok" | "warning" | "error";
  version: string;
  summary: AdminHealthSummary;
  checks: {
    database: AdminHealthCheck;
    queue: AdminHealthCheck;
    worker: AdminHealthCheck;
    ffmpeg: AdminHealthCheck;
    storage: AdminHealthCheck;
    transcription_model: AdminHealthCheck;
    diarization: AdminHealthCheck;
    device: AdminHealthCheck;
    optional_ai: AdminHealthCheck;
  };
  download: {
    in_progress: boolean;
    status: string | null;
    stage: string | null;
    message: string | null;
    progress: number | null;
  };
}

export interface ReleaseAsset {
  name: string;
  browser_download_url: string;
  content_type: string | null;
  size: number | null;
}

export interface ReleaseInfo {
  version: string;
  tag_name: string;
  name: string | null;
  html_url: string;
  published_at: string | null;
  body: string | null;
  draft: boolean;
  prerelease: boolean;
  assets: ReleaseAsset[];
}

export type UpdateStatus =
  | "current"
  | "update-available"
  | "ahead"
  | "unknown";

export interface VersionInfo {
  current_version: string;
  latest_version: string | null;
  is_update_available: boolean;
  update_status: UpdateStatus;
  release_url: string | null;
  current_release_url?: string | null;
  latest_published_at?: string | null;
  release_source?: string;
  releases: ReleaseInfo[];
}

export interface SpeakerSegment {
  recording_id: RecordingId;
  recording_name: string | null;
  recording_date: string | null;
  start: number;
  end: number;
  text: string;
}

export interface SegmentSelection {
  recording_id: RecordingId;
  start: number;
  end: number;
}

export interface ReprocessRequest {
  transcription_backend: "whisper" | "parakeet" | "canary";
  whisper_model_size?: string;
  parakeet_model?: string;
  canary_model?: string;
  /**
   * Omit to keep the recording's existing cap; null clears it back to
   * auto-detect; a number sets it.
   */
  max_speakers?: number | null;
}

export interface AudioFileMetadata {
  format?: string;
  bitrate?: number;
  channels?: number;
  size?: number;
}

export interface RecordingInfo {
  original: AudioFileMetadata | null;
  proxy: AudioFileMetadata | null;
}

export interface AsyncTaskStatus {
  task_id: string;
  status: string;
    result?: unknown;
    meta?: unknown;
}

export interface AxiosErrorLike {
  response?: {
    data?: {
      detail?: string;
    };
    status?: number;
  };
  message?: string;
}
