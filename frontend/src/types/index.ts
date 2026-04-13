export enum RecordingStatus {
  UPLOADING = "UPLOADING",
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
  recording_id: number;
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

export interface TranscriptSpeakerAssignment {
  name: string;
  globalSpeakerId?: number;
  diarizationLabel?: string;
}

export type ExportContentType = "transcript" | "notes" | "both" | "audio";

export interface TranscriptSegment {
  start: number;
  end: number;
  text: string;
  speaker: string;
  overlapping_speakers?: string[];
}

export interface Transcript extends BaseDBModel {
  recording_id: number;
  text?: string;
  segments: TranscriptSegment[];
  notes?: string;
  user_notes?: string | null;
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
  due_at?: string | null;
  completed_at?: string | null;
}

export interface Recording extends BaseDBModel {
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
}

export interface Settings {
  whisper_model_size?: string;
  processing_device?: string;
  theme?: string;
  llm_provider?: string;
  gemini_api_key?: string;
  openai_api_key?: string;
  anthropic_api_key?: string;
  gemini_model?: string;
  openai_model?: string;
  anthropic_model?: string;
  ollama_model?: string;
  ollama_api_url?: string;
  hf_token?: string;
  worker_url?: string;
  companion_url?: string;
  enable_auto_voiceprints?: boolean;
  auto_generate_notes?: boolean;
  auto_generate_title?: boolean;
  prefer_short_titles?: boolean;
  auto_infer_speakers?: boolean;
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
}

export interface CalendarDashboardSummary {
  month: string;
  state: CalendarDashboardState;
  provider_configured: boolean;
  is_syncing: boolean;
  connection_count: number;
  selected_calendar_count: number;
  last_synced_at?: string | null;
  day_counts: CalendarDashboardDayCount[];
  agenda_items: CalendarDashboardEvent[];
  next_event?: CalendarDashboardEvent | null;
}

export interface ChatMessage extends BaseDBModel {
  recording_id: number;
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

export interface CompanionDevices {
  input_devices: AudioDevice[];
  output_devices: AudioDevice[];
  selected_input: string | null;
  selected_output: string | null;
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
  companion_download_url?: string | null;
  releases: ReleaseInfo[];
}

export interface SpeakerSegment {
  recording_id: number;
  recording_name: string | null;
  recording_date: string | null;
  start: number;
  end: number;
  text: string;
}

export interface SegmentSelection {
  recording_id: number;
  start: number;
  end: number;
}
