/**
 * Retention roll-up.
 *
 * Raw per-install rows are kept for 13 months, then collapsed into daily
 * aggregates and deleted. The extra month beyond a year is deliberate: it keeps
 * a like-for-like comparison against the same month last year available right
 * up to the moment the raw rows age out.
 *
 * The aggregates are split in two so the breakdowns survive the deletion:
 * `daily_rollup` holds the totals, and `daily_rollup_dim` holds a generic
 * (dimension, value) -> install count, which preserves version and feature
 * splits without needing a column per dimension.
 *
 * Kept free of Worker runtime APIs so it can be unit tested on plain Node.
 */

export const RETENTION_DAYS = 395;

/** Dimensions preserved as (day, dimension, value, installs) after raw rows go. */
const TEXT_DIMENSIONS = [
  "version",
  "llm_provider",
  "asr_engine",
  "whisper_model_size",
] as const;

const BOOLEAN_DIMENSIONS = [
  "local_origin",
  "gpu",
  "secondary_configured",
  "cli_oauth_in_use",
  "meeting_edge_enabled",
  "calendar_connected",
  "mcp_in_use",
  "chat_used_28d",
  "documents_used",
  "tasks_used",
  "people_library_used",
] as const;

/** The oldest day that stays raw. Everything strictly before it is rolled up. */
export function retentionCutoff(nowMs: number, retentionDays = RETENTION_DAYS): string {
  return new Date(nowMs - retentionDays * 86_400_000).toISOString().slice(0, 10);
}

/**
 * SQL for the roll-up, in execution order. Every statement takes the cutoff day
 * as its only binding, so the caller can run them as one batch.
 *
 * `INSERT OR REPLACE` rather than plain `INSERT` keeps the pass idempotent: a
 * retried or overlapping run recomputes the same day to the same value instead
 * of doubling it.
 */
export function rollupStatements(): string[] {
  const statements: string[] = [
    `INSERT OR REPLACE INTO daily_rollup (
       day, installs, users_total_sum, users_recording_28d_sum,
       recordings_total_sum, recordings_28d_sum, recording_hours_28d_sum
     )
     SELECT day,
            COUNT(*),
            COALESCE(SUM(users_total), 0),
            COALESCE(SUM(users_recording_28d), 0),
            COALESCE(SUM(recordings_total), 0),
            COALESCE(SUM(recordings_28d), 0),
            COALESCE(SUM(recording_hours_28d), 0)
       FROM pings
      WHERE day < ?1
      GROUP BY day`,
  ];

  for (const dimension of TEXT_DIMENSIONS) {
    statements.push(
      `INSERT OR REPLACE INTO daily_rollup_dim (day, dimension, value, installs)
       SELECT day, '${dimension}', COALESCE(${dimension}, 'unknown'), COUNT(*)
         FROM pings
        WHERE day < ?1
        GROUP BY day, COALESCE(${dimension}, 'unknown')`,
    );
  }

  for (const dimension of BOOLEAN_DIMENSIONS) {
    // NULL is preserved as its own 'unknown' bucket rather than folded into
    // 'false', so "the client did not report this" stays distinguishable from
    // "the client reported it off" after the raw rows are gone.
    const bucket = `CASE WHEN ${dimension} IS NULL THEN 'unknown' WHEN ${dimension} = 1 THEN 'true' ELSE 'false' END`;
    statements.push(
      `INSERT OR REPLACE INTO daily_rollup_dim (day, dimension, value, installs)
       SELECT day, '${dimension}', ${bucket}, COUNT(*)
         FROM pings
        WHERE day < ?1
        GROUP BY day, ${bucket}`,
    );
  }

  statements.push(`DELETE FROM pings WHERE day < ?1`);

  return statements;
}
