# Pipeline Refactor Plan

Status: Draft
Created: 2026-05-19
Scope: Live transcription, diarization, speaker identity, final processing, Companion audio upload, transcript UX, tests, and documentation.

## Vision

Nojoin should treat the live pipeline as the primary recording pipeline, not as a disposable preview. Audio should be captured once, progressively transcribed, progressively diarized, and refined as more context arrives. Rolling Pyannote diarization should be able to revise recent and earlier live transcript speaker assignments when the system gains better evidence. User speaker corrections must feed future live speaker assignment immediately. Final processing should promote the already-built transcript and diarization state, then run only catch-up, reconciliation, voiceprint, title, notes, and meeting-intelligence work unless the user explicitly asks for a new transcription pass.

This plan is intentionally waterfall-style. The phases are sequential design and implementation gates for a holistic delivery rather than independent release slices. Partial internal milestones are useful for tracking, but the refactor should not be considered complete until whole-system regression testing passes.

## Target Invariants

- [ ] Normal live recordings run speech-to-text once. Full ASR reruns are reserved for missing live data, explicit manual reprocess, or engine changes.
- [ ] Rolling diarization can update previous live speaker assignments, including backward-looking corrections across the current meeting.
- [ ] Live and final transcript segments use stable identifiers, not list indices, for edits, updates, undo, reconciliation, and frontend rendering.
- [ ] User speaker edits are authoritative and immediately influence future live speaker assignment.
- [ ] Speaker identity is represented as canonical recording speakers plus aliases, corrections, and confidence metadata rather than disposable labels.
- [ ] Final processing preserves manual speaker and text edits even when diarization boundaries differ from live utterance boundaries.
- [ ] The system can recover from live worker failures by catching up from durable audio chunks.
- [ ] Imported recordings use the same unified processing model in batch mode.
- [ ] Increased latency is acceptable when it materially improves diarization quality and speaker stability.

## Acceptance Gates

- [ ] A recorded meeting can be transcribed live, diarized live, corrected by the user during capture, finalized, and exported without a second ASR pass.
- [ ] Renaming or assigning a live speaker causes later same-speaker live segments to use the corrected speaker identity.
- [ ] Rolling Pyannote can revise earlier live speaker labels and the frontend reflects those revisions without corrupting manual edits.
- [ ] Finalization completes substantially faster than the current full post-meeting pipeline for meetings that had successful live processing.
- [ ] Regression fixtures show improved or unchanged transcript text preservation, speaker consistency, edit preservation, and processing reliability.
- [ ] Existing recordings and imports remain readable after migration.
- [ ] The system degrades gracefully when Pyannote, GPU, Hugging Face credentials, or live workers are unavailable.

## Phase 0: Baseline, Fixtures, and Observability

Purpose: establish the failure cases and measurement harness before changing the pipeline.

- [x] Build a representative audio fixture suite.
  - [x] Single-speaker clean speech.
  - [x] Two-speaker alternating turns.
  - [x] Three-or-more-speaker meeting.
  - [x] Overlapping speech and interruptions.
  - [x] Long monologue without silence.
  - [x] Quiet speaker and noisy room cases.
  - [x] Speaker joins late.
  - [x] User renames a speaker during live recording.
  - [x] User assigns one live segment to an existing global speaker.
  - [x] Imported recording with no live upload phase.
  - [x] Missing or invalid Hugging Face token.
- [x] Define measurable acceptance metrics.
  - [x] End-to-end finalization time with live processing enabled.
  - [x] Count of ASR invocations per normal recording.
  - [x] Speaker label churn per speaker.
  - [x] Manual edit preservation rate after finalization.
  - [x] Diarization error proxy for fixtures with known speakers.
  - [x] Time from speech end to visible transcript text.
  - [x] Time from speech end to stable speaker assignment.
- [x] Add debug instrumentation.
  - [x] Live chunk ingest timing.
  - [x] VAD region timing.
  - [x] ASR region timing and engine metadata.
  - [x] Rolling diarization window timing.
  - [x] Speaker match scores and canonical speaker decisions.
  - [x] User correction events.
  - [x] Finalization catch-up and reconciliation timing.
- [x] Create a baseline report from the current implementation.
  - [x] Current live speaker correction behavior.
  - [x] Current index-based final mapping behavior.
  - [x] Current final processing latency.
  - [x] Current live speaker churn on representative fixtures.

Exit gate:

- [x] Baseline fixtures and measurements are reproducible in local development and CI-compatible test environments.

## Phase 1: Architecture and Data Model Design

Purpose: replace append-only transcript JSON assumptions with durable identities and event-aware state.

- [x] Freeze the approved Phase 1 defaults before schema work starts.
  - [x] The canonical write model will be relational current-state tables plus immutable event tables, not `Transcript.segments` JSONB.
  - [x] `Transcript.segments` will remain only as a compatibility projection for existing API and frontend consumers.
  - [x] `RecordingSpeaker` will remain the canonical per-recording speaker identity table and will be extended rather than replaced.
  - [x] Stable utterance IDs will survive text-only and speaker-only revisions, but boundary-changing split and merge operations will supersede prior utterances with new IDs.
  - [x] The originally approved design-only scope has now been implemented through the minimum schema, migration, compatibility API, worker sync, and test seams needed to exercise the canonical model.
- [x] Define the canonical relational write model in dependency order.
  - [x] Add `RecordingAudioChunk` as the durable uploaded-audio source for live, catch-up, finalize, and import flows.
    - [x] Required fields: internal `id`, public `public_id`, `recording_id`, `sequence_no`, `source_kind`, `absolute_start_ms`, `absolute_end_ms`, `duration_ms`, `sample_rate_hz`, `channel_count`, `byte_size`, `sha256`, `storage_path`, `upload_status`, `idempotency_key`, `received_at`, `cleanup_eligible_at`.
    - [x] Define uniqueness and idempotency around `(recording_id, sequence_no)` plus `idempotency_key`.
    - [x] Define retention and cleanup ownership without relying on upload temp-dir deletion.
  - [x] Add `ProcessingRun` as the canonical run ledger for `live`, `catch_up`, `finalize`, `reprocess`, `import`, and `backfill`.
    - [x] Required fields: internal `id`, public `public_id`, `recording_id`, optional `parent_run_id`, `run_kind`, `trigger_source`, optional `requested_by_user_id`, `status`, `config_hash`, `transcription_backend`, `diarization_backend`, `model_metadata`, `span_start_ms`, `span_end_ms`, `reused_live_asr`, `idempotency_key`, `metrics`, `error_summary`, `started_at`, `completed_at`.
    - [x] Define the default cardinality as one live run per recording session, with additional runs for catch-up, finalize, import, reprocess, and backfill.
  - [x] Add `TranscriptUtterance` as the canonical transcript display row.
    - [x] Required fields: internal `id`, public `public_id`, `recording_id`, `sort_key`, `start_ms`, `end_ms`, `text`, optional `recording_speaker_id`, `state`, `source_kind`, `processing_run_id`, `revision`, optional `overlap_group_id`, `overlap_rank`, `manual_text_locked`, `manual_speaker_locked`, `text_confidence`, `speaker_confidence`, `confidence_payload`, `created_at`, `updated_at`.
    - [x] Define state values for provisional, stable, superseded, finalized, and deleted or tombstoned views.
  - [x] Add `TranscriptUtteranceEvent` as the immutable utterance change log.
    - [x] Required fields: `id`, `recording_id`, `utterance_id`, optional `processing_run_id`, optional `actor_user_id`, `event_type`, `source`, `old_values`, `new_values`, `resulting_revision`, `created_at`.
    - [x] Cover create, revise text, revise timing, revise speaker, split, merge, supersede, manual lock, and finalize events.
  - [x] Extend `RecordingSpeaker` into the canonical recording-speaker identity row.
    - [x] Additive fields: public `public_id`, `speaker_status`, `speaker_kind`, `first_seen_ms`, `last_seen_ms`, `identity_confidence`, `identity_locked`.
    - [x] Keep `global_speaker_id`, `local_name`, `embedding`, `color`, `merged_into_id`, and existing merge-chain semantics.
    - [x] Keep `diarization_label` only as a legacy alias input and compatibility surface, not as the long-term identity key.
  - [x] Add `RecordingSpeakerAlias` to map transient labels and names onto canonical recording speakers.
    - [x] Required fields: `id`, `recording_speaker_id`, `alias_type`, `alias_value`, optional `source_run_id`, `active`, optional `valid_from_ms`, optional `valid_to_ms`, `confidence`, `created_at`.
    - [x] Cover `LIVE_XX`, `SPEAKER_XX`, `MANUAL_x`, inferred names, imported labels, and merge-related aliases.
  - [x] Add `SpeakerCorrectionEvent` as the authoritative user-intent log.
    - [x] Required fields: `id`, public `public_id`, `recording_id`, `actor_user_id`, optional `utterance_id`, optional `source_recording_speaker_id`, optional `target_recording_speaker_id`, optional `target_global_speaker_id`, `event_type`, `scope`, optional `effective_from_ms`, `payload`, `created_at`.
    - [x] Cover rename, utterance assignment, recording-wide reassignment, from-now-on reassignment, merge, link-to-global, and promote-to-global actions.
  - [x] Add `DiarizationWindowResult` and `DiarizationWindowTurn` as the rolling and final diarization store.
    - [x] `DiarizationWindowResult` fields: `id`, public `public_id`, `recording_id`, `processing_run_id`, `window_index`, `window_start_ms`, `window_end_ms`, optional `chunk_start_sequence`, optional `chunk_end_sequence`, `model_name`, `model_version`, `device`, `config_hash`, `status`, `raw_payload`, `created_at`.
    - [x] `DiarizationWindowTurn` fields: `id`, `window_result_id`, `local_speaker_key`, `start_ms`, `end_ms`, `confidence`, optional `matched_recording_speaker_id`, `metadata`.
  - [x] Define the shared provenance and confidence conventions.
    - [x] Store human-facing confidence as normalized `0..1` floats.
    - [x] Store raw provider or model evidence in JSONB fields.
    - [x] Require every canonical write row to retain the producing `processing_run_id` and the last correcting event or diarization window that touched it.
- [x] Define the compatibility read model and serializer boundary.
  - [x] Keep `Transcript` as the notes, user-notes, Meeting Edge, and compatibility projection shell.
  - [x] Project current active `TranscriptUtterance` rows back into `Transcript.segments` for existing response serializers.
  - [x] Preserve the current frontend shape during rollout while allowing additive fields such as `id`, `revision`, `recording_speaker_id`, `state`, `speaker_confidence`, `text_confidence`, and `updated_at`.
  - [x] Define projection rules for overlapping speech so existing transcript rendering is preserved until the later UX phase.
  - [x] Define how merged speakers, aliases, and global-speaker links resolve into the compatibility segment `speaker` field.
- [x] Define transaction, concurrency, and idempotency boundaries.
  - [x] Specify which writes must be atomic when live workers create or revise utterances, speaker identities, aliases, and run state.
  - [x] Define idempotency keys for chunk ingest, ASR window processing, diarization window processing, reconciliation, and finalize promotion.
  - [x] Define retry semantics so failed live or catch-up work can be replayed without duplicate canonical rows.
  - [x] Define ordering guarantees between `RecordingAudioChunk`, `ProcessingRun`, `TranscriptUtteranceEvent`, and compatibility projection refreshes.
- [x] Define the canonical update and supersession model.
  - [x] Specify when an automated revision updates an utterance in place versus superseding it with replacement utterances.
  - [x] Specify how backward-looking diarization revisions attach to existing utterances and when they create split or merge supersession chains.
  - [x] Specify how manual text edits block automated text replacement.
  - [x] Specify how manual speaker edits block automated speaker reassignment unless the user explicitly changes scope.
  - [x] Specify how overlapping speech is represented without corrupting primary chronological transcript order.
  - [x] Specify how tombstones or superseded utterances are exposed to later live-delta consumers.
- [x] Define the API contract cutover plan.
  - [x] Add stable utterance ID read and write contracts while preserving existing index-based endpoints as compatibility wrappers.
  - [x] Define canonical text-edit input: `utterance_id`, `text`, `expected_revision`.
  - [x] Define canonical speaker-edit input: `utterance_id`, target speaker selector, correction scope, and `expected_revision`.
  - [x] Define correction scopes explicitly: `utterance_only`, `speaker_everywhere_in_recording`, `from_this_utterance_forward`, and `merge_into_speaker`.
  - [x] Define live transcript polling or event envelopes with transcript revision metadata, changed utterances, and superseded utterance IDs.
  - [x] Define the compatibility rule that current `GET /recordings/{id}` and transcript payloads remain consumable by the existing frontend during rollout.
  - [x] Define deprecation handling for whole-array transcript replacement so current undo and redo keep working until stable-ID editing lands in the frontend.
- [x] Define the migration, backfill, and rollback package.
  - [x] Write the additive Alembic plan for new tables, new columns, and required indexes without removing `Transcript.segments`.
  - [x] Define completed-recording backfill order: create `ProcessingRun`, reuse or normalize `RecordingSpeaker`, generate aliases, create `TranscriptUtterance`, create `TranscriptUtteranceEvent`, then project back into `Transcript.segments`.
  - [x] Preserve manual flags currently stored in segment JSON, including `speaker_manually_edited` and `text_manually_edited`.
  - [x] Preserve existing `RecordingSpeaker` rows, merge chains, global speaker links, and voiceprint data.
  - [x] Define how to exclude in-flight `UPLOADING`, `QUEUED`, and `PROCESSING` recordings from backfill until later live-pipeline cutover phases.
  - [x] Define rollback behavior with a feature flag that disables canonical writes and falls back to legacy JSON read and write behavior.
- [x] Define the Phase 1 validation and handoff package.
  - [x] Add migration design tests for backfilling legacy transcript JSON into canonical utterances.
  - [x] Add projection tests proving canonical utterances serialize back into the current transcript response shape.
  - [x] Add API contract tests for utterance-ID text edits, speaker edits, correction scopes, and compatibility wrappers.
  - [x] Add alias and merge tests covering `LIVE_XX`, `SPEAKER_XX`, `MANUAL_x`, merged speakers, and global-speaker links.
  - [x] Add supersession tests covering utterance split, merge, and tombstone behavior.
  - [x] Add rollback and feature-flag tests proving legacy reads still work with canonical writes disabled.
  - [x] Record open implementation dependencies that later phases will consume directly: durable chunk ingest, unified live ASR, rolling diarization, backward speaker revision, and frontend live revision UX.

Exit gate:

- [x] The canonical Phase 1 design package is fully documented, including schema definitions, compatibility rules, update semantics, migration and rollback strategy, API contracts, and the test plan needed for implementation in later phases.

## Phase 2: Durable Audio Ingest and Rolling Window Store

Purpose: make uploaded audio chunks a reliable source for live catch-up, rolling diarization, and final promotion.

- [x] Update backend upload persistence.
  - [x] Store chunk metadata with sequence, start time, duration, sample rate, channel information, checksum, and upload status.
  - [x] Keep uploaded chunks available until finalization and catch-up complete.
  - [x] Replace temp-dir deletion assumptions with lifecycle-managed cleanup.
  - [x] Make chunk ingestion idempotent for retries.
- [x] Build a server-side rolling audio window assembler.
  - [x] Assemble contiguous chunks by sequence.
  - [x] Support overlapping analysis windows independent of Companion upload cadence.
  - [x] Track absolute recording time for every window.
  - [x] Persist window manifests so live workers can resume after failure.
- [x] Keep Companion upload cadence flexible.
  - [x] Preserve small chunk uploads for reliability and responsiveness.
  - [x] No target chunk duration override is currently needed because backend windowing is decoupled from upload cadence.
  - [x] Confirm stop/finalize waits for all successful uploads before final processing begins.
- [x] Add catch-up behavior.
  - [x] Detect uploaded chunks that were not processed live.
  - [x] Queue catch-up ASR and diarization before final promotion.
  - [x] Surface catch-up progress in processing status.

Checkpoint update:

- [x] Catch-up ASR now runs against pending durable window spans before live transcript reuse.
- [x] Catch-up diarization now persists window results and turns for pending durable manifests before final promotion.
- [x] Transcript reconciliation against persisted diarization windows remains a later Phase 4 concern, not a Phase 2 blocker.

Exit gate:

- [x] A live recording can be stopped, catch up any missed chunks, and finalize from durable chunk metadata without relying on deleted temp state.

## Phase 3: Unified Live ASR Pipeline

Purpose: make live transcription the authoritative transcription source for normal recordings.

- [ ] Replace append-only provisional segment writes with stable utterance writes.
  - [ ] Assign immutable utterance IDs when live ASR creates text.
  - [ ] Store source window, chunk range, text confidence if available, and ASR engine metadata.
  - [ ] Mark utterances as provisional, stable, superseded, or finalized.
  - [ ] Preserve manual text edits across automated revisions.
- [ ] Improve live VAD and ASR boundary handling.
  - [ ] Keep rolling context windows for ASR acoustic run-up.
  - [ ] Avoid duplicate transcription at chunk boundaries.
  - [ ] Tune forced emission for the new quality-first latency posture.
  - [ ] Prevent long monologues from creating unusably large utterances.
- [ ] Make ASR idempotent and resumable.
  - [ ] Key ASR results by recording, chunk/window range, engine, model, and config hash.
  - [ ] Skip already processed windows unless explicitly reprocessed.
  - [ ] Record errors without losing the ability to catch up later.
- [ ] Update final processing to consume live ASR output.
  - [ ] Build transcription results from stable utterances, not raw list-index JSON.
  - [ ] Run ASR only for missing spans or explicit engine override.
  - [ ] Track finalization evidence showing whether ASR was reused or rerun.

Exit gate:

- [ ] Normal live recordings reach finalization with one ASR pass per processed audio span.

## Phase 4: Rolling Pyannote Diarization and Backward Reconciliation

Purpose: introduce high-quality live diarization that can revise earlier speaker assignments as context improves.

- [ ] Add a rolling diarization scheduler.
  - [ ] Run Pyannote over configurable windows, for example 20-60 seconds with overlap.
  - [ ] Separate upload chunk duration from diarization window duration.
  - [ ] Use worker concurrency limits to protect GPU and CPU resources.
  - [ ] Persist window inputs, outputs, model version, device, and config hash.
- [ ] Map window-local Pyannote speakers to canonical recording speakers.
  - [ ] Use temporal continuity across overlapping windows.
  - [ ] Use voice embeddings aggregated over multiple clean speech spans.
  - [ ] Use global speaker voiceprints when available.
  - [ ] Avoid creating new speakers until enough evidence exists.
  - [ ] Keep low-confidence labels provisional rather than churning identities.
- [ ] Reconcile diarization with transcript utterances.
  - [ ] Align diarization turns to ASR utterances by time overlap.
  - [ ] Support utterance splitting when one ASR utterance spans multiple speakers.
  - [ ] Support utterance merging when diarization confirms a continuous same-speaker turn.
  - [ ] Preserve text order and manual text edits during speaker-only revisions.
  - [ ] Represent overlapping speakers without hiding primary utterances.
- [ ] Apply backward-looking revisions.
  - [ ] Revisit recent windows when new overlapping context arrives.
  - [ ] Revisit older windows when a user correction or stronger voiceprint resolves identity.
  - [ ] Mark automated revisions with provenance and confidence.
  - [ ] Never override a manual speaker edit unless the user explicitly changes that scope.
- [ ] Define stabilization rules.
  - [ ] Decide when a live speaker label becomes stable.
  - [ ] Decide when a diarization window no longer needs routine reprocessing.
  - [ ] Keep finalization able to run a full-recording diarization check only for low-confidence spans.

Exit gate:

- [ ] Rolling diarization can improve earlier live speaker assignments in fixtures without losing manual corrections.

## Phase 5: Speaker Identity, User Corrections, and Name Inference

Purpose: make speaker management consistent across live, final, local recording speakers, and global people.

- [ ] Implement canonical speaker corrections.
  - [ ] Store every user rename, segment assignment, merge, and global-speaker link as a correction event.
  - [ ] Apply corrections immediately to current utterances in scope.
  - [ ] Feed corrections into future live speaker matching.
  - [ ] Preserve source aliases so old embeddings and labels continue to help matching.
  - [ ] Prevent deletion of live speaker embeddings while a recording is still uploading.
- [ ] Add correction scopes.
  - [ ] This utterance only.
  - [ ] This speaker everywhere in the recording.
  - [ ] This speaker from now on.
  - [ ] Merge two recording speakers.
  - [ ] Link to existing global speaker.
  - [ ] Promote to new global speaker.
- [ ] Improve voiceprint learning.
  - [ ] Extract embeddings from durable chunk audio during live recording.
  - [ ] Prefer clean, single-speaker spans for voiceprint updates.
  - [ ] Merge embeddings with drift protection once enough evidence exists.
  - [ ] Respect locked global voiceprints.
  - [ ] Record confidence and source spans for every voiceprint update.
- [ ] Infer speaker names from transcript and meeting context.
  - [ ] Detect deterministic self-introductions such as "I'm Alice" or "this is Alice".
  - [ ] Detect direct address patterns that can suggest names for known speaker turns.
  - [ ] Use linked calendar attendees and known people as candidate names.
  - [ ] Optionally ask the LLM for name suggestions with strict JSON output and evidence spans.
  - [ ] Store inferred names as suggestions with confidence and evidence, not silent hallucinated truth.
  - [ ] Auto-apply only deterministic or very high-confidence suggestions if that product decision is approved.
  - [ ] Expose uncertain suggestions to the user for confirmation.
- [ ] Resolve conflicts.
  - [ ] Prefer manual corrections over automated inference.
  - [ ] Prefer locked global voiceprints over weak live embeddings.
  - [ ] Preserve multiple candidates when confidence is low.
  - [ ] Audit every automatic rename or suggested rename.

Exit gate:

- [ ] A live user correction changes future same-speaker live output, and transcript-derived name suggestions are evidence-backed and reversible.

## Phase 6: Finalization and Post-Meeting Processing

Purpose: turn final processing into promotion and enrichment instead of repeating the whole pipeline.

- [ ] Redesign `process_recording_task` around promotion.
  - [ ] Verify all uploaded chunks are present or accounted for.
  - [ ] Run catch-up ASR only for unprocessed spans.
  - [ ] Run catch-up diarization only for unprocessed or low-confidence spans.
  - [ ] Promote stable live utterances to final transcript state.
  - [ ] Remove provisional markers only after reconciliation succeeds.
- [ ] Keep final diarization efficient and targeted.
  - [ ] Run full-recording Pyannote only when live diarization failed, confidence is low, or explicit reprocess requests it.
  - [ ] Otherwise run reconciliation and quality checks against rolling diarization results.
  - [ ] Preserve manual speaker and text edits by stable ID and time overlap.
- [ ] Preserve downstream enrichment.
  - [ ] Extract or finalize speaker voiceprints.
  - [ ] Run deterministic speaker resolution against global people.
  - [ ] Run meeting title inference.
  - [ ] Run note generation and meeting intelligence.
  - [ ] Keep manual notes and Meeting Edge focus available to the final LLM prompts.
- [ ] Support imports through the unified pipeline.
  - [ ] Treat imports as batch chunk ingestion.
  - [ ] Generate utterances and diarization windows through the same storage model.
  - [ ] Skip live UI states but preserve the same final transcript behavior.

Exit gate:

- [ ] Finalization is faster for successful live recordings and functionally equivalent or better for imports and failed-live recordings.

## Phase 7: Frontend Transcript and Speaker UX

Purpose: make live revisions understandable and give users precise control over speaker identity.

- [ ] Move transcript editing from list indices to stable utterance IDs.
  - [ ] Update API client methods.
  - [ ] Update undo and redo history.
  - [ ] Update find and replace.
  - [ ] Update export flows.
  - [ ] Update playback and trim interactions.
- [ ] Display live revision state.
  - [ ] Provisional text state.
  - [ ] Provisional speaker state.
  - [ ] Stable speaker state.
  - [ ] Manual speaker override state.
  - [ ] Low-confidence speaker state.
  - [ ] Recently revised speaker assignment state.
- [ ] Add speaker correction workflows.
  - [ ] Rename this speaker everywhere.
  - [ ] Assign only this utterance.
  - [ ] Apply this speaker from now on.
  - [ ] Merge speakers.
  - [ ] Link to a global speaker.
  - [ ] Confirm or reject inferred speaker name suggestions.
- [ ] Improve overlapping speech presentation.
  - [ ] Show parallel speaker turns without hiding provisional unknown speech.
  - [ ] Avoid layout jumps when rolling diarization revises recent turns.
  - [ ] Keep text readable on narrow viewports.
- [ ] Handle backward-looking updates safely.
  - [ ] Preserve scroll position when older utterances change.
  - [ ] Highlight revisions briefly without creating noisy UI.
  - [ ] Avoid clobbering text currently being edited.
  - [ ] Resolve edit conflicts with a clear local-state policy.

Exit gate:

- [ ] Users can understand and control live speaker identity while automated revisions continue in the background.

## Phase 8: Settings, Operations, and Failure Modes

Purpose: expose the right operational controls without turning normal setup into model-tuning work.

- [ ] Add quality and latency profiles.
  - [ ] Low latency.
  - [ ] Balanced.
  - [ ] High accuracy.
  - [ ] Custom advanced settings.
- [ ] Expose advanced live pipeline settings for admins.
  - [ ] Rolling diarization window length.
  - [ ] Rolling diarization hop size.
  - [ ] Minimum speaker confidence for stable labels.
  - [ ] Minimum speech duration for voiceprints.
  - [ ] Live ASR forced emission timing.
  - [ ] Whether full final diarization runs by default.
- [ ] Improve model readiness checks.
  - [ ] Hugging Face token validity.
  - [ ] Pyannote model access.
  - [ ] Device availability.
  - [ ] Worker queue availability.
  - [ ] Clear fallback state when diarization cannot run.
- [ ] Add operational logging and admin visibility.
  - [ ] Live pipeline stages.
  - [ ] Rolling diarization queue depth.
  - [ ] Per-recording ASR and diarization invocation counts.
  - [ ] Catch-up work remaining.
  - [ ] Finalization promotion summary.

Exit gate:

- [ ] Operators can diagnose live pipeline quality, latency, and fallback behavior from settings and logs.

## Phase 9: Migration, Compatibility, and Documentation

Purpose: land the refactor without stranding existing recordings or users.

- [ ] Migrate existing data.
  - [ ] Preserve existing `Transcript.segments` JSON for compatibility during migration.
  - [ ] Backfill stable utterance IDs.
  - [ ] Backfill speaker references to canonical recording speakers where possible.
  - [ ] Preserve manual edit flags.
  - [ ] Preserve global speaker links and voiceprints.
- [ ] Maintain API compatibility during rollout.
  - [ ] Continue returning the current transcript shape to older frontend code until the new UI is ready.
  - [ ] Add new endpoints or response fields behind compatible contracts.
  - [ ] Keep exports stable.
- [ ] Update documentation.
  - [ ] Architecture live pipeline section.
  - [ ] User guide live transcription behavior.
  - [ ] Admin/settings documentation.
  - [ ] Development setup and test fixture instructions.
  - [ ] Troubleshooting for Pyannote, Hugging Face access, GPU, and worker failures.
- [ ] Prepare rollback and recovery notes.
  - [ ] Schema rollback constraints.
  - [ ] Data recovery from chunk store.
  - [ ] Behavior when rolling diarization is disabled.

Exit gate:

- [ ] Existing recordings, new live recordings, imports, exports, and docs all work against the new model.

## Phase 10: Whole-System Verification and Release Readiness

Purpose: surface regressions at the system level before considering the refactor complete.

- [ ] Add backend unit tests.
  - [ ] Stable utterance ID creation and updates.
  - [ ] Speaker correction event application.
  - [ ] Canonical speaker alias resolution.
  - [ ] Time-overlap reconciliation.
  - [ ] Manual edit preservation.
  - [ ] Name inference evidence and conflict handling.
- [ ] Add backend integration tests.
  - [ ] Live upload with rolling ASR and diarization.
  - [ ] User speaker rename during live recording.
  - [ ] Backward diarization revision after new context.
  - [ ] Finalization without second ASR pass.
  - [ ] Finalization catch-up after missed live task.
  - [ ] Import path through unified pipeline.
  - [ ] Missing Pyannote or Hugging Face fallback.
- [ ] Add frontend tests.
  - [ ] Live transcript rendering with revisions.
  - [ ] Speaker correction scope selection.
  - [ ] Stable-ID editing and undo.
  - [ ] Inferred speaker name confirmation.
  - [ ] Overlapping speech display.
- [ ] Add Companion and upload tests where practical.
  - [ ] Chunk metadata correctness.
  - [ ] Stop/finalize after pending uploads.
  - [ ] Retry and idempotent upload behavior.
- [ ] Run fixture regression suite.
  - [ ] Compare speaker churn before and after.
  - [ ] Compare finalization time before and after.
  - [ ] Confirm ASR invocation count.
  - [ ] Confirm manual edit preservation.
  - [ ] Confirm degraded-mode behavior.
- [ ] Run standard project validation.
  - [ ] Backend tests.
  - [ ] Frontend lint, typecheck, and tests.
  - [ ] Companion build or targeted Rust tests.
  - [ ] Docker compose smoke test.
  - [ ] Manual end-to-end recording test.

Exit gate:

- [ ] Whole-system acceptance criteria pass and the refactor is ready for release review.

## Implementation Dependency Order

- [ ] Phase 0 must complete before schema or pipeline changes begin.
- [ ] Phase 1 must complete before migrations or API implementation begin.
- [ ] Phase 2 must complete before rolling diarization can be reliable.
- [ ] Phase 3 must complete before final processing can stop rerunning normal ASR.
- [ ] Phase 4 must complete before backward-looking speaker correction is considered real.
- [ ] Phase 5 must complete before live user corrections are trusted as future speaker identity.
- [ ] Phase 6 must complete before finalization latency improvements are claimed.
- [ ] Phase 7 must complete before users can safely operate the new model.
- [ ] Phase 8 and Phase 9 must complete before release readiness.
- [ ] Phase 10 must complete before merging or releasing the refactor.

## Key Risks and Decisions

- [ ] Decide whether transcript utterances become relational rows now or whether JSONB remains the write model with stable IDs added.
- [ ] Decide how much automatic speaker-name inference can apply without user confirmation.
- [ ] Decide default rolling diarization latency profile.
- [ ] Decide whether full-recording final diarization remains an optional validation pass or is disabled by default after successful rolling diarization.
- [ ] Decide how long uploaded chunks are retained after finalization for audit, retry, and storage management.
- [ ] Decide whether live transcript updates remain polling-based or move to a streaming/event channel.
- [ ] Decide GPU scheduling limits for concurrent live diarization jobs.

## Likely Code Areas

- [ ] `backend/processing/live_transcribe.py`
- [ ] `backend/processing/diarize.py`
- [ ] `backend/processing/embedding.py`
- [ ] `backend/processing/embedding_core.py`
- [ ] `backend/utils/live_transcript.py`
- [ ] `backend/utils/speaker_assignment.py`
- [ ] `backend/worker/tasks.py`
- [ ] `backend/api/v1/endpoints/recordings.py`
- [ ] `backend/api/v1/endpoints/transcripts.py`
- [ ] `backend/api/v1/endpoints/speakers.py`
- [ ] `backend/models/transcript.py`
- [ ] `backend/models/speaker.py`
- [ ] `backend/models/recording.py`
- [ ] `backend/alembic/versions/`
- [ ] `frontend/src/app/(dashboard)/recordings/[id]/page.tsx`
- [ ] `frontend/src/components/TranscriptView.tsx`
- [ ] `frontend/src/components/SpeakerAssignmentPopover.tsx`
- [ ] `frontend/src/lib/api.ts`
- [ ] `frontend/src/types/index.ts`
- [ ] `frontend/src/components/settings/`
- [ ] `companion/src-tauri/src/audio.rs`
- [ ] `companion/src-tauri/src/uploader.rs`
- [ ] `backend/tests/test_live_transcription.py`
- [ ] `backend/tests/test_live_transcript_reuse.py`
- [ ] `backend/tests/test_speaker_assignment.py`
