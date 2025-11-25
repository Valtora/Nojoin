export enum RecordingStatus {
  UPLOADING = "UPLOADING",
  RECORDED = "RECORDED",
  PROCESSING = "PROCESSING",
  PROCESSED = "PROCESSED",
  ERROR = "ERROR",
}

export interface BaseDBModel {
  id: number;
  created_at: string;
  updated_at: string;
}

export interface GlobalSpeaker extends BaseDBModel {
  name: string;
}

export interface RecordingSpeaker extends BaseDBModel {
  recording_id: number;
  global_speaker_id?: number;
  diarization_label: string;
  name?: string;
  snippet_start?: number;
  snippet_end?: number;
  voice_snippet_path?: string;
  global_speaker?: GlobalSpeaker;
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
}

export interface Recording extends BaseDBModel {
  name: string;
  audio_path: string;
  duration_seconds?: number;
  file_size_bytes?: number;
  status: RecordingStatus;
  speakers?: RecordingSpeaker[];
  transcript?: Transcript;
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
  [key: string]: any;
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
