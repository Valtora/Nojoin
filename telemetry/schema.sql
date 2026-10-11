-- Nojoin telemetry ingest schema (Cloudflare D1).
--
-- Apply with:
--   npx wrangler d1 execute nojoin-telemetry --remote --file=./schema.sql
--
-- The primary key is the whole dedupe strategy for both tables: one row per
-- install per UTC day, upserted. A retried or duplicated request rewrites its
-- own row instead of appending, so neither a network retry nor a restarted
-- worker can inflate the install count, and the write cost stays at one row per
-- install per day.
--
-- Nothing is ever deleted. Rows are kept indefinitely so trends can be followed
-- over years; see ../README.md for the storage ceiling that implies.

-- The install heartbeat: sent on first start, on each new version, and daily.
-- It holds the install id and the version and nothing else, by design: this is
-- the part of telemetry an operator cannot switch off in the app, so the ingest
-- discards any other field rather than keeping a raw payload (src/payload.ts).
CREATE TABLE IF NOT EXISTS heartbeats (
  install_id  TEXT    NOT NULL,
  day         TEXT    NOT NULL,   -- UTC date, derived server-side
  version     TEXT,
  received_at INTEGER NOT NULL,   -- epoch ms, server-side

  PRIMARY KEY (install_id, day)
);

CREATE INDEX IF NOT EXISTS idx_heartbeats_day ON heartbeats (day);

-- The optional usage diagnostics.

CREATE TABLE IF NOT EXISTS pings (
  install_id           TEXT    NOT NULL,
  day                  TEXT    NOT NULL,   -- UTC date, derived server-side
  received_at          INTEGER NOT NULL,   -- epoch ms, server-side
  schema_version       INTEGER NOT NULL,

  version              TEXT,
  install_age_days     INTEGER,
  local_origin         INTEGER,

  users_total          INTEGER,
  users_recording_28d  INTEGER,
  recordings_total     INTEGER,
  recordings_28d       INTEGER,
  recording_hours_28d  REAL,

  llm_provider         TEXT,
  secondary_configured INTEGER,
  cli_oauth_in_use     INTEGER,
  meeting_edge_enabled INTEGER,

  asr_engine           TEXT,
  whisper_model_size   TEXT,
  gpu                  INTEGER,

  calendar_connected   INTEGER,
  mcp_in_use           INTEGER,
  chat_used_28d        INTEGER,
  documents_used       INTEGER,
  tasks_used           INTEGER,
  people_library_used  INTEGER,

  -- The full accepted request body. Fields a newer Nojoin sends before this
  -- Worker knows about them are captured here from day one and can be queried
  -- retroactively with json_extract, so a client release never has to wait on
  -- an ingest deploy.
  payload              TEXT    NOT NULL,

  PRIMARY KEY (install_id, day)
);

-- Supports the daily-count queries, which filter on day across all installs.
CREATE INDEX IF NOT EXISTS idx_pings_day ON pings (day);
