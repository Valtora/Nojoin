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
  text_confidence?: number | null;
  speaker_assignment_source?: string;
  speaker_assignment_authority?: "provisional" | "finalized" | "manual" | string;
  updated_at?: string | null;
  speaker_state_source?: string;
  live_source_speaker?: string | null;
  live_source_speakers?: string[];
  source_public_ids?: string[];
  live_reuse_alignment?: Record<string, any>;
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
  text_confidence?: number | null;
  speaker_assignment_source?: string;
  speaker_assignment_authority?: "provisional" | "finalized" | "manual" | string;
  updated_at?: string | null;
  speaker_state_source?: string;
  live_source_speaker?: string | null;
  live_source_speakers?: string[];
  source_public_ids?: string[];
  live_reuse_alignment?: Record<string, any>;
}

export interface MeetingEdgeConcept {
  term: string;
  explanation: string;
}

export interface MeetingEdgePayload {
  summary: string;
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
  enable_live_transcription?: boolean;
  enable_meeting_edge?: boolean;
  meeting_edge_context_level?: number;
  processing_device?: string;
  theme?: string;
  timezone?: string;
  llm_provider?: string;
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
  hf_token?: string;
  worker_url?: string;
  enable_auto_voiceprints?: boolean;
  prefer_short_titles?: boolean;
  enable_vad?: boolean;
  enable_diarization?: boolean;
  spellcheck_language?: string;

  [key: string]: any;
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

export interface DownloadProgress {
  status: string;
  progress: number;
  model_name: string;
  file_name: string;
  downloaded_bytes: number;
  total_bytes: number;
  in_progress: boolean;
  message?: string;
  stage?: string;
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
}

export interface SystemModelStatus {
  whisper: ModelStatusInfo;
  pyannote: ModelStatusInfo;
  embedding: ModelStatusInfo;
  [key: string]: ModelStatusInfo;
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
}
