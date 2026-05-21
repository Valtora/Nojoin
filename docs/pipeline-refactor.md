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

Purpose: make live transcription the authoritative transcription source for normal live recordings by writing live ASR into canonical utterances first, keeping `Transcript.segments` as a compatibility projection, and letting finalization promote or fill gaps instead of rerunning normal ASR end to end.

Current state truth audit:

- [x] Phase 1 canonical utterance, event, provenance, and compatibility projection primitives already exist.
- [x] Phase 2 durable chunk ingest, rolling window manifests, catch-up span collection, and catch-up diarization persistence already exist.
- [x] `live_transcribe.py` now writes provisional live output into canonical utterances first and refreshes `Transcript.segments` as a compatibility projection.
- [x] `process_recording_task` now reads reusable live and catch-up work from canonical utterances first and finalizes through canonical reconciliation instead of rebuilding canonical state only after final segment construction.
- [x] Meeting Edge and the Phase 3-critical internal consumers now read canonical utterances first with projection fallback; only legacy compatibility wrappers and projection refresh paths remain intentionally segment-based.
- [x] A dedicated persisted live ASR result ledger now exists and is applied through the current Alembic head.
- [x] Catch-up recovery now reuses completed ASR ledger spans by exact span and config match, reruns only failed or missing spans, and keeps pending diarization windows promotable even when no new ASR is required.
- [x] Normal imports remain intentionally out of scope for the first Phase 3 cutover.

Approved implementation decisions:

- Use a dedicated `RecordingAsrWindowResult` ledger keyed by recording, audio span, engine, model, and config hash.
- Mark utterances `provisional` on first live emission, `stable` once the span is sealed and no longer subject to carry-buffer rewrite, and `finalized` only during finalization.
- Preserve utterance public IDs across finalization unless boundaries change; use split and merge supersession events when boundaries change.
- Move Meeting Edge to canonical-first reads in Phase 3 with projection fallback.
- Limit Phase 3 speaker-correction scope to manual lock preservation and live-label alias continuity; broader correction propagation remains a Phase 5 concern.
- Keep imported recordings compatible with the new model but defer direct import cutover until a later phase.

Waterfall checkpoints:

- [x] Checkpoint 3.1: Persist live ASR results as a first-class ledger.
  - [x] Objective: define the authoritative persisted unit of live ASR work before changing write paths.
  - [x] Why this order: retries, catch-up, and finalization all need the same span identity and idempotency contract before live utterance writes can safely move off segment JSON.
  - [x] Likely files and modules: `backend/models/pipeline.py`, `backend/alembic/versions/*`, `backend/utils/audio_windows.py`, `backend/processing/live_transcribe.py`, `backend/worker/tasks.py`.
  - [x] Data model and API implications: add `RecordingAsrWindowResult` with recording id, window or span bounds, chunk range, engine, model, config hash, status, error payload, processing run reference, and produced utterance references; do not change public transcript APIs in this checkpoint.
  - [x] Migration and compatibility: additive migration only; do not backfill legacy recordings up front; keep `Transcript.segments` as the outward compatibility projection.
  - [x] Test strategy: add migration tests, idempotency-key tests, task retry tests, and status-transition tests for pending, completed, failed, and superseded ASR results.
  - [x] Rollback and risk: guard writes and reads behind a feature flag so the system can fall back to legacy segment-only live behavior if the new ledger causes regressions.
  - [x] Implementation checklist:
    - [x] Add the `RecordingAsrWindowResult` model and migration.
    - [x] Define lookup helpers keyed by recording, span, engine, model, and config hash.
    - [x] Record retry-safe status transitions and error payloads without deleting failed work history.
    - [x] Document how this ledger interacts with `ProcessingRun` and durable window manifests.

- [x] Checkpoint 3.2: Write live ASR output directly into canonical provisional utterances.
  - [x] Objective: make live ASR create canonical utterances and events at first emission instead of appending provisional segment dicts.
  - [x] Why this order: Phase 3 cannot claim live transcription is authoritative until the live path writes canonical utterances first and projection second.
  - [x] Likely files and modules: `backend/processing/live_transcribe.py`, `backend/utils/canonical_pipeline.py`, `backend/models/pipeline.py`, `backend/tests/test_live_transcription.py`.
  - [x] Data model and API implications: create a live `ProcessingRun`, persist utterance public IDs immediately, populate source span, chunk range, confidence fields, engine metadata, overlap information, and provenance on each emitted utterance, and project canonical state back into `Transcript.segments` for compatibility readers.
  - [x] Migration and compatibility: keep the existing transcript payload shape unchanged by treating `Transcript.segments` as a projection refreshed from canonical utterances after each live update.
  - [x] Test strategy: extend live transcription tests to prove canonical utterances are created once per emitted span, retries are idempotent, out-of-order uploads do not duplicate utterances, and projection output matches the current live transcript response shape.
  - [x] Rollback and risk: this is the highest-risk live checkpoint; keep the legacy append path available behind a flag until latency and live ordering behavior match baseline.
  - [x] Implementation checklist:
    - [x] Replace direct append-only writes to `Transcript.segments` with canonical utterance creation and event recording.
    - [x] Create a live `ProcessingRun` and attach emitted utterances to the correct run and ASR ledger rows.
    - [x] Set utterance state to `provisional` on first emission.
    - [x] Preserve overlap metadata and emitted ordering without relying on list indices.
    - [x] Refresh `Transcript.segments` from canonical projection after each live write.

- [x] Checkpoint 3.3: Canonicalize live manual edits and speaker label continuity.
  - [x] Objective: ensure manual text edits, manual speaker edits, and live speaker labels survive later live revisions and finalization.
  - [x] Why this order: finalization cannot safely promote live utterances if manual edits and live-label identity are still stored only as mutable segment flags.
  - [x] Likely files and modules: `backend/utils/canonical_pipeline.py`, `backend/api/v1/endpoints/transcripts.py`, `backend/api/v1/endpoints/speakers.py`, `backend/processing/live_transcribe.py`, `backend/tests/test_canonical_transcript_phase1.py`, `backend/tests/test_live_transcript_reuse.py`.
  - [x] Data model and API implications: route compatibility edit endpoints through canonical helpers whenever an utterance ID exists, persist `manual_text_locked` and `manual_speaker_locked`, and durably register live speaker labels through `RecordingSpeakerAlias`; do not add broader correction-scope automation in this phase.
  - [x] Migration and compatibility: legacy index-based edit endpoints remain available as wrappers, but they stop being direct sources of truth when canonical data is present.
  - [x] Test strategy: add live-edit tests proving a text or speaker correction made during upload survives later live ASR revisions, catch-up, and finalization; extend alias tests for `LIVE_XX` continuity.
  - [x] Rollback and risk: moderate risk of projection drift if any endpoint still mutates `Transcript.segments` directly; audit and flag remaining direct writes before enabling this checkpoint by default.
  - [x] Implementation checklist:
    - [x] Route compatibility transcript edit wrappers through canonical text and speaker update helpers.
    - [x] Preserve `speaker_manually_edited` and `text_manually_edited` as projection output derived from canonical lock fields.
    - [x] Register live speaker labels in alias tables at creation time.
    - [x] Preserve existing public IDs and revisions when only text or speaker changes.
    - [x] Leave full correction-scope propagation and broader speaker workflow cleanup for Phase 5.

- [x] Checkpoint 3.4: Make final processing consume canonical live utterances.
  - [x] Objective: convert finalization from segment-array reuse into canonical utterance promotion plus targeted gap filling.
  - [x] Why this order: only after live writes and manual locks are canonical can finalization trust live utterances as reusable work instead of rebuilding from raw segment JSON.
  - [x] Likely files and modules: `backend/worker/tasks.py`, `backend/utils/live_transcript.py`, `backend/utils/canonical_pipeline.py`, `backend/tests/test_reprocess.py`.
  - [x] Data model and API implications: finalization should read active utterances by stable ID and time overlap, reuse eligible live utterances directly, run ASR only for missing spans or explicit engine override, and preserve public IDs unless boundary changes require supersession.
  - [x] Migration and compatibility: keep `Transcript.segments` as a projected final transcript representation so downstream APIs and exports remain stable while the worker cutover lands.
  - [x] Test strategy: prove a fully covered normal live recording reaches finalization without rerunning ASR on already processed spans, and prove explicit engine override still forces rerun when requested.
  - [x] Rollback and risk: high risk because it changes the main worker path; keep a feature flag that reverts finalization to legacy segment-array reuse if canonical promotion produces regressions.
  - [x] Implementation checklist:
    - [x] Replace raw `live_segments_for_reuse` dependence with canonical utterance queries plus projection only where external serializers still require it.
    - [x] Promote eligible `provisional` utterances to `stable` when the span is sealed and to `finalized` only during finalization.
    - [x] Preserve manual text and speaker locks when reconciling final spans.
    - [x] Record finalize provenance showing whether live ASR was reused, partially reused, or rerun.
    - [x] Use split and merge supersession events when final boundary changes require new utterance shapes.

- [x] Checkpoint 3.5: Make catch-up and resume truly span-aware and idempotent.
  - [x] Objective: use the persisted ASR ledger to process only missing or failed spans during catch-up and recovery.
  - [x] Why this order: once finalization trusts canonical live utterances, the system can distinguish covered spans from missing spans and stop rerunning ASR blindly.
  - [x] Likely files and modules: `backend/utils/asr_window_results.py`, `backend/worker/tasks.py`, `backend/tests/test_asr_window_results.py`, `backend/tests/test_reprocess.py`.
  - [x] Data model and API implications: persist engine choice, model, config hash, span coverage, failure state, and reusable catch-up segment payloads for each ASR pass; live rows still carry produced utterance linkage directly, and no public API change is needed.
  - [x] Migration and compatibility: older manifest rows remain legacy coverage hints; completed catch-up ledger rows without reusable segment payloads still fall back to rerun instead of requiring a historical backfill.
  - [x] Test strategy: add exact-match retry-no-op tests, worker recovery tests for completed catch-up rows, partial failure catch-up tests, and keep the focused live/canonical regression suites green.
  - [x] Rollback and risk: moderate risk if span identity is underspecified; exact span-plus-config matching keeps the reuse rule narrow enough to fall back to rerun safely when payloads are missing.
  - [x] Implementation checklist:
    - [x] Skip completed ASR ledger spans unless the user explicitly requests reprocess or changes engine or config.
    - [x] Preserve failed span rows so catch-up can retry without losing history.
    - [x] Reuse sealed live utterances during catch-up instead of reconstructing raw segment arrays.
    - [x] Ensure catch-up fills only uncovered spans before final promotion.
    - [x] Surface enough status for processing progress and operational debugging.

- [x] Checkpoint 3.6: Cut internal readers over to canonical-first transcript reads.
  - [x] Objective: remove the remaining hidden source-of-truth split by moving internal live readers to canonical utterances first and projection fallback second.
  - [x] Why this order: internal readers should only move after canonical live writes and finalization promotion are already trustworthy.
  - [x] Likely files and modules: `backend/worker/tasks.py`, `backend/api/v1/endpoints/speakers.py`, `backend/api/v1/endpoints/transcripts.py`, `backend/models/recording_public.py`.
  - [x] Data model and API implications: no public API contract change; internal readers should query canonical utterances and serialize projection output only where older response shapes still need it.
  - [x] Migration and compatibility: keep projection fallback in place until Meeting Edge and speaker maintenance flows are proven against canonical data in staging and regression tests.
  - [x] Test strategy: Meeting Edge regression tests, speaker rename and merge regression tests, transcript export and trim checks, and compatibility endpoint smoke tests.
  - [x] Rollback and risk: low to moderate because this is mostly a read-path cutover; keep projection fallback until confidence is high.
  - [x] Implementation checklist:
    - [x] Move Meeting Edge refresh to canonical-first transcript reads with projection fallback.
    - [x] Audit remaining `Transcript.segments` mutations and remove or wrap the Phase 3-critical ones.
    - [x] Leave non-critical compatibility-only reads in place only when they are intentionally projection-based.
    - [x] Record any residual segment-only dependencies that are intentionally deferred to later phases.
  - Residual deferred dependencies: legacy transcript compatibility wrappers that still accept segment-index edits, projection refresh and write paths that intentionally maintain `Transcript.segments`, and normal import cutover remain deferred to later phases.

Checkpoint dependency chain:

- [x] Checkpoint 3.1 must land before Checkpoints 3.2 and 3.5.
- [x] Checkpoint 3.2 must land before every later Phase 3 checkpoint.
- [x] Checkpoint 3.3 must land before Checkpoint 3.4.
- [x] Checkpoint 3.4 must land before Checkpoint 3.6 becomes the default read path.
- [x] Checkpoint 3.5 can begin once Checkpoint 3.2 is stable, but it must complete before the final Phase 3 exit gate is considered met.

Validation matrix:

- [x] Live ingest correctness: in-order uploads, out-of-order uploads, carry-over, forced emission, and long-monologue splitting still match or beat baseline behavior.
- [x] Canonical correctness: live utterances receive immutable IDs, correct state transitions, correct provenance, and correct supersession behavior.
- [x] Manual edit preservation: text and speaker edits made during live recording survive later live revisions, catch-up, and finalization.
- [x] Finalization reuse: normal live recordings do not rerun ASR for already covered spans; explicit engine override still triggers rerun.
- [x] Resume and catch-up: worker restarts, failed live windows, and duplicate uploads process only missing spans.
- [x] Compatibility: transcript response shapes, export flows, and compatibility edit endpoints remain stable while `Transcript.segments` is projection-only.
- [x] Internal consumers: Meeting Edge and other Phase 3-critical readers succeed against canonical-first reads with projection fallback.
- [ ] Metrics: ASR invocation count, finalization duration, and manual edit preservation remain at or better than the recorded baseline.

Exit gate:

- [ ] Normal live recordings complete with one ASR pass per covered audio span, catch-up runs only for missing or failed spans, finalization promotes canonical live utterances instead of rebuilding from segment arrays, and `Transcript.segments` remains only a compatibility projection.
  - Automated coverage is now green for live ingest ordering and chunk handling, canonical utterance state and provenance behavior, catch-up and finalization reuse, compatibility projection stability, and canonical-first internal readers.
  - Remaining gap before closing the exit gate: baseline-style metrics comparison and a realistic full-recording validation run.

## Phase 4: Rolling Pyannote Diarization and Backward Reconciliation

Purpose: introduce high-quality live diarization that can revise earlier speaker assignments as context improves.

- [x] Add a rolling diarization scheduler.
  - [x] Run Pyannote over configurable windows, for example 20-60 seconds with overlap.
  - [x] Separate upload chunk duration from diarization window duration.
  - [x] Use worker concurrency limits to protect GPU and CPU resources.
  - [x] Persist window inputs, outputs, model version, device, and config hash.
- [x] Map window-local Pyannote speakers to canonical recording speakers.
  - [x] Use temporal continuity across overlapping windows.
  - [x] Use voice embeddings aggregated over multiple clean speech spans.
  - [x] Use global speaker voiceprints when available.
  - [x] Avoid creating new speakers until enough evidence exists.
  - [x] Keep low-confidence labels provisional rather than churning identities.
- [x] Reconcile diarization with transcript utterances.
  - [x] Align diarization turns to ASR utterances by time overlap.
  - [x] Support utterance splitting when one ASR utterance spans multiple speakers.
  - [x] Support utterance merging when diarization confirms a continuous same-speaker turn.
  - [x] Preserve text order and manual text edits during speaker-only revisions.
  - [x] Represent overlapping speakers without hiding primary utterances.
- [x] Apply backward-looking revisions.
  - [x] Revisit recent windows when new overlapping context arrives.
  - [x] Revisit older windows when a user correction or stronger voiceprint resolves identity.
  - [x] Mark automated revisions with provenance and confidence.
  - [x] Never override a manual speaker edit unless the user explicitly changes that scope.
- [x] Define stabilization rules.
  - [x] Decide when a live speaker label becomes stable.
  - [x] Decide when a diarization window no longer needs routine reprocessing.
  - [x] Keep finalization able to run a full-recording diarization check only for low-confidence spans.

Exit gate:

- [x] Rolling diarization can improve earlier live speaker assignments in fixtures without losing manual corrections.

## Phase 5: Speaker Identity, User Corrections, and Name Inference

Purpose: make speaker management consistent across live, final, local recording speakers, and global people.

Current-state audit:

- [x] Canonical utterance speaker patching already supports explicit correction scopes, correction events, alias continuity, and replay of completed diarization windows.
- [x] Recording speaker schema already has the Phase 5 primitives needed for canonical identity state, including merged speaker provenance, identity lock fields, correction scope enums, and alias tables.
- [x] Transcript read compatibility already supports rebuilding `Transcript.segments` from canonical utterances when canonical writes are enabled.
- [x] Recording speaker rename, promote, merge, and global-merge paths now route through the canonical mutation helpers and repair compatibility projection state afterward.
- [x] Frontend recording transcript edits now default to stable utterance-id mutations with explicit correction scope instead of the segment-index compatibility bridge.
- [x] Voiceprint learning now records durable provenance for incremental live updates while respecting lock and drift rules.
- [x] Retry speaker inference now emits evidence-backed suggestions instead of destructively renaming recording speakers.

Approved implementation decisions:

- [x] Phase 5 should extend the canonical pipeline rather than add a new side channel for speaker state.
- [x] Compatibility readers must continue to see repaired `Transcript.segments` after canonical speaker mutations.
- [x] Manual corrections must remain authoritative over later diarization, name inference, and voiceprint updates.
- [x] Source aliases must be preserved so historic labels and embeddings still route future live matches correctly.
- [x] Name inference should move to suggestion-first semantics with explicit evidence instead of silent destructive renames.

- [x] Checkpoint 5A: Canonicalize speaker correction mutation paths.
  - [x] Route recording speaker rename and link operations through canonical recording-speaker helpers instead of direct projection mutation.
  - [x] Route promote-to-global through canonical helpers while preserving `promote_global_speaker` event semantics.
  - [x] Route local speaker merge and global-merge collision handling through canonical helpers so merge provenance, alias carry-forward, and replay semantics stay consistent.
  - [x] Refresh compatibility transcript projection from canonical utterances after canonical speaker mutations.
  - [x] Extend regression coverage for recording speaker rename, link, promote, merge, and projection-repair behavior.
  - [x] Keep legacy fallback behavior only for recordings that are not yet ready for canonical backfill.

- [x] Checkpoint 5B: Minimal scope-aware transcript editing UX.
  - [x] Add stable utterance-id based speaker patching to the recording detail flow.
  - [x] Add scope selection to the speaker assignment UI.
  - [x] Keep the segment-index endpoint only as a compatibility bridge.
  - [x] Default the user-facing transcript edit flow to canonical scope-aware mutations.
  - [x] Add focused frontend tests for scope selection and stable-id payloads.

Checkpoint 5C: Live voiceprint learning and provenance.

- [x] Checkpoint 5C: Live voiceprint learning and provenance.
  - [x] Extract embeddings from durable chunk audio during live recording rather than waiting for final-only/manual flows.
  - [x] Prefer clean, single-speaker spans and skip ambiguous or low-duration spans.
  - [x] Merge embeddings with drift protection once enough evidence exists.
  - [x] Respect locked global voiceprints unless an explicit user action overrides them.
  - [x] Record confidence, contributing spans, and update source for every voiceprint mutation.

Checkpoint 5D: Evidence-backed name suggestion pipeline.

- [x] Detect deterministic self-introduction patterns and other rule-based name evidence.
- [x] Use calendar attendees and known people as constrained candidate sets.
- [x] If the LLM is used, require strict JSON output with evidence spans and confidence.
- [x] Store results as transcript-level suggestions with provenance instead of directly renaming speakers.
- [x] Keep automated name evidence non-destructive unless the user explicitly approves the suggestion.
- [x] Expose uncertain suggestions to the user for review and confirmation.

Checkpoint 5E: Conflict policy, metrics, and exit-gate validation.

- [x] Prefer manual corrections over automated inference and over weak identity evidence.
- [x] Prefer locked global voiceprints over weak live embeddings.
- [x] Preserve low-confidence name evidence as reversible pending suggestions instead of forcing a destructive rename.
- [x] Audit suggestion generation and resolution alongside voiceprint updates.
- [x] Validate the end-to-end exit gate with live correction, replay, identity, and suggestion fixtures.

Validation matrix:

- [x] Backend endpoint tests prove rename, link, promote, merge, alias persistence, correction events, and projection repair all route through canonical state.
- [x] Transcript API tests prove `utterance_only`, `speaker_everywhere_in_recording`, `from_this_utterance_forward`, and merge scopes remain correct under replay.
- [x] Frontend tests prove the recording page sends stable utterance IDs plus explicit correction scope.
- [x] Worker tests prove incremental voiceprint learning records provenance and respects drift and lock rules.
- [x] Suggestion tests prove inferred names remain reversible, evidence-backed, and non-destructive by default.

Validation evidence:

- [x] Additive migration: `backend/alembic/versions/e5a7c3b1d9f4_add_speaker_name_suggestions_to_transcripts.py` adds transcript-level suggestion persistence.
- [x] Backend regression: `backend/tests/test_infer_speakers_task.py`
- [x] Backend regression: `backend/tests/test_automatic_meeting_intelligence_worker.py`
- [x] Backend regression: `backend/tests/test_canonical_transcript_phase1.py`
- [x] Frontend regression: `frontend/src/lib/api.test.ts`

Dependency chain:

- [x] 5A before 5B because the UI must land on stable canonical mutation semantics instead of legacy segment rewriting.
- [x] 5A before 5C because live identity learning must honor canonical manual-correction and merge provenance.
- [x] 5A before 5D because suggestions should target canonical recording speakers and global people, not legacy projection labels.
- [x] 5C before 5E because conflict policy and metrics need real provenance and confidence data to validate.

Exit gate:

- [x] A live user correction changes future same-speaker live output immediately, voiceprint updates carry provenance and drift protection, and any transcript-derived name suggestion is evidence-backed and reversible.

## Phase 6: Finalization and Post-Meeting Processing

Purpose: turn final processing into promotion and enrichment instead of repeating the whole pipeline.

- [x] Redesign `process_recording_task` around promotion.
  - [x] Verify all uploaded chunks are present or accounted for.
  - [x] Run catch-up ASR only for unprocessed spans.
  - [x] Run catch-up diarization only for unprocessed or low-confidence spans.
  - [x] Promote stable live utterances to final transcript state.
  - [x] Remove provisional markers only after reconciliation succeeds.
- [x] Keep final diarization efficient and targeted.
  - [x] Run full-recording Pyannote only when live diarization failed, confidence is low, or explicit reprocess requests it.
  - [x] Otherwise run reconciliation and quality checks against rolling diarization results.
  - [x] Preserve manual speaker and text edits by stable ID and time overlap.
- [x] Preserve downstream enrichment.
  - [x] Extract or finalize speaker voiceprints.
  - [x] Run deterministic speaker resolution against global people.
  - [x] Run meeting title inference.
  - [x] Run note generation and meeting intelligence.
  - [x] Keep manual notes and Meeting Edge focus available to the final LLM prompts.
- [x] Support imports through the unified pipeline.
  - [x] Treat imports as batch chunk ingestion.
  - [x] Generate utterances and diarization windows through the same storage model.
  - [x] Skip live UI states but preserve the same final transcript behavior.

Implementation status:

- [x] Finalization now widens durable catch-up beyond preexisting live reuse, so any recording with pending durable manifests can reuse span-level ASR work before considering a whole-recording rerun.
- [x] When completed rolling diarization windows already exist for live-covered audio, finalization can promote canonical utterances first and replay those windows against the finalized utterances instead of forcing a new whole-recording Pyannote pass.
- [x] One-shot and chunked imports now bootstrap durable `RecordingAudioChunk` and `RecordingAudioWindowManifest` rows for the assembled import file before worker processing begins.

Exit gate:

- [ ] Finalization is faster for successful live recordings and functionally equivalent or better for imports and failed-live recordings.
  - Automated worker and import coverage now exercises promotion-oriented finalization, durable catch-up reuse without live prerequisites, completed-window replay decisions, and durable import chunk/window bootstrap.
  - Manual full live-recording validation is intentionally deferred until Phase 7 lands the supporting transcript and speaker UX needed to observe and verify the new steady-state behavior end to end.

## Phase 7: Frontend Transcript and Speaker UX

Purpose: make live revisions understandable and give users precise control over speaker identity.

- [x] Move transcript editing from list indices to stable utterance IDs.
  - [x] Update API client methods.
  - [x] Update undo and redo history.
  - [x] Update find and replace.
  - [x] Update export flows.
  - [x] Update playback and trim interactions.
- [x] Display live revision state.
  - [x] Provisional text state.
  - [x] Provisional speaker state.
  - [x] Stable speaker state.
  - [x] Manual speaker override state.
  - [x] Low-confidence speaker state.
  - [x] Recently revised speaker assignment state.
- [x] Add speaker correction workflows.
  - [x] Rename this speaker everywhere.
  - [x] Assign only this utterance.
  - [x] Apply this speaker from now on.
  - [x] Merge speakers.
  - [x] Link to a global speaker.
  - [x] Confirm or reject inferred speaker name suggestions.
- [x] Improve overlapping speech presentation.
  - [x] Show parallel speaker turns without hiding provisional unknown speech.
  - [x] Avoid layout jumps when rolling diarization revises recent turns.
  - [x] Keep text readable on narrow viewports.
- [x] Handle backward-looking updates safely.
  - [x] Preserve scroll position when older utterances change.
  - [x] Highlight revisions briefly without creating noisy UI.
  - [x] Avoid clobbering text currently being edited.
  - [x] Resolve edit conflicts with a clear local-state policy.

## Phase 8: Operational Readiness and Admin Health

Purpose: make pipeline readiness, degraded-mode behavior, and model availability obvious to operators while keeping a single balanced processing profile and avoiding a model-tuning console or duplicate logging surface.

- [x] Add a lightweight Admin Health Dashboard.
  - [x] Confirm configured transcription model presence and worker readiness.
  - [x] Confirm Pyannote model access, download state, and cache readiness.
  - [x] Confirm Hugging Face token presence and validation state when diarization requires it.
  - [x] Confirm device availability and active execution mode, for example GPU-ready or CPU fallback.
  - [x] Confirm worker queue reachability so operators can see whether live and final jobs can run.
  - [x] Confirm FFmpeg availability so missing audio-processing dependencies are surfaced before recordings fail.
- [x] Expose current fallback and degraded-mode state.
  - [x] Show when diarization is unavailable and which fallback behavior is currently in effect.
  - [x] Show when acceleration is unavailable and the pipeline is running in slower CPU mode.
  - [x] Show when optional AI enhancement is disabled separately from core transcription and diarization readiness.
  - [x] Keep degraded-state messaging explicit so failures are diagnosed before or during recording rather than buried in task output.
- [x] Add lightweight processing-health summaries.
  - [x] Show whether core processing is ready, degraded, or blocked.
  - [x] Show whether required models, dependencies, and workers are ready before recording starts.
  - [x] Show active model-download progress when readiness is pending on cached assets.
  - [x] Keep the summary status-oriented and read-only rather than a live metrics console.
- [x] Add targeted readiness checks with concise remediation hints.
  - [x] Distinguish missing credentials, missing model artifacts, unavailable device access, unavailable workers, queue failures, and missing FFmpeg dependencies.
  - [x] Provide a short operator-facing next action for each failed readiness check.
  - [x] Keep the check surface intentionally small and focused on availability, not pipeline tuning.
- [x] Explicitly keep tuning and extra logging out of scope for this phase.
  - [x] Keep a single balanced processing profile rather than adding runtime quality or latency profile controls to the admin UI.
  - [x] Do not expose advanced admin controls for diarization window length, hop size, confidence thresholds, minimum voiceprint duration, forced-emission timing, or default final-diarization behavior.
  - [x] Do not expand raw operational logging beyond the existing container logs and structured pipeline metrics already emitted by the backend.
  - [x] Do not duplicate low-level pipeline metrics in the UI when operator needs are satisfied by health status and existing logs.

Implementation status:

- [x] Administration now exposes a read-only Admin Health Dashboard backed by consolidated backend readiness checks for database, queue, worker, FFmpeg, transcription model cache, diarization prerequisites, device state, and optional AI configuration.
- [x] The dashboard reports overall readiness as ready, degraded, or blocked and surfaces active model-download progress when assets are still being prepared.

Exit gate:

- [x] Operators can open the Admin Health Dashboard and immediately see whether required models, credentials, workers, dependencies, and execution devices are ready for live and final processing.
- [x] Operators can tell when the pipeline is running in a degraded fallback mode and which capability is unavailable.
- [x] The Phase 8 admin surface remains lightweight and status-oriented rather than a tuning console or a replacement for existing logs.

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
