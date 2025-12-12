export enum RecordingStatus {
  UPLOADING = "UPLOADING",
  RECORDED = "RECORDED",
  QUEUED = "QUEUED",
  PROCESSING = "PROCESSING",
  PROCESSED = "PROCESSED",
  ERROR = "ERROR",
}

export enum ClientStatus {
  RECORDING = "RECORDING",
  PAUSED = "PAUSED",
  UPLOADING = "UPLOADING",
  IDLE = "IDLE",
}

export enum UserRole {
  OWNER = 'owner',
  ADMIN = 'admin',
  USER = 'user',
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

export interface GlobalSpeaker extends BaseDBModel {
  name: string;
  has_voiceprint?: boolean;
  recording_count?: number; // Number of recordings this speaker is associated with
  color?: string;
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
}

export interface TranscriptSegment {
  start: number;
  end: number;
  text: string;
  speaker: string;
}

export interface Transcript extends BaseDBModel {
  recording_id: number;
  text?: string;
  segments: TranscriptSegment[];
  notes?: string;
  notes_status?: string; // pending, generating, completed, error
  transcript_status?: string; // pending, processing, completed, error
  error_message?: string;
}

export interface Tag extends BaseDBModel {
  name: string;
  color?: string;
}

export interface Recording extends BaseDBModel {
  name: string;
  audio_path: string;
  duration_seconds?: number;
  file_size_bytes?: number;
  status: RecordingStatus;
  client_status?: ClientStatus;
  upload_progress?: number;
  processing_step?: string;
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
  chat_custom_instructions?: string;
  [key: string]: any;
}

export interface ChatMessage extends BaseDBModel {
  recording_id: number;
  user_id: number;
  role: 'user' | 'assistant';
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
  all_global_speakers: Array<{ id: number; name: string; has_voiceprint: boolean }>;
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
  all_global_speakers: Array<{ id: number; name: string; has_voiceprint: boolean }>;
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
