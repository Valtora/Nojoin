export const GENERAL_KEYWORDS = ['appearance', 'theme', 'light', 'dark', 'mode', 'color'];

export const AI_KEYWORDS = [
  'provider', 'llm', 'gemini', 'openai', 'anthropic', 'model', 'ai', 
  'api key', 'gpt', 'claude', 'hugging face', 'token', 'diarization', 
  'pyannote', 'speaker', 'separation', 'voiceprint', 'auto-create', 
  'identification', 'recognition', 'whisper', 'transcription', 'speech to text'
];

export const AUDIO_KEYWORDS = [
  'device', 'input', 'output', 'microphone', 'speaker', 'audio', 'capture', 'playback'
];

export const SYSTEM_KEYWORDS = [
  'infrastructure', 'worker', 'redis', 'url', 'broker', 'connection', 
  'companion', 'app', 'backend', 'api', 'port', 'address'
];

export const TAB_KEYWORDS: Record<string, string[]> = {
  general: GENERAL_KEYWORDS,
  ai: AI_KEYWORDS,
  audio: AUDIO_KEYWORDS,
  system: SYSTEM_KEYWORDS,
};
