# ADR-0005: Versioned voiceprint extraction, and an optional per-recording speaker cap

- **Status:** Accepted
- **Date:** 2026-07-25
- **Deciders:** Valtora

## Context

A user reported a 96-minute meeting with two confirmed participants diarised as four speakers ([#128](https://github.com/Valtora/Nojoin/issues/128)). Nojoin already runs an embedding-based merge pass after diarisation that should have collapsed this, so the first question was why it did not.

Investigation ruled out the obvious explanation. `process_recording_task` is the only entry point into the final pipeline, all dispatch sites reach it, and the merge pass runs unconditionally inside `_assign_and_identify_speakers`. Import and live capture converge long before diarisation.

The real problem was in the voiceprint itself, and it was measurable. Across this project's own library — 12 people linked to speakers across dozens of meetings, 2904 same-person pairs — **29% of same-person voiceprint pairs scored below the 0.70 merge threshold**, and 37% fell below the 0.75 identification threshold. The median was a healthy 0.803, but the tenth percentile was 0.363. Different-people pairs were clean by comparison: median 0.073, with 1.2% above 0.70. The embedding space separates people perfectly well; same-person similarity was collapsing whenever acoustic conditions changed — which is precisely the condition that makes the diariser split a person in the first place. The safety net was weakest exactly where it was needed.

Three defects in `extract_embeddings` accounted for it:

1. **`Inference(window="sliding")`.** `wespeaker-voxceleb-resnet34-LM` declares `duration = 5.0` and `resolution = CHUNK`, so the model was being shown 5-second sub-windows in 0.5-second hops and its raw outputs mean-averaged. A speaker embedding model is built to pool over a whole utterance; `window="whole"` is the documented way to ask it to.
2. **Averaging un-normalised vectors.** wespeaker outputs vary in magnitude, so the loudest crops dominated the mean.
3. **No overlap exclusion.** The diarisation pipeline sets `embedding_exclude_overlap: true` for this exact reason; Nojoin's extractor took the ten longest turns with crosstalk included.

Two further defects surfaced while fixing the above. Survivor selection in the merge pass ranked by utterance count, but utterance rows are not written until *after* the pass runs on an imported recording — so every count was zero, the remaining tiebreak was identical for all candidates, and the survivor was effectively whichever row the database returned first. A two-minute fragment could beat a fifty-minute cluster and take its `name` and `global_speaker_id` with it. Separately, the pass emitted a log line only for pairs it *merged*: a run that scored every pair below threshold produced no output at all, and was indistinguishable from a run that had no voiceprints to score or never executed.

## Decision

**Voiceprint extraction is fixed and the method is versioned.** Extraction now uses `window="whole"` over overlap-extruded crops of at most 30 seconds, unit-normalises each crop embedding before averaging, and unit-normalises the result. `EMBEDDING_METHOD_VERSION` records how a stored vector was produced, and is persisted on `global_speakers.embedding_version` and `recording_speakers.embedding_version`.

**Embeddings of different versions are never compared.** A cosine score between vectors from two extraction methods looks like a similarity but is not one. `find_matching_global_speaker` skips candidates of a different version, and the merge pass scores only within the largest single-version group. The alternative — comparing anyway and hoping the drift is small — would silently mismatch people with no way to detect it.

**Stale voiceprints are repaired by re-extraction, not by tolerance.** `rebuild_voiceprints_task` re-derives them from the recordings' audio and rebuilds each affected person from their current-version speakers. It is operator-triggered, not automatic on upgrade, because it runs the embedding model over the entire library on hardware the user owns. `GET /speakers/voiceprints/method-status` reports how many are stale so the state is visible rather than silent.

**The merge pass reports every run.** A `speaker_merge_pass` metric is emitted for every execution — including runs that merge nothing and runs that cannot score anything, which carry an explicit reason — carrying each pair's cosine score. A companion `final_diarization_speaker_stats` metric records how much speech each cluster holds, which is what distinguishes a negligible fragment from a substantial mis-split.

**Identification outranks acoustic similarity.** Two speakers already resolved to different people, or manually given different names, are never merged however they score, and survivor selection prefers an identified speaker over an anonymous one and ranks by diarised speech duration before utterance count.

**The speaker cap is applied as `max_speakers`, never `num_speakers`.** `Recording.max_speakers` is nullable; `NULL` means auto-detect and passes *no* speaker keyword to pyannote at all, leaving that path byte-identical. The cap is settable at import, and on a live recording it stays editable for the whole capture because diarisation runs at stop time — which is what makes a late joiner survivable.

## Consequences

Same-person similarity improves and, more importantly, different-person similarity falls: on a verification recording the worst false-positive pair moved from 0.584 to 0.146, and the margin between the lowest same-speaker and highest different-speaker score roughly doubled. `DUPLICATE_SPEAKER_MERGE_THRESHOLD` is deliberately left at 0.70; the input to the comparison was wrong, not the threshold, and a lower threshold causes wrong merges, which are harder for a user to undo than wrong splits.

Accepted trade-offs:

- **Existing voiceprints stop matching until rebuilt.** This is the cost of refusing to compare across versions, and it is the honest failure mode: no automatic match, rather than a wrong one. `method-status` makes it visible and `rebuild` fixes it.
- **A rebuild cannot recover voiceprints whose audio is gone.** Those rows stay stale and unmatchable; the person can still be linked manually.
- **A binding cap is blunt.** pyannote does not gently merge surplus clusters — `VBxClustering` discards its result and re-partitions with k-means at the requested count. Better than four-when-there-are-two, but not a substitute for the extraction fix, which is why both ship together.
- **Changing extraction again means another version bump and another rebuild.** That is the intended cost; it is what stops a silent drift.

Obligations this creates on contributors:

- Any change to how embeddings are produced **must** bump `EMBEDDING_METHOD_VERSION`. Leaving it alone makes old and new vectors falsely comparable.
- Any new comparison site must check the version. `embeddings_are_comparable` exists for this.
- Adding a column to `recordings`, `recording_speakers`, or `global_speakers` means updating every hand-written `CREATE TABLE` in `backend/tests/`; only a full `pytest` run catches a miss.

## Follow-ups

Re-running diarisation without re-running transcription is desirable and out of scope here; reprocessing currently redoes both. Tracked separately from [#128](https://github.com/Valtora/Nojoin/issues/128).
