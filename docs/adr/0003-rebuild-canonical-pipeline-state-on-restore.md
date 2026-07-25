# ADR-0003: Rebuild canonical pipeline state on restore rather than archiving it

- **Status:** Accepted
- **Date:** 2026-07-25
- **Deciders:** Valtora

## Context

Nojoin's unified transcript pipeline keeps its authoritative data in a graph of
canonical tables: `transcript_utterances`, `transcript_utterance_events`,
`processing_runs`, `recording_asr_window_results`, `recording_speaker_aliases`,
`speaker_correction_events`, `diarization_window_results` and
`diarization_window_turns`. What the frontend reads, and what the backup archives,
is `Transcript.segments` — a **projection** rebuilt from those tables by
`refresh_transcript_projection_from_canonical`.

None of the canonical tables were in the backup. Worse, `pipeline_generation` was
restored verbatim as `"unified"`, so a restored recording asserted full canonical
support while owning zero canonical rows. Transcript and speaker edits route
through `ensure_canonical_backfill` and `apply_compatibility_segment_replace`,
which would then behave unpredictably against an empty graph.

The decision was whether to archive the canonical tables or to rebuild them.

## Decision

**We do not archive canonical pipeline state. We rebuild it from the projection.**

A restore sets `Recording.pipeline_generation` to `NULL`, which makes restored
recordings indistinguishable from legacy rows to the existing cutover machinery
(`backend/startup_canonical_cutover.py`, run from `backend/entrypoint.sh` at API
boot). `finalize_restored_recording_task` then calls `ensure_canonical_backfill`
per restored recording, which derives the utterance graph from
`Transcript.segments` via `replace_utterances_from_segments`.

The canonical tables are listed in `UNARCHIVED_TABLES` in
`backend/core/backup_manager.py` with this rationale attached, and
`test_backup_model_parity` fails if anyone adds a table without classifying it.

## Rationale

**The projection is lossless for everything that affects future behaviour.**
`replace_utterances_from_segments` reconstructs `manual_text_locked` and
`manual_speaker_locked` from the projection's `text_manually_edited` and
`speaker_manually_edited` flags. Those locks are what stop a later reprocess from
overwriting a user's hand corrections. Had they *not* round-tripped, this decision
would have gone the other way: silently exposing corrected transcripts to being
re-overwritten is not an acceptable cost.

**Archiving the graph is where restore bugs come from.** The canonical tables carry
a dense foreign-key graph, including self-references and cross-references between
utterances, events, runs and window results. Every one would need remapping on
restore. This subsystem's history is precisely that: unremapped foreign keys
silently dropping rows. `recording_speakers` alone carries three back-references
into these tables (`processing_run_id`, `last_speaker_correction_event_id`,
`last_diarization_window_result_id`) which were unhandled and would have dropped
every restored speaker row; the parity test found them. Adding eight more tables
of the same shape multiplies that surface for no user-visible gain.

**Reuse beats reinvention.** Backfilling legacy rows from the projection is an
existing, tested, in-production path. A restored recording is exactly a legacy
recording from the target installation's point of view, so it should travel the
same road.

## Consequences

**Accepted loss.** Audit history does not survive a restore:
`transcript_utterance_events`, `speaker_correction_events`, `processing_runs`,
per-window diarisation results and confidence payloads are gone. Utterance
`public_id`s are regenerated, so an external reference to a specific utterance
does not survive. Recording-level `public_id` and `meeting_uid` **are** preserved,
so recording URLs, document relationships and later backups still line up.

**Restored recordings do work on first touch.** The backfill runs in the
finalisation task, so it is normally complete before anyone opens the meeting. If
that task fails, the mutation routes backfill lazily on first edit and the boot
sweep catches the rest, so there are three independent paths to the same state.

**This is documented as user-facing behaviour** in `docs/BACKUP_RESTORE.md`, so an
operator relying on backups for audit evidence knows the archive is not one.

**Reversible.** If audit history later needs to survive restores, the canonical
tables can be added to `MODELS` with their foreign keys classified in
`RESTORE_FOREIGN_KEYS`. The parity test and the format version make that a
contained change rather than a rewrite.
