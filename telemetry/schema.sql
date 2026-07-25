-- Nojoin telemetry ingest schema (Cloudflare D1).
--
-- Apply with:
--   npx wrangler d1 execute nojoin-telemetry --remote --file=./schema.sql
--
-- The primary key is the whole dedupe strategy: one row per install per UTC
-- day, upserted. A retried or duplicated ping rewrites its own row instead of
-- appending, so neither a network retry nor a restarted worker can inflate the
-- install count, and the write cost stays at one row per install per day.

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

-- Supports both the daily-count queries and the retention sweep's `day <`
-- filter, so the roll-up scans only the rows it is about to remove.
CREATE INDEX IF NOT EXISTS idx_pings_day ON pings (day);

-- Aggregates that outlive the raw rows (see src/rollup.ts).
CREATE TABLE IF NOT EXISTS daily_rollup (
  day                     TEXT PRIMARY KEY,
  installs                INTEGER NOT NULL,
  users_total_sum         INTEGER NOT NULL,
  users_recording_28d_sum INTEGER NOT NULL,
  recordings_total_sum    INTEGER NOT NULL,
  recordings_28d_sum      INTEGER NOT NULL,
  recording_hours_28d_sum REAL    NOT NULL
);

-- Generic breakdown store, so version and feature splits survive the deletion
-- of raw rows without needing a column per dimension.
CREATE TABLE IF NOT EXISTS daily_rollup_dim (
  day       TEXT    NOT NULL,
  dimension TEXT    NOT NULL,
  value     TEXT    NOT NULL,
  installs  INTEGER NOT NULL,
  PRIMARY KEY (day, dimension, value)
);
