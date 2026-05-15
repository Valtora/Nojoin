# Unified LLM Processing Plan

## Status

- Overall status: In progress
- Last updated: 2026-05-15

## Confirmed Decisions

- [x] Keep Retry Speaker Inference as a speaker-only manual action for now.
- [x] Return meeting notes as Markdown text in v1 for minimal change.
- [x] Use the same unified automatic path for Ollama on day one.
- [x] Remove the automatic per-feature AI toggle matrix instead of supporting mixed permutations.
- [x] Keep Prefer Short Titles as an output-style preference.

## Goal

Collapse the automatic LLM work in recording processing into one provider call that returns:

- speaker suggestions for unresolved diarization labels
- a meeting title
- meeting notes as Markdown

This unified call should run only when an LLM provider is configured. Manual Generate Notes and Retry Speaker Inference remain separate actions.

## Scope

### In scope

- backend LLM request and response contract for automatic meeting intelligence
- provider implementations for Gemini, OpenAI, Anthropic, and Ollama
- worker orchestration changes in the automatic processing pipeline
- removal of automatic AI feature toggles from settings models and UI
- regression coverage for the new automatic path and preserved manual paths
- documentation updates describing the new behavior

### Out of scope

- changing meeting notes storage away from Markdown
- redesigning meeting chat or chat tool-calling behavior
- replacing manual Generate Notes with the unified automatic path
- replacing Retry Speaker Inference with a broader re-run action
- blocking rollout on Ollama quality or compatibility hardening beyond basic path support

## Primary File Targets

- `backend/processing/llm_services.py`
- `backend/worker/tasks.py`
- `backend/utils/meeting_notes.py`
- `backend/utils/config_manager.py`
- `backend/api/v1/endpoints/settings.py`
- `frontend/src/types/index.ts`
- `frontend/src/components/settings/GeneralSettings.tsx`
- `frontend/src/components/RecordingStatusDisplay.tsx`
- `frontend/src/app/setup/page.tsx`
- `docs/AGENTS.md`
- `docs/ARCHITECTURE.md`
- `docs/GETTING_STARTED.md`
- `docs/USAGE.md`
- `docs/PRD.md`

## Work Breakdown

### Phase 0: Contract and sequencing

- [x] Define the automatic unified request contract.
  - [x] Include transcript text rendered from the current post-resolution state.
  - [x] Keep deterministic speaker names resolved before the LLM stage.
  - [x] Keep unresolved placeholder labels visible so the model can rename them.
  - [x] Include user-authored notes as supporting context.
  - [x] Include `prefer_short_titles` as a prompt option.
- [x] Define the automatic unified result contract.
  - [x] `speaker_mapping: { diarization_label: inferred_name }`
  - [x] `title: string`
  - [x] `notes_markdown: string`
- [x] Standardize the response shape.
  - [x] Require a single JSON object in the model response.
  - [x] Add a fenced-JSON extraction fallback for providers that wrap the payload.
  - [x] Validate required keys and value types before applying results.
- [x] Define consistency rules.
  - [x] The model must use the same inferred speaker names in `notes_markdown` that it returns in `speaker_mapping`.
  - [x] The title and notes must be generated from the same unified context.
- [x] Define failure behavior.
  - [x] Unified AI failure must not fail overall recording processing.
  - [x] On unified AI failure, the recording should still finish processing.
  - [x] On unified AI failure, do not apply partial speaker or title changes in v1.
  - [x] On unified AI failure, mark notes generation error using the existing status model.

### Phase 1: Shared backend abstractions

- [x] Add shared data structures for automatic meeting intelligence.
  - [x] Create a typed request container.
  - [x] Create a typed result container.
- [x] Add shared prompt-building helpers.
  - [x] Build one automatic prompt for speaker suggestions, title generation, and notes generation.
  - [x] Reuse the existing user-notes prompt helper.
  - [x] Preserve the user-notes appendix behavior for stored notes.
- [x] Add shared parsing and validation helpers.
  - [x] Parse provider output into the typed result object.
  - [x] Reject malformed JSON or missing required fields.
  - [x] Normalize whitespace and final field values.
- [x] Add a helper to identify which speakers are eligible for LLM renaming.
  - [x] Exclude manually renamed speakers.
  - [x] Exclude merged speakers.
  - [x] Exclude speakers already matched to global voiceprints.
  - [x] Include only unresolved placeholder labels.

### Phase 2: LLM backend implementation

- [ ] Extend `LLMBackend` with a unified automatic method.
  - [ ] Add `generate_meeting_intelligence(...)` to the base interface.
  - [ ] Keep `infer_speakers`, `generate_meeting_notes`, and `infer_meeting_title` for manual and compatibility flows.
- [ ] Implement the unified automatic method for Gemini.
- [ ] Implement the unified automatic method for OpenAI.
- [ ] Implement the unified automatic method for Anthropic.
- [ ] Implement the unified automatic method for Ollama.
- [ ] Ensure all providers share the same result contract.
  - [ ] Same JSON schema.
  - [ ] Same parser and validation path.
  - [ ] Same notes finalization step.
- [ ] Keep unrelated LLM features unchanged.
  - [ ] Meeting chat request flow stays as-is.
  - [ ] Notes editing via tool-calling stays as-is.
  - [ ] Manual Generate Notes stays dedicated to notes-only generation.

### Phase 3: Worker pipeline refactor

- [ ] Extract the automatic AI stage in `process_recording_task` into a dedicated helper.
  - [ ] Make the helper easy to unit test without running the full audio pipeline.
  - [ ] Feed it the final transcript text, unresolved speaker labels, user notes, and config.
- [ ] Remove the three separate automatic LLM round trips from the main processing path.
  - [ ] Remove the automatic standalone speaker inference call.
  - [ ] Remove the automatic standalone title inference call.
  - [ ] Remove the automatic standalone meeting notes call.
- [ ] Insert the unified automatic AI stage after deterministic speaker resolution.
  - [ ] Run it after merge handling, manual-name preservation, and voiceprint matching.
  - [ ] Run it before final title and notes persistence is completed.
- [ ] Apply the unified result.
  - [ ] Update unresolved `RecordingSpeaker` names from `speaker_mapping`.
  - [ ] Update `recording.name` from `title`.
  - [ ] Update `transcript.notes` from `notes_markdown`.
  - [ ] Finalize notes with the existing appended user-notes section behavior.
- [ ] Preserve current status semantics where possible.
  - [ ] Set `transcript.notes_status = "generating"` when unified AI starts.
  - [ ] Preserve `transcript.error_message` handling.
  - [ ] Decide whether to keep existing step names or replace them with a unified step label.
  - [ ] If step text changes, update frontend logic that reads `processing_step` strings.
- [ ] Keep manual tasks separate.
  - [ ] `generate_notes_task` remains notes-only.
  - [ ] `infer_speakers_task` remains speaker-only.

### Phase 4: Settings and UI cleanup

- [ ] Remove automatic feature toggles from backend settings models.
  - [ ] Remove `auto_generate_notes`.
  - [ ] Remove `auto_generate_title`.
  - [ ] Remove `auto_infer_speakers`.
- [ ] Remove automatic feature toggles from default user settings.
- [ ] Keep `prefer_short_titles` in settings.
- [ ] Decide legacy-key handling.
  - [ ] Ignore legacy toggle keys when present in saved user settings.
  - [ ] Optionally strip legacy keys when settings are next saved.
- [ ] Remove the toggle controls from the General settings UI.
- [ ] Remove the toggle fields from frontend settings types.
- [ ] Update product copy to reflect the new gating model.
  - [ ] AI configured means automatic AI enhancement is available.
  - [ ] Missing provider config means automatic AI enhancement is skipped.
  - [ ] Manual Generate Notes and Retry Speaker Inference still exist.
- [ ] Audit setup and status surfaces for stale wording.
  - [ ] Settings copy
  - [ ] setup wizard copy
  - [ ] notes error copy
  - [ ] recording status copy

### Phase 5: Test coverage

- [ ] Add unit tests for the unified response parser.
  - [ ] valid JSON response
  - [ ] fenced JSON response
  - [ ] malformed JSON response
  - [ ] missing field response
- [ ] Add unit tests for unresolved-speaker selection.
  - [ ] manual names excluded
  - [ ] merged speakers excluded
  - [ ] globally identified speakers excluded
  - [ ] placeholder speakers included
- [ ] Add provider-level mocked tests for the unified method where practical.
- [ ] Add helper-level tests for the automatic AI application path.
  - [ ] successful unified response updates speakers, title, and notes
  - [ ] missing config skips the unified stage cleanly
  - [ ] unified AI failure leaves the recording processed and sets notes error
  - [ ] `prefer_short_titles` is included in prompt generation
- [ ] Preserve manual action coverage.
  - [ ] existing Generate Notes task tests still pass
  - [ ] add regression coverage if shared helpers are reused by manual flows
  - [ ] add or update speaker-only inference tests if missing
- [ ] Add full-task coverage only if it remains maintainable.
  - [ ] Prefer extracted helper tests over brittle end-to-end worker tests when possible.

### Phase 6: Documentation and rollout notes

- [ ] Update architecture docs.
  - [ ] `docs/AGENTS.md`
  - [ ] `docs/ARCHITECTURE.md`
- [ ] Update user-facing docs.
  - [ ] `docs/GETTING_STARTED.md`
  - [ ] `docs/USAGE.md`
- [ ] Update product reference docs if wording is now stale.
  - [ ] `docs/PRD.md`
- [ ] Add a manual QA checklist for rollout.
  - [ ] recording with LLM configured
  - [ ] recording with no LLM configured
  - [ ] manual Generate Notes after transcript edits
  - [ ] Retry Speaker Inference still works independently
  - [ ] settings page no longer shows the toggle matrix
  - [ ] Ollama smoke test on the unified automatic path

## Suggested Execution Order

- [ ] Implement Phase 0 before touching worker orchestration.
- [ ] Implement Phase 1 before provider changes.
- [ ] Implement Phase 2 before replacing the worker path.
- [ ] Implement Phase 3 before removing settings toggles.
- [ ] Implement Phase 4 before documentation updates.
- [ ] Finish Phase 5 before closing the feature.
- [ ] Finish Phase 6 before merge.

## Risks and Watchouts

- JSON output may be inconsistent across providers, especially Ollama and smaller local models.
- Notes Markdown embedded inside JSON must be parsed without corrupting newlines or quoting.
- Frontend loading and status behavior currently depends partly on `processing_step` strings.
- If unresolved-speaker selection is wrong, the LLM could overwrite trusted names.
- Legacy saved toggle keys may remain in user settings unless explicitly stripped.
- A single-call design concentrates failure, so error handling must keep the recording usable when the AI stage fails.

## Done Criteria

- [ ] `process_recording_task` performs one automatic LLM call instead of three.
- [ ] Automatic AI runs only when provider configuration is present.
- [ ] Manual Generate Notes remains notes-only.
- [ ] Retry Speaker Inference remains speaker-only.
- [ ] Meeting notes are still stored as Markdown.
- [ ] Automatic AI feature toggles are gone from backend settings and frontend UI.
- [ ] `prefer_short_titles` still works.
- [ ] Docs and tests are updated.