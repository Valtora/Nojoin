export const GENERAL_KEYWORDS = ['appearance', 'theme', 'light', 'dark', 'mode', 'color', 'spellcheck', 'spell', 'language', 'dictionary', 'timezone', 'time zone', 'date', 'time', 'clock', 'utc', 'gmt', 'bst'];

export const AI_KEYWORDS = [
  'provider', 'llm', 'gemini', 'openai', 'anthropic', 'model', 'ai', 
  'api key', 'gpt', 'claude', 'hugging face', 'token', 'diarization', 
  'pyannote', 'speaker', 'separation', 'voiceprint', 'auto-create', 
  'identification', 'recognition', 'whisper', 'transcription', 'speech to text'
];

export const AUDIO_KEYWORDS = [
  'device', 'input', 'output', 'microphone', 'speaker', 'audio', 'capture', 'playback',
  'warning', 'warnings', 'dismiss', 'quiet', 'silence', 'reset warnings',
  'meeting length', 'minimum meeting length', 'recording length', 'loopback'
];

export const COMPANION_KEYWORDS = [
  'companion', 'companion app', 'pair', 'pairing', 'pairing code',
  'manual pairing', 'connect', 'connection', 'disconnect', 'backend switch',
  'switch backend', 'reconnect', 'repair', 'repairing', 'version mismatch',
  'temporarily disconnected', 'browser repair required', 'browser repair in progress',
  'download companion', 'installer', 'update companion',
  'status', 'local recording', 'firefox', 'windows roots', 'enterprise roots',
  'certificate trust', 'security.enterprise_roots.enabled', ...AUDIO_KEYWORDS
];

export const SYSTEM_KEYWORDS = [
  'infrastructure', 'worker', 'redis', 'url', 'broker', 'connection', 
  'companion', 'app', 'backend', 'api', 'port', 'address'
];

export const ACCOUNT_KEYWORDS = [
  'profile', 'username', 'password', 'change password', 'account', 'user',
  'calendar', 'calendars', 'agenda', 'events', 'gmail', 'google', 'outlook', 'microsoft'
];

export const UPDATES_KEYWORDS = [
  'update', 'updates', 'release', 'releases', 'version', 'versions',
  'latest', 'upgrade', 'changelog', 'release notes', 'installer',
  'download', 'github', 'companion'
];

export const ADMIN_KEYWORDS = [
  'admin', 'users', 'manage', 'create user', 'delete user', 'role', 'superuser',
  'calendar provider', 'oauth', 'gmail', 'google', 'outlook', 'microsoft'
];

export const INVITES_KEYWORDS = [
  'invite', 'invitation', 'link', 'code', 'join', 'register', 'create invite', 'revoke'
];

export const TAB_KEYWORDS: Record<string, string[]> = {
  general: GENERAL_KEYWORDS,
  ai: AI_KEYWORDS,
  companion: COMPANION_KEYWORDS,
  updates: UPDATES_KEYWORDS,
  system: SYSTEM_KEYWORDS,
  account: ACCOUNT_KEYWORDS,
  admin: ADMIN_KEYWORDS,
  invites: INVITES_KEYWORDS,
};
