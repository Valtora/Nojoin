# Screen study: pre-registration

Issue #653. This file fixes the hypotheses, measures, pass rules and the split between calibration and held-out meetings before any real frame is analysed. Its SHA-256 is posted on #653 first, and `run.py` refuses to run unless the file it is given matches the posted hash. A change to anything here after that is a new pre-registration with a new hash, posted before it is used.

## Question

When a user shares the screen that shows their meeting, does the meeting app's active-speaker ring help Nojoin's speaker labels? The ring is read from frames alone, with no model and no reading of names.

## Meetings

- 12 meetings, 4 each on Teams, Zoom and Meet, recorded with Screen study on, each with at least 3 remote speakers, at least 20 minutes and gallery view.
- Each is reviewed in full: every speaker error corrected, one-word replies included. The reviewed speaker of a line is its ground truth.
- Each is then tagged `study-teams`, `study-zoom` or `study-meet`. The tag is the meeting's platform.
- On each platform the first meeting by creation time is the calibration meeting. The other three are held out and scored once.

## Data

- From the frames: each stored frame and its index row, as the sampler writes them.
- From the database, by `export_timeline.sql`: each line's start and end, its diariser cluster, the pipeline's speaker at its latest finalize, and its reviewed speaker (the People id, or the meeting speaker's id when not linked), plus each audio chunk's start. No text, names or titles.

## Clock

- A frame's time is its audio chunk's `absolute_start_ms` plus its `offset_ms`, the transcript's clock.
- A frame's state holds until the next frame when that frame's `lost_before` is 0. Otherwise, or when the field is missing, it holds for one tick (1 s) and the rest of the gap is unobserved.
- Everything is laid on a 100 ms grid. Speech time is the bins a reviewed line covers. A bin with two reviewed speakers counts as speech for the precondition and is left out of every measure that needs one speaker.

## Detector

- The detector in `detect.py` at the commit whose files hash to the summary's `code_sha256`. It finds rings, groups them into slots seen in at least 3 frames, splits the meeting into layouts, and reads which slot is lit in every frame.
- Gate: before reading a real frame, it must be at least 99% correct per frame on the held-out synthetic meetings (`evaluate.py`, 10 meetings per platform of 20 minutes each from seed 3700000, at 1920 px on the long edge as the sampler stores them). `run.py` runs the gate first and stops if it fails.
- A tile is a slot within a layout. A layout that returns keeps its number.
- Tiles are on screen at a frame when a ring was seen in the same layout within 30 s of it.

## Lag

- Each platform's lag is the shift, from -1 s to +3 s in 100 ms steps, that maximises agreement on its calibration meeting: the share of one-speaker speech bins whose single lit, on-screen tile is the tile most often lit for that speaker. A tie goes to the lag nearest zero.
- The held-out meetings use their platform's lag and nothing else from calibration.

## Thresholds used throughout

- A speaker, pipeline speaker or tile counts once it holds 3 s (30 bins).
- A short reply is a line of 1.5 s or less, with one reviewed speaker, a pipeline speaker and overlap rank 0.

## Hypotheses and pass rules

All rules are scored on the held-out meetings, pooled unless a rule says otherwise. A rule with fewer cases than its minimum is inconclusive, which is not a pass.

### Precondition

Tiles are on screen for at least 50% of speech time. If not, nothing else is scored.

### H1. Speaker count

- Tile count: per layout, the tiles lit alone and on screen for at least 3 s; the meeting's count is the layout with most.
- True count: reviewed speakers with at least 3 s of one-speaker speech. Pipeline count: pipeline speakers with at least 3 s.
- Pass when the tile count is at least the true count in at least 95% of meetings, and, of the meetings where the pipeline counted more speakers than the truth, the tile count is below the pipeline's in at least half. That second part needs at least 3 such meetings.

### H2. Matching speakers to tiles

- Each tile stands for the reviewed speaker it is lit for most during speech, if it is lit for at least 3 s of speech.
- Matched speech: one-speaker bins with a single lit, on-screen tile that stands for someone. Accuracy: matched bins where that someone is the reviewed speaker, over matched bins.
- Pass when accuracy is at least 95% and matched speech is at least 80% of one-speaker speech, on at least 2 platforms, each platform's held-out meetings pooled.

### H3. Merge hints

- A pipeline speaker's dominant tile in a layout is the tile it is lit for most there, when that is at least 3 s and at least 60% of its lit bins in that layout. Two pipeline speakers with the same dominant tile in a layout are a merge hint.
- Truth: each pipeline speaker with at least 3 s belongs to the reviewed person it holds most. Two that belong to the same person are a wrong split; the merges needed are, per person, the number of their pipeline speakers less one.
- Pass when hints are at least 90% precise and the right hints fix at least half the merges needed. Needs at least 5 merges needed.

### H4. Split hints

- A pipeline speaker is hinted to hold two people when, in some layout, two tiles are each lit for at least 3 s and at least 20% of its lit bins there.
- Truth: a pipeline speaker holds two people when its second reviewed person has at least 3 s and at least 20% of its speech.
- Pass when hints are at least 80% precise and find at least half of the pipeline speakers that hold two people. Needs at least 5 such speakers.

### H5. Short replies

- Each tile stands for the pipeline speaker it is lit for most during lines longer than 1.5 s, if that is at least 3 s.
- A short reply is moved to a tile's pipeline speaker when one tile is lit, on screen, for at least half of the reply's bins and stands for a different speaker.
- A reply's pipeline label is wrong when its pipeline speaker belongs to a different person than the reviewed one. A move fixes a wrong label when the new speaker belongs to the reviewed person, and breaks a right one when it does not.
- Pass when moves fix at least 30% of wrong labels and break at most 2% of right ones. Needs at least 50 wrong labels.

## Output

`summary.json` only: these rules and their numbers, each platform's lag and ring colour as numbers, and per meeting its counts and shares, keyed by recording id. No names, text, frames or crops. The analysis refuses any other write, and the summary is checked to hold no string but ids, platforms, result words and hashes.
