/**
 * Nojoin anonymous telemetry ingest.
 *
 * Accepts one POST /v1/ping per install per day and stores it in D1. See
 * ../README.md for the deployment runbook and ../../docs/TELEMETRY.md for the
 * user-facing disclosure this code has to remain true to.
 *
 * Deliberate non-features:
 *   - Nothing derived from the connection is stored. No IP, no User-Agent, no
 *     geo. The only server-side addition is `received_at`.
 *   - No response body. The ingest is one-way; it must never become a channel
 *     that tells an install something, because that would give operators a
 *     reason to keep telemetry on that has nothing to do with consent.
 */

import { dayBucket, parsePayload } from "./payload";
import { retentionCutoff, rollupStatements } from "./rollup";

export interface Env {
  DB: D1Database;
  PING_LIMITER: RateLimit;
}

/** Bodies larger than this are not plausible for the documented payload. */
const MAX_BODY_BYTES = 8192;

const INSERT_SQL = `
INSERT INTO pings (
  install_id, day, received_at, schema_version, version, install_age_days,
  local_origin, users_total, users_recording_28d, recordings_total,
  recordings_28d, recording_hours_28d, llm_provider, secondary_configured,
  cli_oauth_in_use, meeting_edge_enabled, asr_engine, whisper_model_size, gpu,
  calendar_connected, mcp_in_use, chat_used_28d, documents_used, tasks_used,
  people_library_used, payload
) VALUES (
  ?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15, ?16, ?17,
  ?18, ?19, ?20, ?21, ?22, ?23, ?24, ?25, ?26
)
ON CONFLICT (install_id, day) DO UPDATE SET
  received_at = excluded.received_at,
  schema_version = excluded.schema_version,
  version = excluded.version,
  install_age_days = excluded.install_age_days,
  local_origin = excluded.local_origin,
  users_total = excluded.users_total,
  users_recording_28d = excluded.users_recording_28d,
  recordings_total = excluded.recordings_total,
  recordings_28d = excluded.recordings_28d,
  recording_hours_28d = excluded.recording_hours_28d,
  llm_provider = excluded.llm_provider,
  secondary_configured = excluded.secondary_configured,
  cli_oauth_in_use = excluded.cli_oauth_in_use,
  meeting_edge_enabled = excluded.meeting_edge_enabled,
  asr_engine = excluded.asr_engine,
  whisper_model_size = excluded.whisper_model_size,
  gpu = excluded.gpu,
  calendar_connected = excluded.calendar_connected,
  mcp_in_use = excluded.mcp_in_use,
  chat_used_28d = excluded.chat_used_28d,
  documents_used = excluded.documents_used,
  tasks_used = excluded.tasks_used,
  people_library_used = excluded.people_library_used,
  payload = excluded.payload
`;

function status(code: number): Response {
  return new Response(null, { status: code });
}

async function handlePing(request: Request, env: Env): Promise<Response> {
  // Keyed on the connecting address purely as a flood guard. The value is used
  // for the limiter decision and is never stored.
  const clientIp = request.headers.get("CF-Connecting-IP") ?? "unknown";
  const { success } = await env.PING_LIMITER.limit({ key: clientIp });
  if (!success) {
    return status(429);
  }

  const declaredLength = Number(request.headers.get("content-length") ?? "0");
  if (declaredLength > MAX_BODY_BYTES) {
    return status(413);
  }

  const raw = await request.text();
  if (raw.length > MAX_BODY_BYTES) {
    return status(413);
  }

  let body: unknown;
  try {
    body = JSON.parse(raw);
  } catch {
    return status(400);
  }

  const parsed = parsePayload(body);
  if (!parsed.ok) {
    return status(400);
  }

  const receivedAt = Date.now();
  const row = parsed.row;

  await env.DB.prepare(INSERT_SQL)
    .bind(
      row.install_id,
      dayBucket(receivedAt),
      receivedAt,
      row.schema_version,
      row.version,
      row.install_age_days,
      row.local_origin,
      row.users_total,
      row.users_recording_28d,
      row.recordings_total,
      row.recordings_28d,
      row.recording_hours_28d,
      row.llm_provider,
      row.secondary_configured,
      row.cli_oauth_in_use,
      row.meeting_edge_enabled,
      row.asr_engine,
      row.whisper_model_size,
      row.gpu,
      row.calendar_connected,
      row.mcp_in_use,
      row.chat_used_28d,
      row.documents_used,
      row.tasks_used,
      row.people_library_used,
      row.payload,
    )
    .run();

  return status(204);
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    if (url.pathname !== "/v1/ping") {
      return status(404);
    }
    if (request.method !== "POST") {
      return status(405);
    }

    try {
      return await handlePing(request, env);
    } catch {
      // A failure here must not be diagnosable from outside: the ingest is
      // public, and an error body is free reconnaissance. Detail goes to the
      // Workers log instead, which observability can query.
      console.error("ping failed");
      return status(500);
    }
  },

  async scheduled(_controller: ScheduledController, env: Env): Promise<void> {
    const cutoff = retentionCutoff(Date.now());
    const statements = rollupStatements().map((sql) =>
      env.DB.prepare(sql).bind(cutoff),
    );
    await env.DB.batch(statements);
    console.log(`rolled up and pruned pings before ${cutoff}`);
  },
} satisfies ExportedHandler<Env>;
