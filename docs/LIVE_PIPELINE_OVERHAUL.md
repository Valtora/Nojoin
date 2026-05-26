# Live Pipeline Reset Tracker

Status: Reset implementation completed for current pass  
Last updated: 2026-05-26

This tracker supersedes the earlier Stage 1-8 speaker-management plan. The
browser/live infrastructure from the earlier rollout remains in place where it
was sound: 0-based sequencing, stereo browser-live audio, split ASR/diarization
window state, persisted live state, and safe final reuse. This reset replaces
the live speaker identity policy that was still causing unstable diarization and
speaker-label churn.

## Approved Architecture

1. Live only emits anonymous session speakers (`LIVE_*` recording speakers).
   Live does not auto-link to global speakers.
2. Live speaker rows stay in one automatic state until the user manually
   renames them or full post-recording speaker inference runs.
3. Live rolling diarization is boundary-first and tail-scoped. It can split the
   recent tail of the transcript using word/timestamp evidence, but it does not
   broadly relabel older live utterances.
4. Live does not learn or update durable voiceprints after speaker creation.
   Session embeddings are matching hints, not an online identity-learning loop.
5. Full processing remains the only durable identity stage. Recording/global
   voiceprints and speaker inference stay in the finalize pipeline.
6. Existing `speaker_state` and `speaker_state_source` payload fields remain for
   compatibility and operator context, but they no longer drive the reset live
   speaker policy.

## What This Reset Changes

- Removes live/global identity claiming in the live speaker resolver.
- Stops ambiguous long live spans from reusing the previous speaker label just
  because it was the latest label.
- Keeps word-level boundary split machinery, but constrains live rolling
  reconciliation to a recent tail window instead of broad overlapping ownership.
- Disables live rolling voiceprint learning so noisy windows cannot poison
  durable identity state before full processing.

## Tracker

### A. Rewrite Tracker

Status: Implemented 2026-05-26

- [x] Replace the old stage tracker with the approved reset plan.
- [x] Document which parts of the earlier overhaul remain valid.
- [x] Update the validation status below after the focused test run.

### B. Live Anonymous Session Speakers

Status: Implemented 2026-05-26

- [x] Keep live matching scoped to recording-local `LIVE_*` speakers.
- [x] Remove live/global auto-linking from `_resolve_live_speaker()`.
- [x] Stop claiming embeddings onto fallback or preferred labels.
- [x] Keep strong same-session matches, but treat soft cross-speaker matches as
  ambiguous instead of flipping labels.
- [x] For longer ambiguous spans in multi-speaker meetings, create a new
  session speaker instead of reusing the previous label.

### C. Boundary-First Tail Reconciliation

Status: Implemented 2026-05-26

- [x] Add a boundary-only mode to
  `reconcile_diarization_window_result()`.
- [x] Restrict live rolling reconciliation to the recent tail of each window.
- [x] Keep word/timestamp split helpers as the main correction mechanism for
  late speaker changes.
- [x] Stop live rolling windows from broadly reassigning speaker ownership
  across the whole overlap span.

### D. Remove Live Identity Learning

Status: Implemented 2026-05-26

- [x] Stop live rolling voiceprint learning for recording speakers.
- [x] Stop live rolling voiceprint learning for global speakers.
- [x] Preserve full processing as the only stage that builds durable
  voiceprints or inferred identities.

### E. Final Processing Handoff

Status: Verified against existing finalize pipeline 2026-05-26

- [x] Confirm `backend/worker/tasks.py` still owns voiceprint extraction during
  full processing.
- [x] Confirm the independent post-recording speaker inference task still
  exists.
- [x] Keep live speaker rows compatible with later finalize-time inference and
  manual rename flows.

### F. Validation

Status: Validated 2026-05-26

- [x] Add resolver regression coverage for ambiguous soft/weak live matches.
- [x] Add canonical regression coverage for boundary-only tail reconcile mode.
- [x] Run focused backend validation for the reset slices.
- [x] Update this section with the actual command results.

Validation run:

- [x] `PYTHONPATH=/home/msadmin/Nojoin-dev .venv/bin/pytest backend/tests/test_live_transcription.py -k "resolve_live_speaker_reuses_fallback_without_embedding or resolve_live_speaker_soft_matches_existing_label_without_updating_embedding or resolve_live_speaker_weak_match_creates_new_speaker or soft_cross_speaker_match_creates_new_session_speaker or long_ambiguous_multi_speaker_match_avoids_last_label"` (`5 passed`)
- [x] `PYTHONPATH=/home/msadmin/Nojoin-dev .venv/bin/pytest backend/tests/test_canonical_transcript_phase1.py -k "boundary_only_mode_keeps_existing_speaker or keeps_stable_live_speaker_without_repeated_conflicting_windows"` (`2 passed`)
- [x] `PYTHONPATH=/home/msadmin/Nojoin-dev .venv/bin/pytest backend/tests/test_live_transcription.py backend/tests/test_canonical_transcript_phase1.py` (`145 passed`)

## Notes

- This reset intentionally prefers temporary over-segmentation of anonymous live
  speakers to incorrect durable identity learning during the meeting.
- Final post-recording processing remains the place where speaker identity can
  be merged, inferred, and polished with more context.
