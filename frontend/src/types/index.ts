export enum RecordingStatus {
  UPLOADING = "UPLOADING",
  RECORDED = "RECORDED",
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
  hf_token?: string;
  worker_url?: string;
  companion_url?: string;
  enable_auto_voiceprints?: boolean;
  [key: string]: any;
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
