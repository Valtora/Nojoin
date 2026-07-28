import type { SettingsAutosaveSnapshot } from "./SettingsAutosaveState";

const AUTOSAVE_STATE_PRIORITY: Record<SettingsAutosaveSnapshot["status"], number> = {
  blocked: 4,
  error: 3,
  saving: 2,
  pending: 1,
  saved: 0,
};

/**
 * Collapses several autosave states into the one worth showing.
 *
 * Settings saves on two schedules — the shared settings object, and account
 * fields that write to their own endpoint — but the footer has room for one
 * message. The most urgent state wins, so a failure is never masked by a
 * neighbour reporting success.
 *
 * Category-matching for search used to live here too; it now belongs to the
 * settings registry, which matches individual settings rather than whole tabs.
 */
export function mergeAutosaveStates(
  ...states: Array<SettingsAutosaveSnapshot | null | undefined>
): SettingsAutosaveSnapshot {
  const present = states.filter(
    (state): state is SettingsAutosaveSnapshot =>
      state !== null && state !== undefined,
  );

  if (present.length === 0) {
    return { status: "saved" };
  }

  return present.reduce((strongest, candidate) =>
    AUTOSAVE_STATE_PRIORITY[candidate.status] >
    AUTOSAVE_STATE_PRIORITY[strongest.status]
      ? candidate
      : strongest,
  );
}
