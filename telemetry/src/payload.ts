/**
 * Payload contract for the Nojoin telemetry ingest.
 *
 * This module is deliberately free of Worker runtime APIs so it can be unit
 * tested on plain Node. Everything the ingest decides about a request body
 * lives here.
 *
 * Two invariants shape the design:
 *
 * 1. An unknown field is never a reason to reject. A newer Nojoin will always
 *    ship fields before this Worker is redeployed, and rejecting them would
 *    turn a routine client release into an outage of our own telemetry. Unknown
 *    fields survive verbatim in the stored raw payload.
 * 2. An unknown *enum value* is stored as NULL in its typed column rather than
 *    rejected, for the same reason. The raw payload keeps the real value, so a
 *    later query can recover it retroactively.
 */

export const SUPPORTED_SCHEMA_VERSIONS = [1] as const;

/** Canonical UUID, matching the uuid4 the client mints. */
const INSTALL_ID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Generous cap; a version string longer than this is malformed, not a release. */
const MAX_VERSION_LENGTH = 32;
const VERSION_RE = /^[0-9A-Za-z.\-+]+$/;

const LLM_PROVIDERS = ["gemini", "openai", "anthropic", "ollama", "cli_oauth"];
const ASR_ENGINES = ["whisper", "parakeet", "canary"];
const WHISPER_MODEL_SIZES = ["turbo", "tiny", "base", "small", "medium", "large"];

/**
 * Upper bounds for the counters. These are not business rules — they exist so a
 * corrupt or forged payload cannot write an absurd value that then poisons every
 * aggregate query built on top of the table.
 */
const MAX_DAYS = 36_500; // ~100 years
const MAX_USERS = 1_000_000;
const MAX_RECORDINGS = 1_000_000_000;
const MAX_HOURS = 10_000_000;

export interface TelemetryRow {
  install_id: string;
  schema_version: number;
  version: string | null;
  install_age_days: number | null;
  local_origin: number | null;
  users_total: number | null;
  users_recording_28d: number | null;
  recordings_total: number | null;
  recordings_28d: number | null;
  recording_hours_28d: number | null;
  llm_provider: string | null;
  secondary_configured: number | null;
  cli_oauth_in_use: number | null;
  meeting_edge_enabled: number | null;
  asr_engine: string | null;
  whisper_model_size: string | null;
  gpu: number | null;
  calendar_connected: number | null;
  mcp_in_use: number | null;
  chat_used_28d: number | null;
  documents_used: number | null;
  tasks_used: number | null;
  people_library_used: number | null;
  payload: string;
}

export type ParseResult =
  | { ok: true; row: TelemetryRow }
  | { ok: false; error: string };

function asRecord(value: unknown): Record<string, unknown> | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return null;
  }
  return value as Record<string, unknown>;
}

/** SQLite has no boolean type; store 1/0 and leave anything else NULL. */
function boolCol(value: unknown): number | null {
  if (typeof value !== "boolean") {
    return null;
  }
  return value ? 1 : 0;
}

function intCol(value: unknown, max: number): number | null {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return null;
  }
  const rounded = Math.trunc(value);
  if (rounded < 0 || rounded > max) {
    return null;
  }
  return rounded;
}

function floatCol(value: unknown, max: number): number | null {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return null;
  }
  if (value < 0 || value > max) {
    return null;
  }
  // One decimal place is all the precision an hours figure needs, and it keeps
  // the stored value from implying accuracy the client never had.
  return Math.round(value * 10) / 10;
}

function enumCol(value: unknown, allowed: string[]): string | null {
  if (typeof value !== "string") {
    return null;
  }
  return allowed.includes(value) ? value : null;
}

function versionCol(value: unknown): string | null {
  if (typeof value !== "string") {
    return null;
  }
  const trimmed = value.trim();
  if (!trimmed || trimmed.length > MAX_VERSION_LENGTH || !VERSION_RE.test(trimmed)) {
    return null;
  }
  return trimmed;
}

/**
 * Validate a decoded request body into a storable row.
 *
 * Only two conditions reject outright: an unrecognised schema version (we would
 * be guessing at the meaning of every field) and a malformed install id (the
 * primary key, and the one value that must be trustworthy for dedupe to work).
 */
export function parsePayload(body: unknown): ParseResult {
  const record = asRecord(body);
  if (!record) {
    return { ok: false, error: "body must be a JSON object" };
  }

  const schema = record.schema;
  if (typeof schema !== "number" || !SUPPORTED_SCHEMA_VERSIONS.includes(schema as 1)) {
    return { ok: false, error: "unsupported schema version" };
  }

  const installId = record.install_id;
  if (typeof installId !== "string" || !INSTALL_ID_RE.test(installId)) {
    return { ok: false, error: "install_id must be a UUID" };
  }

  return {
    ok: true,
    row: {
      install_id: installId.toLowerCase(),
      schema_version: schema,
      version: versionCol(record.version),
      install_age_days: intCol(record.install_age_days, MAX_DAYS),
      local_origin: boolCol(record.local_origin),
      users_total: intCol(record.users_total, MAX_USERS),
      users_recording_28d: intCol(record.users_recording_28d, MAX_USERS),
      recordings_total: intCol(record.recordings_total, MAX_RECORDINGS),
      recordings_28d: intCol(record.recordings_28d, MAX_RECORDINGS),
      recording_hours_28d: floatCol(record.recording_hours_28d, MAX_HOURS),
      llm_provider: enumCol(record.llm_provider, LLM_PROVIDERS),
      secondary_configured: boolCol(record.secondary_configured),
      cli_oauth_in_use: boolCol(record.cli_oauth_in_use),
      meeting_edge_enabled: boolCol(record.meeting_edge_enabled),
      asr_engine: enumCol(record.asr_engine, ASR_ENGINES),
      whisper_model_size: enumCol(record.whisper_model_size, WHISPER_MODEL_SIZES),
      gpu: boolCol(record.gpu),
      calendar_connected: boolCol(record.calendar_connected),
      mcp_in_use: boolCol(record.mcp_in_use),
      chat_used_28d: boolCol(record.chat_used_28d),
      documents_used: boolCol(record.documents_used),
      tasks_used: boolCol(record.tasks_used),
      people_library_used: boolCol(record.people_library_used),
      payload: JSON.stringify(record),
    },
  };
}

/** UTC day bucket. Derived from server time so a skewed client clock cannot
 * write into the wrong day, or the future. */
export function dayBucket(receivedAtMs: number): string {
  return new Date(receivedAtMs).toISOString().slice(0, 10);
}
